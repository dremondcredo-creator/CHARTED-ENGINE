"""Market verdict: does the price reaction agree with the fundamental?"""
from __future__ import annotations

from datetime import datetime, timezone

DEFAULT_PHASE = "T+30M"
USD_FLAT_PCT = 0.05
YIELD_FLAT_BP = 2.0
GOLD_FLAT_PCT = 0.15
EURUSD_FLAT_PCT = 0.05


def _dir_from_pct(v, flat_thr):
    if v is None: return "UNKNOWN"
    if v > flat_thr: return "RISING"
    if v < -flat_thr: return "FALLING"
    return "FLAT"


def _dir_from_signed_pct(v, flat_thr):
    if v is None: return "UNKNOWN"
    if v > flat_thr: return "UP"
    if v < -flat_thr: return "DOWN"
    return "FLAT"


def _snapshot(conn, release_id, phase, symbol):
    r = conn.execute("""
        SELECT price, change_pct, change_abs
        FROM price_snapshots
        WHERE release_id=? AND phase=? AND symbol=?
    """, (release_id, phase, symbol)).fetchone()
    return dict(r) if r else None


def _yield_bp(conn, release_id, phase):
    changes = []
    for sym in ("US2Y", "US10Y"):
        s = _snapshot(conn, release_id, phase, sym)
        if s and s["change_abs"] is not None:
            changes.append(s["change_abs"] * 100.0)
    return sum(changes) / len(changes) if changes else None


def compute_verdict(conn, release_id, phase=DEFAULT_PHASE):
    usd = _snapshot(conn, release_id, phase, "DXY")
    gold = _snapshot(conn, release_id, phase, "GOLD")
    eurusd = _snapshot(conn, release_id, phase, "EURUSD")
    yb = _yield_bp(conn, release_id, phase)

    if not usd and not gold and not eurusd:
        return None

    usd_chg = usd["change_pct"] if usd else None
    gold_chg = gold["change_pct"] if gold else None
    eu_chg = eurusd["change_pct"] if eurusd else None

    return {
        "phase": phase,
        "usd_dir": _dir_from_pct(usd_chg, USD_FLAT_PCT),
        "usd_change_pct": round(usd_chg, 4) if usd_chg is not None else None,
        "yield_dir": _dir_from_pct(yb, YIELD_FLAT_BP) if yb is not None else "UNKNOWN",
        "yield_change_bp": round(yb, 2) if yb is not None else None,
        "gold_dir": _dir_from_signed_pct(gold_chg, GOLD_FLAT_PCT),
        "gold_change_pct": round(gold_chg, 4) if gold_chg is not None else None,
        "eurusd_dir": _dir_from_signed_pct(eu_chg, EURUSD_FLAT_PCT),
        "eurusd_change_pct": round(eu_chg, 4) if eu_chg is not None else None,
    }


def align_with_fundamentals(verdict, fundamental_impact):
    if fundamental_impact == "NEUTRAL" or verdict is None:
        return "NEUTRAL", 0.0, "no clear fundamental USD signal"

    if fundamental_impact == "BULLISH":
        want_usd, want_y = "RISING", "RISING"
    else:
        want_usd, want_y = "FALLING", "FALLING"

    usd_ok = verdict["usd_dir"] == want_usd
    y_ok = verdict["yield_dir"] == want_y

    if "UNKNOWN" in (verdict["usd_dir"], verdict["yield_dir"]):
        if usd_ok:
            return "CONFIRMS", 1.0, "USD aligns; yield confirmation is unavailable"
        if y_ok:
            return "CONFIRMS", 0.5, "yields align; USD confirmation is unavailable"
        return "INSUFFICIENT_DATA", 0.0, "USD or yield observations are unavailable"

    if usd_ok and y_ok:
        return "CONFIRMS", 2.0, f"USD {want_usd.lower()}, yields {want_y.lower()} — full alignment"
    if usd_ok:
        return "CONFIRMS", 1.0, f"USD {want_usd.lower()} but yields {verdict['yield_dir'].lower()} — partial"
    if y_ok:
        return "CONFIRMS", 0.5, f"yields {want_y.lower()} but USD {verdict['usd_dir'].lower()} — partial"
    if verdict["usd_dir"] == "FLAT" and verdict["yield_dir"] == "FLAT":
        return "NEUTRAL", 0.0, "USD and yields flat — market unmoved"
    return "CONTRADICTS", -2.0, f"USD {verdict['usd_dir'].lower()}, yields {verdict['yield_dir'].lower()} — market disagrees"


def _light(score):
    if score >= 1.5: return "🟢"
    if score <= -1.5: return "🔴"
    return "🟡"


def persist_verdict(conn, release_id, verdict, alignment, score, notes, fund):
    if verdict is None:
        return
    conn.execute("""
        INSERT INTO market_verdicts
          (release_id, usd_dir, usd_change_pct, yield_dir, yield_change_bp,
           gold_dir, gold_change_pct, eurusd_dir, eurusd_change_pct,
           fundamental_dir, market_alignment, market_score, market_light,
           notes, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id) DO UPDATE SET
          usd_dir=excluded.usd_dir, usd_change_pct=excluded.usd_change_pct,
          yield_dir=excluded.yield_dir, yield_change_bp=excluded.yield_change_bp,
          gold_dir=excluded.gold_dir, gold_change_pct=excluded.gold_change_pct,
          eurusd_dir=excluded.eurusd_dir, eurusd_change_pct=excluded.eurusd_change_pct,
          fundamental_dir=excluded.fundamental_dir,
          market_alignment=excluded.market_alignment,
          market_score=excluded.market_score, market_light=excluded.market_light,
          notes=excluded.notes, computed_utc=excluded.computed_utc
    """, (release_id,
          verdict["usd_dir"], verdict["usd_change_pct"],
          verdict["yield_dir"], verdict["yield_change_bp"],
          verdict["gold_dir"], verdict["gold_change_pct"],
          verdict["eurusd_dir"], verdict["eurusd_change_pct"],
          fund, alignment, score, _light(score), notes,
          datetime.now(timezone.utc).isoformat()))
    conn.commit()


def run_for_release(conn, release_id, phase=DEFAULT_PHASE):
    row = conn.execute("""
        SELECT r.event_code, r.period, s.impact
        FROM releases r LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.id = ?
    """, (release_id,)).fetchone()
    if not row or not row["impact"]:
        return None
    v = compute_verdict(conn, release_id, phase)
    if v is None:
        return None
    alignment, score, notes = align_with_fundamentals(v, row["impact"])
    persist_verdict(conn, release_id, v, alignment, score, notes, row["impact"])
    return {"release_id": release_id, "event_code": row["event_code"],
            "period": row["period"], "verdict": v,
            "alignment": alignment, "score": score, "notes": notes}


def run_all(conn, phase=DEFAULT_PHASE):
    rows = conn.execute("""
        SELECT DISTINCT p.release_id FROM price_snapshots p
        JOIN releases r ON r.id = p.release_id
        JOIN surprises s ON s.release_id = r.id
        WHERE p.phase = ?
    """, (phase,)).fetchall()
    n = 0
    for r in rows:
        if run_for_release(conn, r["release_id"], phase):
            n += 1
    return n