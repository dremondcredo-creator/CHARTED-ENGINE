"""Trend detection across an event's history."""
from __future__ import annotations

from datetime import datetime, timezone


def event_history(conn, event_code, limit=12):
    rows = conn.execute("""
        SELECT r.period, r.actual, r.forecast, r.previous,
               s.direction, s.impact, s.raw_surprise
        FROM releases r
        LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.event_code = ? AND r.actual IS NOT NULL
        ORDER BY r.period DESC
        LIMIT ?
    """, (event_code, limit)).fetchall()
    return list(reversed(rows))


def _linear_slope(values):
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = sum(xs) / n
    my = sum(values) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def _streak(history):
    dirs = [r["direction"] for r in history if r["direction"]]
    if not dirs:
        return 0, None
    last = dirs[-1]
    if last == "INLINE":
        return 0, None
    count = 0
    for d in reversed(dirs):
        if d == last:
            count += 1
        else:
            break
    return count, last


def detect_trend(history, flat_threshold_pct=0.15):
    vals = [r["actual"] for r in history if r["actual"] is not None]
    if len(vals) < 3:
        return {"direction": "INSUFFICIENT", "slope": 0.0, "norm_slope": 0.0,
                "streak": 0, "streak_dir": None, "n_obs": len(vals)}

    slope = _linear_slope(vals)
    mean = sum(vals) / len(vals)
    norm = slope / abs(mean) if mean else 0.0

    diffs = [abs(vals[i] - vals[i-1]) for i in range(1, len(vals))]
    avg_step = sum(diffs) / len(diffs) if diffs else 0.0
    if avg_step and abs(slope) < 0.25 * avg_step:
        direction = "FLAT"
    elif norm > flat_threshold_pct / 100:
        direction = "RISING"
    elif norm < -flat_threshold_pct / 100:
        direction = "FALLING"
    else:
        direction = "FLAT"

    s, sd = _streak(history)
    return {
        "direction": direction,
        "slope": round(slope, 4),
        "norm_slope": round(norm * 100, 3),
        "streak": s,
        "streak_dir": sd,
        "n_obs": len(vals),
    }


def persist_trend(conn, event_code, trend):
    conn.execute("""
        INSERT INTO trend_snapshots
          (ts_utc, event_code, direction, slope, norm_slope,
           streak, streak_dir, n_obs)
        VALUES (?,?,?,?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(), event_code,
          trend["direction"], trend["slope"], trend["norm_slope"],
          trend["streak"], trend["streak_dir"], trend["n_obs"]))
    conn.commit()


def reinforcement_status(trend, latest_direction):
    if trend["direction"] in ("INSUFFICIENT", "FLAT") or latest_direction == "INLINE":
        return "NEUTRAL"
    if trend["direction"] == "RISING" and latest_direction == "UPSIDE":
        return "REINFORCES"
    if trend["direction"] == "FALLING" and latest_direction == "DOWNSIDE":
        return "REINFORCES"
    return "CONTRADICTS"