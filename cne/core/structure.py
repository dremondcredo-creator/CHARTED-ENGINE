"""Structure engine: break vs fade, retest, confirmation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cne.collectors import prices
from cne.core import levels as lv
from cne.core import market as market_mod

DEFAULT_PHASE = "T+30M"
RETEST_WINDOW_HOURS = 2
BREAK_MIN_ATR_FRAC = 0.10
RETEST_TOL_ATR_FRAC = 0.20
SWING_STRENGTH = 3


def _parse(s):
    if not s: return None
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _atr_upto(df, ts, n=14):
    sub = df[df.index <= ts]
    if len(sub) < n + 1: return None
    h, l, c = sub["High"].values, sub["Low"].values, sub["Close"].values
    trs = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
           for i in range(1, len(sub))]
    return sum(trs[-n:]) / len(trs[-n:])


def _classify_structure(df, up_to_ts, strength=SWING_STRENGTH):
    sub = df[df.index <= up_to_ts]
    if len(sub) < 2 * strength + 1:
        return "RANGE"
    highs, lows = lv.find_pivots(sub, strength=strength)
    if len(highs) < 2 or len(lows) < 2:
        return "RANGE"
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]
    if h2 > h1 and l2 > l1: return "BULLISH"
    if h2 < h1 and l2 < l1: return "BEARISH"
    return "RANGE"


def _retest_held(df, level_price, after_ts, kind):
    end = after_ts + timedelta(hours=RETEST_WINDOW_HOURS)
    sub = df[(df.index > after_ts) & (df.index <= end)]
    if sub.empty: return None
    atr = _atr_upto(df, after_ts) or 0
    tol = atr * RETEST_TOL_ATR_FRAC if atr else 0

    if kind == "RESISTANCE":
        touched = (sub["Low"] - level_price).abs() <= tol
        if not touched.any(): return None
        return int(bool((sub.loc[touched, "Close"] > level_price).any()))
    else:
        touched = (sub["High"] - level_price).abs() <= tol
        if not touched.any(): return None
        return int(bool((sub.loc[touched, "Close"] < level_price).any()))


def _combine(mkt, event_class, before, after, held):
    m_score = mkt["score"] if mkt else 0.0
    s_bonus = 0.0
    bits = []

    if event_class == "BREAK_UP":
        s_bonus = 1.5 if held == 1 else (1.0 if held == 0 else 0.5)
        bits.append("broke up" + (" + held retest" if held == 1 else ""))
    elif event_class == "BREAK_DOWN":
        s_bonus = -1.5 if held == 1 else (-1.0 if held == 0 else -0.5)
        bits.append("broke down" + (" + held retest" if held == 1 else ""))
    elif event_class == "FADE_UP":
        s_bonus = -1.0
        bits.append("faded resistance")
    elif event_class == "FADE_DOWN":
        s_bonus = 1.0
        bits.append("faded support")
    else:
        bits.append("no level test")

    if before != "RANGE" and before == after:
        s_bonus += 1.0 if after == "BULLISH" else -1.0
        bits.append(f"structure {after.lower()} preserved")

    total = m_score + s_bonus
    if total >= 3.0:   conf = "A_PLUS"
    elif total >= 1.5: conf = "CONFIRMS"
    elif total <= -1.5: conf = "CONTRADICTS"
    else:              conf = "NEUTRAL"
    return conf, total, "; ".join(bits)


def classify_release(conn, release_id, symbol):
    rel = conn.execute("""
        SELECT id, event_code, period, release_time_utc
        FROM releases WHERE id = ?
    """, (release_id,)).fetchone()
    if not rel or not rel["release_time_utc"]: return None

    snaps = {}
    for r in conn.execute("""
        SELECT phase, price, bar_ts_utc FROM price_snapshots
        WHERE release_id=? AND symbol=?
    """, (release_id, symbol)).fetchall():
        snaps[r["phase"]] = r["price"]

    pre, t0, t30 = snaps.get("PRE"), snaps.get("T0"), snaps.get("T+30M")
    if not (pre and t30): return None

    release_ts = _parse(rel["release_time_utc"])
    if not release_ts: return None

    df = prices.fetch_ohlc(symbol, period="10d", interval="5m")
    if df is None or df.empty: return None

    atr = _atr_upto(df, release_ts)
    pre_df = df[df.index <= release_ts]
    highs, lows = lv.find_pivots(pre_df, strength=SWING_STRENGTH)
    tol = atr * 0.35 if atr else 0
    res = lv.cluster_levels(highs, tol)
    sup = lv.cluster_levels(lows, tol)
    levels = [{"kind": "RESISTANCE", "center": c["center"], "touches": c["touches"]} for c in res]
    levels += [{"kind": "SUPPORT", "center": c["center"], "touches": c["touches"]} for c in sup]

    above, below = lv.nearest_levels_above_below(levels, pre)
    before = _classify_structure(df, release_ts)
    after = _classify_structure(df, release_ts + timedelta(minutes=30))

    event_class, break_pct, nearest = "NO_TEST", 0.0, None
    t0_price = t0 if t0 else pre

    if above:
        spiked = t0_price >= (above["center"] - (atr * 0.05 if atr else 0))
        closed = t30 > above["center"] + (atr * BREAK_MIN_ATR_FRAC if atr else 0)
        if spiked and closed:
            event_class = "BREAK_UP"
            break_pct = (t30 - above["center"]) / above["center"] * 100
            nearest = above
        elif spiked and t0_price > above["center"] and t30 < above["center"]:
            event_class = "FADE_UP"
            break_pct = (t30 - above["center"]) / above["center"] * 100
            nearest = above

    if event_class == "NO_TEST" and below:
        spiked = t0_price <= (below["center"] + (atr * 0.05 if atr else 0))
        closed = t30 < below["center"] - (atr * BREAK_MIN_ATR_FRAC if atr else 0)
        if spiked and closed:
            event_class = "BREAK_DOWN"
            break_pct = (t30 - below["center"]) / below["center"] * 100
            nearest = below
        elif spiked and t0_price < below["center"] and t30 > below["center"]:
            event_class = "FADE_DOWN"
            break_pct = (t30 - below["center"]) / below["center"] * 100
            nearest = below

    held = None
    if nearest and event_class in ("BREAK_UP", "BREAK_DOWN"):
        held = _retest_held(df, nearest["center"],
                            release_ts + timedelta(minutes=30),
                            nearest["kind"])

    mkt = market_mod.run_for_release(conn, release_id)
    conf, score, notes = _combine(mkt, event_class, before, after, held)

    return {
        "release_id": release_id, "symbol": symbol, "phase": DEFAULT_PHASE,
        "nearest_level": nearest["center"] if nearest else None,
        "nearest_kind": nearest["kind"] if nearest else None,
        "distance_atr": round(abs(pre - nearest["center"]) / atr, 3) if (nearest and atr) else None,
        "structure_before": before, "structure_after": after,
        "event_class": event_class,
        "break_pct": round(break_pct, 4) if nearest else None,
        "held_retest": held,
        "confirmation": conf, "confirmation_score": round(score, 2),
        "notes": notes,
    }


def persist(conn, result):
    if not result: return
    conn.execute("""
        INSERT INTO structure_verdicts
          (release_id, symbol, phase, nearest_level, nearest_kind, distance_atr,
           structure_before, structure_after, event_class, break_pct,
           held_retest, confirmation, confirmation_score, notes, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id, symbol) DO UPDATE SET
          symbol=excluded.symbol, phase=excluded.phase,
          nearest_level=excluded.nearest_level, nearest_kind=excluded.nearest_kind,
          distance_atr=excluded.distance_atr,
          structure_before=excluded.structure_before,
          structure_after=excluded.structure_after,
          event_class=excluded.event_class, break_pct=excluded.break_pct,
          held_retest=excluded.held_retest,
          confirmation=excluded.confirmation,
          confirmation_score=excluded.confirmation_score,
          notes=excluded.notes, computed_utc=excluded.computed_utc
    """, (result["release_id"], result["symbol"], result["phase"],
          result["nearest_level"], result["nearest_kind"], result["distance_atr"],
          result["structure_before"], result["structure_after"],
          result["event_class"], result["break_pct"], result["held_retest"],
          result["confirmation"], result["confirmation_score"],
          result["notes"], datetime.now(timezone.utc).isoformat()))
    conn.commit()


def classify_all(conn, symbol="GOLD"):
    rows = conn.execute("""
        SELECT DISTINCT release_id FROM price_snapshots WHERE symbol = ?
    """, (symbol,)).fetchall()
    n = 0
    for r in rows:
        res = classify_release(conn, r["release_id"], symbol)
        if res:
            persist(conn, res)
            n += 1
    return n