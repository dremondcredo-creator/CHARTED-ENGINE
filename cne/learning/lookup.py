"""Runtime edge lookup."""
from __future__ import annotations

from cne.learning import features as feat

MIN_N = 15


def _row(conn, symbol, level, key):
    return conn.execute("""
        SELECT * FROM edge_stats
        WHERE symbol=? AND feature_level=? AND feature_hash=? AND horizon='T+2H'
    """, (symbol, level, feat.hash_key(key))).fetchone()


def historical_edge_for_release(conn, release_id, symbol="GOLD"):
    f = feat.build(conn, release_id)
    if not f: return {"found": False, "reason": "no feature vector"}

    for lvl, key in feat.feature_keys(f):
        r = _row(conn, symbol, lvl, key)
        if r and r["n_samples"] >= MIN_N:
            note = None if lvl == "L1" else f"widened to {lvl}"
            return {"found": True, "level": lvl, "feature_key": key,
                    "n_samples": r["n_samples"], "wins": r["wins"],
                    "losses": r["losses"], "flats": r["flats"],
                    "win_rate": r["win_rate"],
                    "avg_change_pct": r["avg_change_pct"],
                    "avg_mfe_pct": r["avg_mfe_pct"],
                    "avg_mae_pct": r["avg_mae_pct"],
                    "standard_error": r["standard_error"],
                    "confidence": r["confidence"], "fallback_note": note}
    return {"found": False, "reason": f"no level with n≥{MIN_N}"}


def top_edges(conn, symbol="GOLD", level="L2", min_n=20, limit=15):
    rows = conn.execute("""
        SELECT * FROM edge_stats
        WHERE symbol=? AND feature_level=? AND n_samples >= ?
        ORDER BY (win_rate * (n_samples * 1.0)) DESC LIMIT ?
    """, (symbol, level, min_n, limit)).fetchall()
    return [dict(r) for r in rows]