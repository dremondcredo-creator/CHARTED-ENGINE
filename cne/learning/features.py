"""Feature vectors + hierarchical keys."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def build(conn, release_id):
    r = conn.execute("""
        SELECT r.event_code, e.tier, e.category,
               s.direction AS sdir, s.magnitude AS smag
        FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.id = ?
    """, (release_id,)).fetchone()
    if not r: return None

    mv = conn.execute("""
        SELECT market_alignment, market_score FROM market_verdicts
        WHERE release_id = ?
    """, (release_id,)).fetchone()
    sv = conn.execute("""
        SELECT event_class, held_retest, confirmation
        FROM structure_verdicts WHERE release_id = ? AND symbol='GOLD'
    """, (release_id,)).fetchone()

    release_ts = conn.execute("SELECT release_time_utc FROM releases WHERE id = ?",
                              (release_id,)).fetchone()["release_time_utc"]
    macro = conn.execute("""
        SELECT bias_label FROM macro_snapshots
        WHERE country='US' AND ts_utc <= ?
        ORDER BY ts_utc DESC LIMIT 1
    """, (release_ts or "9999",)).fetchone()
    chop = conn.execute("""
        SELECT chop_label FROM chop_history
        WHERE symbol='GOLD' AND ts_utc <= ?
        ORDER BY ts_utc DESC LIMIT 1
    """, (release_ts or "9999",)).fetchone()

    return {"release_id": release_id, "event_code": r["event_code"],
            "tier": r["tier"], "category": r["category"],
            "surprise_direction": r["sdir"], "surprise_magnitude": r["smag"],
            "market_alignment": mv["market_alignment"] if mv else None,
            "market_score": mv["market_score"] if mv else None,
            "structure_event": sv["event_class"] if sv else None,
            "held_retest": sv["held_retest"] if sv else None,
            "structure_confirmation": sv["confirmation"] if sv else None,
            "macro_label": macro["bias_label"] if macro else None,
            "chop_state": chop["chop_label"] if chop else None}


def persist(conn, f):
    if not f: return
    conn.execute("""
        INSERT INTO setup_features
          (release_id, event_code, tier, category,
           surprise_direction, surprise_magnitude,
           market_alignment, market_score,
           structure_event, held_retest, structure_confirmation,
           macro_label, chop_state, computed_utc)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id) DO UPDATE SET
          event_code=excluded.event_code, tier=excluded.tier,
          category=excluded.category,
          surprise_direction=excluded.surprise_direction,
          surprise_magnitude=excluded.surprise_magnitude,
          market_alignment=excluded.market_alignment,
          market_score=excluded.market_score,
          structure_event=excluded.structure_event,
          held_retest=excluded.held_retest,
          structure_confirmation=excluded.structure_confirmation,
          macro_label=excluded.macro_label, chop_state=excluded.chop_state,
          computed_utc=excluded.computed_utc
    """, (f["release_id"], f["event_code"], f["tier"], f["category"],
          f["surprise_direction"], f["surprise_magnitude"],
          f["market_alignment"], f["market_score"],
          f["structure_event"], f["held_retest"],
          f["structure_confirmation"], f["macro_label"],
          f["chop_state"], datetime.now(timezone.utc).isoformat()))
    conn.commit()


def feature_keys(f):
    ev = f.get("event_code") or "any"
    mag = f.get("surprise_magnitude") or "any"
    dirn = f.get("surprise_direction") or "any"
    mkt = f.get("market_alignment") or "any"
    strc = f.get("structure_event") or "any"
    retest = f.get("held_retest")
    ret = "held" if retest == 1 else ("failed" if retest == 0 else "na")

    return [
        ("L1", f"ev={ev}|mag={mag}|dir={dirn}|mkt={mkt}|str={strc}|retest={ret}"),
        ("L2", f"ev={ev}|mag={mag}|dir={dirn}|mkt={mkt}|str={strc}"),
        ("L3", f"ev={ev}|mag={mag}|dir={dirn}|mkt={mkt}"),
        ("L4", f"ev={ev}|mag={mag}|dir={dirn}"),
        ("L5", f"ev={ev}"),
    ]


def hash_key(key):
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def refresh_all(conn, days_back=120):
    rows = conn.execute("""
        SELECT DISTINCT r.id FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN market_verdicts m ON m.release_id = r.id
        ORDER BY r.release_time_utc DESC LIMIT 500
    """).fetchall()
    n = 0
    for r in rows:
        f = build(conn, r["id"])
        if f:
            persist(conn, f)
            n += 1
    return n