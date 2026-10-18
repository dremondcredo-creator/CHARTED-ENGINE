"""Aggregated edge statistics."""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from cne.learning import features as feat

CONF_BANDS = [(60, "HIGH"), (30, "MEDIUM"), (15, "LOW")]
MIN_SAMPLES = 10


def _confidence(n):
    for floor, label in CONF_BANDS:
        if n >= floor: return label
    return "INSUFFICIENT"


def refresh(conn):
    conn.execute("DELETE FROM edge_stats")

    rows = conn.execute("""
        SELECT o.release_id, o.symbol, o.outcome, o.outcome_magnitude,
               o.mfe_2h_pct, o.mae_2h_pct,
               f.event_code, f.tier, f.category,
               f.surprise_direction, f.surprise_magnitude,
               f.market_alignment, f.structure_event, f.held_retest
        FROM outcome_labels o
        JOIN setup_features f ON f.release_id = o.release_id
        WHERE o.outcome IS NOT NULL
    """).fetchall()

    groups = {}
    for r in rows:
        d = dict(r)
        for lvl, key in feat.feature_keys(d):
            gk = (r["symbol"], lvl, key)
            groups.setdefault(gk, []).append(r)

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for (symbol, level, key), items in groups.items():
        n = len(items)
        if n < MIN_SAMPLES: continue
        wins = sum(1 for x in items if x["outcome"] == "WIN")
        losses = sum(1 for x in items if x["outcome"] == "LOSS")
        flats = sum(1 for x in items if x["outcome"] == "FLAT")
        changes = [x["outcome_magnitude"] for x in items if x["outcome_magnitude"] is not None]
        mfes = [x["mfe_2h_pct"] for x in items if x["mfe_2h_pct"] is not None]
        maes = [x["mae_2h_pct"] for x in items if x["mae_2h_pct"] is not None]

        avg = statistics.mean(changes) if changes else None
        sd = statistics.stdev(changes) if len(changes) >= 2 else None
        se = (sd / (len(changes) ** 0.5)) if (sd and changes) else None

        conn.execute("""
            INSERT INTO edge_stats
              (symbol, feature_level, feature_key, feature_hash,
               n_samples, wins, losses, flats, win_rate,
               avg_change_pct, avg_mfe_pct, avg_mae_pct,
               stddev_change_pct, standard_error, confidence,
               horizon, updated_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (symbol, level, key, feat.hash_key(key),
              n, wins, losses, flats, round(wins / n, 4),
              round(avg, 4) if avg is not None else None,
              round(statistics.mean(mfes), 4) if mfes else None,
              round(statistics.mean(maes), 4) if maes else None,
              round(sd, 4) if sd is not None else None,
              round(se, 4) if se is not None else None,
              _confidence(n), "T+2H", now))
        written += 1
    conn.commit()
    return written