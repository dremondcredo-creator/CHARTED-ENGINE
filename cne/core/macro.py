"""Macro bias scoring — weighted hawkish/dovish per country."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from cne.core.events import BY_CODE

CATEGORY_WEIGHTS = {"inflation": 1.00, "labour": 0.75, "growth": 0.25, "policy": 1.00}
TIER_WEIGHTS = {1: 2.0, 2: 1.0, 3: 0.5}
IMPACT_VALUE = {"BULLISH": 1.0, "BEARISH": -1.0, "NEUTRAL": 0.0}

LABELS = [
    ( 4.0, "STRONG HAWKISH"),
    ( 2.0, "HAWKISH"),
    ( 0.5, "MILD HAWKISH"),
    (-0.5, "NEUTRAL"),
    (-2.0, "MILD DOVISH"),
    (-4.0, "DOVISH"),
    (-1e9, "STRONG DOVISH"),
]


def _label(score):
    for threshold, name in LABELS:
        if score >= threshold:
            return name
    return "NEUTRAL"


def _parse_ts(s):
    if not s:
        return None
    try:
        ts = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def recent_releases(conn, country="US", window_days=30):
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = conn.execute("""
        SELECT r.id, r.event_code, r.period, r.release_time_utc,
               s.impact, s.direction, s.magnitude
        FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN event_definitions e ON e.code = r.event_code
        WHERE e.country = ?
    """, (country,)).fetchall()
    out = []
    for r in rows:
        ts = _parse_ts(r["release_time_utc"])
        if ts and ts >= cutoff:
            out.append(dict(r))
    out.sort(key=lambda x: x["release_time_utc"] or "", reverse=True)
    return out


def compute_macro_score(conn, country="US", window_days=30):
    releases = recent_releases(conn, country, window_days)
    buckets = {"inflation": 0.0, "labour": 0.0, "growth": 0.0, "policy": 0.0}
    contributions = []

    for r in releases:
        ev = BY_CODE.get(r["event_code"])
        if not ev or not r["impact"]:
            continue
        cat_w = CATEGORY_WEIGHTS.get(ev.category, 0.0)
        tier_w = TIER_WEIGHTS.get(ev.tier, 1.0)
        points = IMPACT_VALUE[r["impact"]] * cat_w * tier_w
        buckets[ev.category] = buckets.get(ev.category, 0.0) + points
        contributions.append({
            "event": ev.code, "name": ev.name, "period": r["period"],
            "impact": r["impact"], "category": ev.category, "tier": ev.tier,
            "weight": round(cat_w * tier_w, 3), "points": round(points, 3),
        })

    total = sum(buckets.values())
    return {
        "country": country, "window_days": window_days,
        "n_releases": len(releases),
        "total": round(total, 3), "label": _label(total),
        "inflation": round(buckets["inflation"], 3),
        "labour":    round(buckets["labour"], 3),
        "growth":    round(buckets["growth"], 3),
        "policy":    round(buckets["policy"], 3),
        "contributions": contributions,
    }


def persist_snapshot(conn, score):
    conn.execute("""
        INSERT INTO macro_snapshots
          (ts_utc, country, window_days, n_releases,
           inflation_score, labour_score, growth_score,
           total_score, bias_label)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (datetime.now(timezone.utc).isoformat(),
          score["country"], score["window_days"], score["n_releases"],
          score["inflation"], score["labour"], score["growth"],
          score["total"], score["label"]))
    conn.commit()


def narrative(score):
    parts = []
    if score["inflation"] > 0.5: parts.append("inflation pressures running hot")
    elif score["inflation"] < -0.5: parts.append("inflation cooling")
    else: parts.append("inflation near neutral")
    if score["labour"] > 0.5: parts.append("labour market resilient")
    elif score["labour"] < -0.5: parts.append("labour market softening")
    else: parts.append("labour signals mixed")
    if score["growth"] > 0.5: parts.append("growth supportive")
    elif score["growth"] < -0.5: parts.append("growth disappointing")
    return f"{score['label']}: " + "; ".join(parts) + "."