"""Keyless BiQuote economic calendar with event-field provenance."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import iso_utc, save_observation
from cne.core.events import BY_CODE
from cne.db.database import upsert_release

BASE_URL = "https://biquote.io"
CALENDAR_URL = f"{BASE_URL}/api/calendar"
DOCS_URL = f"{BASE_URL}/docs/"
TERMS_URL = f"{BASE_URL}/terms.html"
SOURCE = "BiQuote economic calendar"
POLL_INTERVAL_MINUTES = 5

_US_EVENT_CODES = {
    "US_PPI_YOY": ("ppi",),
    "US_CORE_PPI_YOY": ("core ppi",),
    "US_NFP": ("nonfarm payroll", "non farm payroll", "employment change"),
    "US_UNEMPLOYMENT": ("unemployment rate",),
    "US_AHE_MOM": ("average hourly earnings",),
    "US_JOBLESS_CLAIMS": ("initial claims", "initial jobless claims"),
    "US_CONTINUING_CLAIMS": ("continuing claims", "continuing jobless claims"),
    "US_RETAIL_MOM": ("retail sales",),
    "US_GDP_QOQ": ("gdp",),
    "US_PCE_YOY": ("pce price", "personal consumption expenditures"),
    "US_ISM_PMI": ("ism manufacturing pmi",),
}
_EU_EVENT_CODES = {
    "EU_CORE_CPI_YOY": ("core cpi",),
    "EU_CPI_YOY": ("cpi", "hicp", "inflation"),
    "EU_GDP_QOQ": ("gdp",),
    "EU_EMPLOYMENT_QOQ": ("employment",),
    "EU_UNEMPLOYMENT": ("unemployment rate",),
    "EU_RETAIL_MOM": ("retail sales",),
    "EU_PMI_MFG": ("manufacturing pmi",),
}
_COUNTRY_NAMES = {
    "US": "United States",
    "EU": "Euro Area",
    "EZ": "Euro Area",
    "GB": "United Kingdom",
    "CA": "Canada",
    "JP": "Japan",
    "AU": "Australia",
    "NZ": "New Zealand",
    "CH": "Switzerland",
}
_UNIT_NAMES = {
    "percent": "%",
    "percentage": "%",
    "people": "people",
    "currency": "currency",
    "index": "index",
    "points": "points",
    "none": None,
}


def _number(value: object, field: str) -> float | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"BiQuote calendar returned an invalid {field}.") from exc
    if not isfinite(number):
        raise ValueError(f"BiQuote calendar returned a non-finite {field}.")
    return number


def _timestamp(value: object, field: str) -> str:
    if not value:
        raise ValueError(f"BiQuote calendar is missing {field}.")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"BiQuote calendar returned an invalid {field}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"BiQuote calendar returned a timezone-free {field}.")
    return iso_utc(parsed)


def _event_code(country_code: str, name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9/]+", " ", name.lower()).strip()
    codes = _US_EVENT_CODES if country_code == "US" else (
        _EU_EVENT_CODES if country_code in {"EU", "EZ"} else {}
    )
    for code, aliases in sorted(codes.items(), key=lambda item: max(map(len, item[1])), reverse=True):
        if any(alias in normalized for alias in aliases):
            return code
    if country_code == "US":
        is_core = "core" in normalized
        if "cpi" in normalized:
            if is_core:
                return "US_CORE_CPI_MOM" if "m/m" in normalized else "US_CORE_CPI_YOY"
            return "US_CPI_MOM" if "m/m" in normalized else "US_CPI_YOY"
    if country_code in {"EU", "EZ"} and "core" in normalized and "cpi" in normalized:
        return "EU_CORE_CPI_YOY"
    return None


def _period(value: object, event_name: str) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        match = re.fullmatch(r"(\d{4})-?Q([1-4])", str(value).strip(), re.IGNORECASE)
        if match:
            return f"{match.group(1)}-Q{match.group(2)}"
        return str(value).strip()
    if re.search(r"\bgdp\b", event_name, re.IGNORECASE):
        quarter = (parsed.month - 1) // 3 + 1
        return f"{parsed.year}-Q{quarter}"
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _normalized_unit(event: dict, code: str | None) -> str | None:
    if code in BY_CODE:
        return BY_CODE[code].unit
    unit = str(event.get("unit") or "").strip().lower()
    multiplier = str(event.get("multiplier") or "").strip().lower()
    normalized = _UNIT_NAMES.get(unit, unit or None)
    if normalized and multiplier not in {"", "none"}:
        return f"{normalized} ({multiplier})"
    return normalized


def _release_value(value: float | None, event: dict, code: str | None) -> float | None:
    if value is None or not code:
        return value
    if (
        BY_CODE[code].unit == "K"
        and str(event.get("multiplier") or "").lower() == "none"
        and str(event.get("unit") or "").lower() == "people"
        and abs(value) >= 1_000
    ):
        return value / 1_000
    return value


def _fetch_events(start: datetime, end: datetime) -> list[dict]:
    def fetch_range(first_day, last_day) -> list[dict]:
        response = http.request(
            "GET",
            CALENDAR_URL,
            params={
                "from": first_day.isoformat(),
                "to": last_day.isoformat(),
                "countries": "US,EU",
                "limit": 500,
            },
            timeout=30,
            cache_seconds=300,
        )
        events = response.json()
        if not isinstance(events, list):
            raise TypeError("BiQuote calendar response is not a JSON list.")
        if len(events) < 500:
            return events
        if first_day == last_day:
            raise ValueError(
                f"BiQuote calendar reached its 500-event limit on {first_day}; "
                "the day's results may be truncated."
            )
        midpoint = first_day + (last_day - first_day) // 2
        return fetch_range(first_day, midpoint) + fetch_range(
            midpoint + timedelta(days=1), last_day
        )

    return fetch_range(start.date(), end.date())


def collect(
    conn,
    *,
    days_back: int = 7,
    days_ahead: int = 45,
) -> int:
    if not 0 <= days_back <= 92 or not 0 <= days_ahead <= 92 or days_back + days_ahead > 92:
        raise ValueError("BiQuote calendar date range must fit the 92-day request window.")
    now = datetime.now(timezone.utc)
    events = _fetch_events(
        now - timedelta(days=days_back),
        now + timedelta(days=days_ahead),
    )
    retrieved_at = datetime.now(timezone.utc)
    retrieved = retrieved_at.isoformat()
    changed = 0
    for event in events:
        if not isinstance(event, dict):
            raise TypeError("BiQuote calendar entry is not a JSON object.")
        event_id = str(event.get("id") or "").strip()
        event_name = str(event.get("name") or "").strip()
        if not event_id or not event_name:
            raise ValueError("BiQuote calendar entry is missing its id or name.")
        event_id = f"biquote:{event_id}"
        release_time = _timestamp(event.get("time"), "scheduled release time")
        parsed_release_time = datetime.fromisoformat(release_time)
        if parsed_release_time > now + timedelta(days=93):
            raise ValueError("BiQuote calendar entry is outside the requested date range.")
        forecast_was_available_pre_release = retrieved_at <= parsed_release_time

        country_code = str(event.get("countryCode") or "").strip().upper()
        currency = str(event.get("currency") or "").strip().upper() or None
        country = _COUNTRY_NAMES.get(country_code, country_code or None)
        code = _event_code(country_code, event_name)
        period = _period(event.get("period"), event_name)
        provider_forecast = _number(event.get("forecast"), "forecast")
        previous = _number(event.get("previous"), "previous")
        actual = _number(event.get("actual"), "actual")
        revised_previous = _number(event.get("revisedPrevious"), "revised previous")
        provider_forecast = _release_value(provider_forecast, event, code)
        previous = _release_value(previous, event, code)
        actual = _release_value(actual, event, code)
        revised_previous = _release_value(revised_previous, event, code)
        source_time = None
        raw_json = json.dumps(event, ensure_ascii=False, sort_keys=True)
        old = conn.execute(
            "SELECT * FROM calendar_events WHERE event_id=?", (event_id,)
        ).fetchone()
        forecast = provider_forecast
        forecast_text = (
            str(event["forecast"])
            if event.get("forecast") is not None
            else None
        )
        if (
            not forecast_was_available_pre_release
            and old
            and old["forecast"] is not None
        ):
            forecast = old["forecast"]
            forecast_text = old["forecast_text"]
        old_payload = json.loads(old["raw_json"]) if old else {}
        is_revision = bool(
            int(event.get("revision") or 0) > 0
            or revised_previous is not None
            or old
            and any(
                old_payload.get(field) != event.get(field)
                for field in ("forecast", "previous", "actual", "revisedPrevious")
                if old_payload.get(field) is not None and event.get(field) is not None
            )
        )
        effective_actual = actual
        if effective_actual is None and old and old["actual"] is not None:
            effective_actual = old["actual"]
        if effective_actual is not None:
            release_status = "RELEASED"
        elif parsed_release_time <= retrieved_at:
            release_status = "DELAYED"
        else:
            release_status = "SCHEDULED"

        unit = _normalized_unit(event, code)
        importance = str(event.get("importance") or "").strip().upper() or None
        provider_source = str(event.get("source") or "").strip() or "unspecified"
        source_url = str(event.get("sourceUrl") or "").strip()
        if not source_url.startswith("https://"):
            source_url = DOCS_URL
        validation_errors = [
            field
            for field, value in (
                ("currency", currency),
                ("country", country),
                ("period", period),
                ("unit", unit),
            )
            if not value
        ]
        missing_fields = [
            field
            for field in ("forecast", "previous", "actual", "revisedPrevious")
            if event.get(field) is None
        ]
        conn.execute(
            """
            INSERT INTO calendar_events
              (event_id, event_name, currency, country, release_time_utc, period,
               forecast_text, forecast, previous_text, previous, actual_text, actual,
               revised_previous_text, revised_previous, unit, importance, source,
               source_url, source_timestamp_utc, retrieved_at_utc, release_status,
               is_revision, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_id) DO UPDATE SET
              event_name=excluded.event_name,
              currency=COALESCE(excluded.currency, calendar_events.currency),
              country=COALESCE(excluded.country, calendar_events.country),
              release_time_utc=COALESCE(excluded.release_time_utc, calendar_events.release_time_utc),
              period=COALESCE(excluded.period, calendar_events.period),
              forecast_text=COALESCE(excluded.forecast_text, calendar_events.forecast_text),
              forecast=COALESCE(excluded.forecast, calendar_events.forecast),
              previous_text=COALESCE(excluded.previous_text, calendar_events.previous_text),
              previous=COALESCE(excluded.previous, calendar_events.previous),
              actual_text=COALESCE(excluded.actual_text, calendar_events.actual_text),
              actual=COALESCE(excluded.actual, calendar_events.actual),
              revised_previous_text=COALESCE(excluded.revised_previous_text, calendar_events.revised_previous_text),
              revised_previous=COALESCE(excluded.revised_previous, calendar_events.revised_previous),
              unit=COALESCE(excluded.unit, calendar_events.unit),
              importance=COALESCE(excluded.importance, calendar_events.importance),
              source=excluded.source,
              source_url=excluded.source_url,
              retrieved_at_utc=excluded.retrieved_at_utc,
              release_status=excluded.release_status,
              is_revision=MAX(calendar_events.is_revision, excluded.is_revision),
              raw_json=excluded.raw_json
            """,
            (
                event_id,
                event_name,
                currency,
                country,
                release_time,
                period,
                forecast_text,
                forecast,
                str(event["previous"]) if event.get("previous") is not None else None,
                previous,
                str(event["actual"]) if event.get("actual") is not None else None,
                actual,
                str(event["revisedPrevious"]) if event.get("revisedPrevious") is not None else None,
                revised_previous,
                unit,
                importance,
                f"{SOURCE} · {provider_source}",
                source_url,
                source_time,
                retrieved,
                release_status,
                int(is_revision),
                raw_json,
            ),
        )

        if code and period:
            upsert_release(
                conn,
                event_code=code,
                period=period,
                release_time_utc=release_time,
                previous=previous,
                forecast=forecast if forecast_was_available_pre_release else None,
                actual=actual,
                revised_previous=revised_previous,
                actual_source=f"{SOURCE} · {provider_source}" if actual is not None else None,
                forecast_source=(
                    f"{SOURCE} · {provider_source}"
                    if forecast is not None and forecast_was_available_pre_release
                    else None
                ),
            )

        for field, raw_value, numeric, status in (
            (
                "FORECAST",
                event.get("forecast"),
                provider_forecast,
                "PRE_RELEASE_FORECAST"
                if forecast_was_available_pre_release
                else "POST_RELEASE_FORECAST",
            ),
            ("PREVIOUS", event.get("previous"), previous, "PUBLISHED" if previous is not None else "SCHEDULED"),
            ("ACTUAL", event.get("actual"), actual, release_status),
            ("REVISED_PREVIOUS", event.get("revisedPrevious"), revised_previous, release_status),
        ):
            if raw_value is None:
                continue
            save_observation(
                conn,
                domain="economic",
                indicator=f"{code or 'UNMAPPED'}_{field}",
                observation_period=period or event_id,
                value_numeric=numeric,
                value_text=str(raw_value),
                unit=unit or "provider value",
                source=f"{SOURCE} · {provider_source}",
                source_url=source_url,
                observed_utc=release_time,
                published_utc=None,
                publication_status=status,
                metadata={
                    "provider": SOURCE,
                    "source_record_id": str(event.get("id")),
                    "source_timestamp_utc": source_time,
                    "retrieved_at_utc": retrieved,
                    "data_status": status,
                    "is_revision": is_revision,
                    "is_delayed": False,
                    "validation_errors": validation_errors,
                    "missing_fields": missing_fields,
                    "last_successful_sync_utc": retrieved,
                    "event_code": code,
                    "event_name": event_name,
                    "currency": currency,
                    "country": country,
                    "period": period,
                    "provider_unit": event.get("unit"),
                    "multiplier": event.get("multiplier"),
                    "importance": importance,
                    "upstream_source": provider_source,
                    "source_endpoint": CALENDAR_URL,
                    "time_mode": event.get("timeMode"),
                    "forecast_known_pre_release": forecast_was_available_pre_release,
                },
            )

        save_observation(
            conn,
            domain="economic",
            indicator="CALENDAR_EVENT",
            observation_period=event_id,
            value_text=event_name,
            unit="event",
            source=f"{SOURCE} · {provider_source}",
            source_url=source_url,
            observed_utc=release_time,
            published_utc=None,
            publication_status=release_status,
            metadata={
                "provider": SOURCE,
                "source_record_id": str(event.get("id")),
                "source_timestamp_utc": source_time,
                "retrieved_at_utc": retrieved,
                "data_status": release_status,
                "is_revision": is_revision,
                "is_delayed": release_status == "DELAYED",
                "validation_errors": validation_errors,
                "missing_fields": missing_fields,
                "last_successful_sync_utc": retrieved,
                "event_code": code,
                "currency": currency,
                "country": country,
                "period": period,
                "importance": importance,
                "upstream_source": provider_source,
                "source_endpoint": CALENDAR_URL,
                "time_mode": event.get("timeMode"),
            },
        )
        changed += 1
    conn.commit()
    return changed
