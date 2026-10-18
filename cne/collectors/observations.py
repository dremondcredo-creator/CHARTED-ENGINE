"""Shared source and timestamp helpers for Workflow 01."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from cne.db.database import record_observation


def iso_utc(value: str | datetime | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def period_timestamp(period: str) -> str:
    """Represent a source period's date without implying its release time."""
    date_part = period[:10]
    if len(period) == 7 and period[4:6] == "-Q" and period[-1] in "1234":
        quarter_month = (int(period[-1]) - 1) * 3 + 1
        try:
            return iso_utc(datetime(int(period[:4]), quarter_month, 1))
        except ValueError:
            return iso_utc()
    try:
        parsed = datetime.fromisoformat(date_part)
    except ValueError:
        try:
            parsed = datetime.strptime(period, "%Y-%m")
        except ValueError:
            try:
                parsed = datetime.strptime(period, "%Y")
            except ValueError:
                return iso_utc()
    return iso_utc(parsed)


def parse_publication_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return iso_utc(parsed)


def save_observation(
    conn,
    *,
    domain: str,
    indicator: str,
    value_numeric: float | None = None,
    value_text: str | None = None,
    unit: str,
    source: str,
    source_url: str | None,
    observed_utc: str,
    published_utc: str | None = None,
    publication_status: str = "PUBLISHED",
    observation_period: str | None = None,
    metadata: dict | None = None,
) -> None:
    record_observation(
        conn,
        domain=domain,
        indicator=indicator,
        value_numeric=value_numeric,
        value_text=value_text,
        unit=unit,
        source=source,
        source_url=source_url,
        observed_utc=iso_utc(observed_utc),
        published_utc=iso_utc(published_utc) if published_utc else None,
        publication_status=publication_status,
        observation_period=observation_period,
        metadata=metadata,
    )
