"""Surprise engine: actual − forecast → classified impact."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from cne.core.events import BY_CODE

_INVERT = {"BULLISH": "BEARISH", "BEARISH": "BULLISH", "NEUTRAL": "NEUTRAL"}


def compute_surprise(
    event_code: str,
    forecast: float,
    actual: float,
    historical_scale: float | None = None,
) -> dict:
    ev = BY_CODE.get(event_code)
    if not ev:
        return {"error": f"unknown event {event_code}"}
    if not isfinite(forecast) or not isfinite(actual):
        return {"error": "forecast and actual must be finite numeric values"}

    raw = actual - forecast

    if abs(raw) <= ev.inline_tol:
        direction, magnitude, impact = "INLINE", "SMALL", "NEUTRAL"
    else:
        direction = "UPSIDE" if raw > 0 else "DOWNSIDE"
        magnitude = "LARGE" if abs(raw) >= ev.large_thr else "MODERATE"
        impact = ev.higher_is if direction == "UPSIDE" else _INVERT[ev.higher_is]

    normalized = (
        raw / historical_scale
        if historical_scale is not None and isfinite(historical_scale)
        and historical_scale > 0
        else None
    )
    return {
        "raw_surprise": round(raw, 4),
        "normalized_surprise": round(normalized, 4) if normalized is not None else None,
        "historical_scale": historical_scale,
        "direction": direction,
        "economic_direction": "ABOVE" if raw > 0 else "BELOW" if raw < 0 else "INLINE",
        "magnitude": magnitude,
        "impact": impact,
    }


def persist(conn, release_id: int, result: dict) -> None:
    conn.execute("""
        INSERT INTO surprises (release_id, raw_surprise, direction,
                               magnitude, impact, computed_utc,
                               normalized_surprise, historical_scale,
                               normalization_n, normalization_status, data_status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(release_id) DO UPDATE SET
          raw_surprise=excluded.raw_surprise,
          normalized_surprise=excluded.normalized_surprise,
          historical_scale=excluded.historical_scale,
          normalization_n=excluded.normalization_n,
          normalization_status=excluded.normalization_status,
          data_status=excluded.data_status,
          direction=excluded.direction,
          magnitude=excluded.magnitude,
          impact=excluded.impact,
          computed_utc=excluded.computed_utc
    """, (
        release_id,
        result["raw_surprise"],
        result["direction"],
        result["magnitude"],
        result["impact"],
        datetime.now(timezone.utc).isoformat(),
        result.get("normalized_surprise"),
        result.get("historical_scale"),
        result.get("normalization_n"),
        result.get("normalization_status"),
        result.get("data_status", "UNVERIFIED"),
    ))
    conn.commit()


def recompute_all(conn) -> int:
    from cne.core.release_monitor import process_releases

    return process_releases(conn)