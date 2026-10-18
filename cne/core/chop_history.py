"""Chop persistence — how long has a symbol been choppy?"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


def snapshot(conn, symbol, timeframe="5m"):
    from cne.core.chop import evaluate_symbol
    r = evaluate_symbol(conn, symbol, timeframe=timeframe)
    if not r: return None
    conn.execute("""
        INSERT INTO chop_history (ts_utc, symbol, timeframe, chop_score, chop_label)
        VALUES (?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(), symbol, timeframe,
          r["chop_score"], r["label"]))
    conn.commit()
    return r


def trend(conn, symbol, timeframe="5m", days=3):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT ts_utc, chop_score, chop_label FROM chop_history
        WHERE symbol=? AND timeframe=? AND ts_utc >= ?
        ORDER BY ts_utc ASC
    """, (symbol, timeframe, cutoff)).fetchall()
    if not rows:
        return {"n_snapshots": 0, "trend_label": "UNKNOWN"}

    scores = [r["chop_score"] for r in rows if r["chop_score"] is not None]
    labels = [r["chop_label"] for r in rows]
    choppy_pct = labels.count("CHOPPY") / len(labels) if labels else 0
    clean_pct = labels.count("CLEAN") / len(labels) if labels else 0
    changes = sum(1 for i in range(1, len(labels)) if labels[i] != labels[i-1])
    flip_rate = changes / (len(labels) - 1) if len(labels) > 1 else 0

    if flip_rate >= 0.4: trend_label = "FLIPPING"
    elif choppy_pct >= 0.7: trend_label = "PERSISTENT_CHOPPY"
    elif clean_pct >= 0.7: trend_label = "PERSISTENT_CLEAN"
    else: trend_label = "MIXED"

    return {
        "symbol": symbol, "n_snapshots": len(rows),
        "mean_chop": round(sum(scores) / len(scores), 2) if scores else None,
        "choppy_pct": round(choppy_pct * 100, 1),
        "clean_pct": round(clean_pct * 100, 1),
        "flip_rate": round(flip_rate * 100, 1),
        "trend_label": trend_label,
    }


def prefer_with_history(conn, gold_now, eurusd_now):
    g_now = gold_now["chop_score"] if gold_now else None
    e_now = eurusd_now["chop_score"] if eurusd_now else None
    if g_now is None and e_now is None:
        return None, "no current chop data"
    if g_now is None: return "EURUSD", "Gold data unavailable"
    if e_now is None: return "GOLD", "EURUSD data unavailable"

    g_hist = trend(conn, "GOLD")
    e_hist = trend(conn, "EURUSD")

    def adj(score, hist):
        s = score
        if hist.get("trend_label") == "PERSISTENT_CHOPPY": s += 8
        elif hist.get("trend_label") == "FLIPPING": s += 5
        elif hist.get("trend_label") == "PERSISTENT_CLEAN": s -= 5
        return s

    g_adj = adj(g_now, g_hist)
    e_adj = adj(e_now, e_hist)
    diff = g_adj - e_adj
    reason = (f"Gold adj {g_adj:.1f} ({g_hist.get('trend_label','?')}) vs "
              f"EURUSD adj {e_adj:.1f} ({e_hist.get('trend_label','?')})")
    if abs(diff) < 5:
        return None, f"tie · {reason}"
    return ("GOLD" if diff < 0 else "EURUSD"), reason