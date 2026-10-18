"""Pure release-to-job schedule planning helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cne.collectors import biquote_calendar, finance_calendar
from cne.config import settings


def _parse(value: str):
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def build_schedule(conn, days_ahead=14):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    rows = conn.execute("""
        SELECT id, event_code, period, release_time_utc FROM releases
        WHERE release_time_utc IS NOT NULL
    """).fetchall()
    jobs = []
    for row in rows:
        timestamp = _parse(row["release_time_utc"])
        if not timestamp or timestamp < now or timestamp > end:
            continue
        jobs.append({
            "release_id": row["id"],
            "code": row["event_code"],
            "period": row["period"],
            "ts": timestamp,
            "pre_at": timestamp - timedelta(minutes=15),
            "release_alert_at": timestamp + timedelta(minutes=5),
            "snapshot_times": {
                "T0": timestamp,
                "T+5M": timestamp + timedelta(minutes=5),
                "T+30M": timestamp + timedelta(minutes=30),
                "T+2H": timestamp + timedelta(hours=2),
            },
            "finalize_at": timestamp + timedelta(hours=2, minutes=10),
        })
    return jobs


def _calendar_code(event: dict) -> str | None:
    source = str(event.get("source") or "")
    name = str(event.get("event_name") or "")
    currency = str(event.get("currency") or "").upper()
    if source.startswith(biquote_calendar.SOURCE):
        country = "US" if currency == "USD" else "EU" if currency == "EUR" else ""
        return biquote_calendar._event_code(country, name)
    if source.startswith(finance_calendar.SOURCE):
        return finance_calendar._event_code({"name": name, "title": name})
    return None


def build_preparation_schedule(
    conn,
    days_ahead=14,
    windows_minutes: tuple[int, ...] | None = None,
):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    windows = windows_minutes or settings.preparation_windows_minutes
    rows = conn.execute(
        """
        SELECT event_id, event_name, currency, country, period, release_time_utc,
               source, actual
        FROM calendar_events
        WHERE release_time_utc IS NOT NULL AND actual IS NULL
          AND release_status != 'RELEASED'
        """
    ).fetchall()
    entries = []
    scheduled_releases: set[tuple[str, str]] = set()
    for row in rows:
        timestamp = _parse(row["release_time_utc"])
        if not timestamp or timestamp <= now or timestamp > end:
            continue
        event = dict(row)
        code = _calendar_code(event)
        if code and row["period"]:
            scheduled_releases.add((code, row["period"]))
        preparation_times = {
            window: timestamp - timedelta(minutes=window)
            for window in windows
            if timestamp - timedelta(minutes=window) > now
        }
        if preparation_times:
            entries.append(
                {
                    "event_key": row["event_id"],
                    "event_name": row["event_name"],
                    "release_time_utc": timestamp,
                    "preparation_times": preparation_times,
                }
            )

    release_rows = conn.execute(
        """
        SELECT r.id, r.event_code, r.period, r.release_time_utc, e.name
        FROM releases r JOIN event_definitions e ON e.code = r.event_code
        WHERE r.release_time_utc IS NOT NULL AND r.actual IS NULL
        """
    ).fetchall()
    for row in release_rows:
        timestamp = _parse(row["release_time_utc"])
        key = (row["event_code"], row["period"])
        if (
            not timestamp
            or timestamp <= now
            or timestamp > end
            or key in scheduled_releases
        ):
            continue
        preparation_times = {
            window: timestamp - timedelta(minutes=window)
            for window in windows
            if timestamp - timedelta(minutes=window) > now
        }
        if preparation_times:
            entries.append(
                {
                    "event_key": f"release:{row['id']}",
                    "event_name": row["name"],
                    "release_time_utc": timestamp,
                    "preparation_times": preparation_times,
                }
            )
    return sorted(entries, key=lambda item: item["release_time_utc"])
