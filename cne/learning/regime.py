"""Macro regime detection from macro_snapshots."""
from __future__ import annotations

from datetime import datetime, timezone


def _coarse(label):
    if not label: return "NEUTRAL"
    if "HAWKISH" in label: return "HAWKISH"
    if "DOVISH" in label: return "DOVISH"
    return "NEUTRAL"


def detect_regimes(conn, country="US"):
    rows = conn.execute("""
        SELECT ts_utc, total_score, bias_label FROM macro_snapshots
        WHERE country=? ORDER BY ts_utc ASC
    """, (country,)).fetchall()
    if not rows: return 0

    conn.execute("DELETE FROM macro_regimes WHERE country=?", (country,))
    now = datetime.now(timezone.utc).isoformat()
    regimes = []
    cur_dir = None
    cur_start = None
    cur_n = 0
    pending_dir = None
    pending_n = 0

    for r in rows:
        d = _coarse(r["bias_label"])
        if cur_dir is None:
            cur_dir, cur_start, cur_n = d, r["ts_utc"], 1
            continue
        if d == cur_dir:
            cur_n += 1
            pending_dir, pending_n = None, 0
            continue
        if pending_dir == d:
            pending_n += 1
        else:
            pending_dir, pending_n = d, 1
        if pending_n >= 2:
            regimes.append({"label": cur_dir, "start": cur_start,
                            "end": r["ts_utc"], "n": cur_n})
            cur_dir, cur_start, cur_n = d, r["ts_utc"], 1
            pending_dir, pending_n = None, 0
        else:
            cur_n += 1

    if cur_dir:
        regimes.append({"label": cur_dir, "start": cur_start,
                        "end": None, "n": cur_n})

    for i, reg in enumerate(regimes, start=1):
        conn.execute("""
            INSERT INTO macro_regimes (country, regime_id, start_ts_utc,
                end_ts_utc, label, score_at_start, score_at_end,
                n_snapshots, detected_utc)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (country, i, reg["start"], reg["end"], reg["label"],
              None, None, reg["n"], now))
    conn.commit()
    return len(regimes)


def tag_releases(conn, country="US"):
    conn.execute("""
        DELETE FROM release_regimes WHERE release_id IN (
            SELECT r.id FROM releases r
            JOIN event_definitions e ON e.code = r.event_code
            WHERE e.country = ?
        )
    """, (country,))
    rows = conn.execute("""
        SELECT r.id AS release_id, r.release_time_utc FROM releases r
        JOIN event_definitions e ON e.code = r.event_code
        WHERE e.country = ? AND r.release_time_utc IS NOT NULL
    """, (country,)).fetchall()
    regs = conn.execute("""
        SELECT regime_id, start_ts_utc, end_ts_utc, label FROM macro_regimes
        WHERE country=? ORDER BY regime_id ASC
    """, (country,)).fetchall()
    if not regs: return 0

    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for r in rows:
        ts = r["release_time_utc"]
        match = None
        for reg in regs:
            if reg["start_ts_utc"] <= ts and (reg["end_ts_utc"] is None or ts < reg["end_ts_utc"]):
                match = reg
                break
        if match is None: match = regs[0]
        conn.execute("""
            INSERT OR REPLACE INTO release_regimes
              (release_id, country, regime_id, regime_label, tagged_utc)
            VALUES (?,?,?,?,?)
        """, (r["release_id"], country, match["regime_id"], match["label"], now))
        n += 1
    conn.commit()
    return n


def current_regime(conn, country="US"):
    row = conn.execute("""
        SELECT * FROM macro_regimes
        WHERE country=? AND end_ts_utc IS NULL
        ORDER BY regime_id DESC LIMIT 1
    """, (country,)).fetchone()
    return dict(row) if row else None


def refresh(conn, country="US"):
    return {"regimes": detect_regimes(conn, country),
            "releases_tagged": tag_releases(conn, country)}