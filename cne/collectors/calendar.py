"""Forecast/consensus loader from CSV."""
from __future__ import annotations

import csv
import math

from cne.collectors.observations import iso_utc
from cne.config import settings
from cne.core.events import BY_CODE
from cne.db.database import record_observation, upsert_release


def _ensure_template():
    if not settings.calendar_csv.exists():
        settings.calendar_csv.write_text(
            "event_code,period,forecast,previous,release_time_utc,consensus_source\n"
            "# Add real rows before each release\n"
        )


def _f(v, *, field, row_number):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        number = float(v)
    except ValueError as exc:
        raise ValueError(f"calendar.csv row {row_number}: invalid {field} value {v!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"calendar.csv row {row_number}: {field} must be finite")
    return number


def collect(conn):
    _ensure_template()
    before = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
    with settings.calendar_csv.open() as fh:
        for row_number, row in enumerate(csv.DictReader(fh), start=2):
            code = (row.get("event_code") or "").strip()
            if not code or code.startswith("#"):
                continue
            if code not in BY_CODE:
                raise ValueError(f"calendar.csv row {row_number}: unknown event_code {code!r}")
            period = (row.get("period") or "").strip()
            if not period:
                raise ValueError(f"calendar.csv row {row_number}: period is required")
            forecast = _f(row.get("forecast"), field="forecast", row_number=row_number)
            previous = _f(row.get("previous"), field="previous", row_number=row_number)
            release_time = (row.get("release_time_utc") or "").strip() or None
            source = (row.get("consensus_source") or "").strip() or "User-entered calendar.csv"
            upsert_release(
                conn,
                event_code=code,
                period=period,
                release_time_utc=release_time,
                previous=previous,
                forecast=forecast,
                forecast_source=source,
            )
            event = BY_CODE[code]
            observed = iso_utc(release_time) if release_time else iso_utc()
            for field, value, status in (
                ("forecast", forecast, "USER_ENTERED_FORECAST"),
                ("previous", previous, "PRIOR_PUBLISHED_VALUE"),
            ):
                if value is None:
                    continue
                record_observation(
                    conn,
                    domain="economic",
                    indicator=f"{code}_{field.upper()}",
                    observation_period=period,
                    value_numeric=value,
                    unit=event.unit,
                    source=source,
                    source_url="data/calendar.csv",
                    observed_utc=observed,
                    publication_status=status,
                    metadata={"event_code": code, "release_time_utc": release_time},
                )
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
    return after - before