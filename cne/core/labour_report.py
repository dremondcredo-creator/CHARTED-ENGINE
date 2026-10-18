"""Combined US labour report: NFP + U3 + wages."""
from __future__ import annotations

import json
from datetime import datetime, timezone

REQUIRED = ["US_NFP", "US_UNEMPLOYMENT", "US_AHE_MOM"]


def _fetch(conn, code, period):
    row = conn.execute("""
        SELECT r.actual, r.forecast, r.previous,
               s.direction, s.impact, s.raw_surprise
        FROM releases r
        LEFT JOIN surprises s ON s.release_id = r.id
        WHERE r.event_code = ? AND r.period = ?
    """, (code, period)).fetchone()
    return dict(row) if row else None


def _verdict(n_bull, n_bear, n_neutral):
    total = n_bull + n_bear + n_neutral
    if n_bull == total and total >= 3:
        return "STRONG", "GREEN", "Hawkish Fed pressure — USD-positive"
    if n_bear == total and total >= 3:
        return "WEAK", "RED", "Dovish Fed pressure — USD-negative"
    if n_bull >= 2 and n_bear == 0:
        return "SOLID", "GREEN", "Mildly hawkish — USD-positive lean"
    if n_bear >= 2 and n_bull == 0:
        return "SOFT", "RED", "Mildly dovish — USD-negative lean"
    return "MIXED", "YELLOW", "Conflicting signals — wait for price confirmation"


def build_labour_report(conn, period=None):
    if period is None:
        row = conn.execute("""
            SELECT period FROM releases
            WHERE event_code = 'US_NFP' AND actual IS NOT NULL
            ORDER BY period DESC LIMIT 1
        """).fetchone()
        if not row:
            return None
        period = row["period"]

    components = {}
    impacts = []
    for code in REQUIRED:
        c = _fetch(conn, code, period)
        components[code] = c
        if c and c["impact"]:
            impacts.append(c["impact"])

    n_bull = impacts.count("BULLISH")
    n_bear = impacts.count("BEARISH")
    n_neutral = impacts.count("NEUTRAL")
    verdict, color, implication = _verdict(n_bull, n_bear, n_neutral)

    return {
        "period": period, "country": "US", "verdict": verdict,
        "color": color, "implication": implication,
        "components": components,
        "counts": {"bullish": n_bull, "bearish": n_bear, "neutral": n_neutral},
    }


def persist(conn, report):
    slim = {}
    for code, c in report["components"].items():
        if c:
            slim[code] = {
                "actual": c["actual"], "forecast": c["forecast"],
                "direction": c["direction"], "impact": c["impact"],
                "raw_surprise": c["raw_surprise"],
            }
    conn.execute("""
        INSERT INTO labour_reports
          (period, country, verdict, color, implication, components, ts_utc)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(period, country) DO UPDATE SET
          verdict=excluded.verdict, color=excluded.color,
          implication=excluded.implication,
          components=excluded.components, ts_utc=excluded.ts_utc
    """, (report["period"], report["country"], report["verdict"],
          report["color"], report["implication"],
          json.dumps(slim), datetime.now(timezone.utc).isoformat()))
    conn.commit()