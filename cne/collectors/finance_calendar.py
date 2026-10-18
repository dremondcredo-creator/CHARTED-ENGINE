"""No-key FinanceCalendar public economic calendar connector."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from math import isfinite
from urllib.parse import urlparse

from cne.collectors import http
from cne.collectors.observations import iso_utc, save_observation
from cne.core.events import BY_CODE
from cne.db.database import upsert_release

CALENDAR_URL = "https://www.financecalendar.com/wp-json/fc/v1/calendar"
DOCS_URL = "https://www.financecalendar.com/api/"
SOURCE = "FinanceCalendar.com"

_EVENT_CODES = {
    "us cpi": "US_CPI_YOY",
    "us cpi report": "US_CPI_YOY",
    "us consumer price index": "US_CPI_YOY",
    "us core cpi": "US_CORE_CPI_YOY",
    "us core cpi report": "US_CORE_CPI_YOY",
    "us ppi": "US_PPI_YOY",
    "us producer price index": "US_PPI_YOY",
    "us core ppi": "US_CORE_PPI_YOY",
    "us core producer price index": "US_CORE_PPI_YOY",
    "us nonfarm payrolls": "US_NFP",
    "us non farm payrolls": "US_NFP",
    "us jobs report": "US_NFP",
    "us unemployment rate": "US_UNEMPLOYMENT",
    "us average hourly earnings": "US_AHE_MOM",
    "us initial jobless claims": "US_JOBLESS_CLAIMS",
    "us jobless claims": "US_JOBLESS_CLAIMS",
    "us continuing jobless claims": "US_CONTINUING_CLAIMS",
    "us retail sales": "US_RETAIL_MOM",
    "us gdp": "US_GDP_QOQ",
    "us pce inflation": "US_PCE_YOY",
    "us core pce inflation": "US_PCE_YOY",
    "euro area inflation": "EU_CPI_YOY",
    "eurozone inflation": "EU_CPI_YOY",
    "eurozone cpi": "EU_CPI_YOY",
    "eurozone core cpi": "EU_CORE_CPI_YOY",
    "eurozone gdp": "EU_GDP_QOQ",
    "euro area gdp": "EU_GDP_QOQ",
    "eurozone employment": "EU_EMPLOYMENT_QOQ",
    "euro area employment": "EU_EMPLOYMENT_QOQ",
}


def _normal(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _event_code(event: dict) -> str | None:
    for field in ("name", "title"):
        candidate = _EVENT_CODES.get(_normal(event.get(field)))
        if candidate:
            return candidate
    name = _normal(event.get("name"))
    title = _normal(event.get("title"))
    for alias, code in sorted(_EVENT_CODES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in name or alias in title:
            return code
    return None


def _number(value: object, event_code: str | None) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(",", "")
        match = re.fullmatch(
            r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMB])?\s*(%|percent)?"
            r"(?:\s+(?:initial claims|continuing claims|claims|jobs|payrolls))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        number = float(match.group(1))
        suffix = (match.group(2) or "").upper()
        number *= {"": 1.0, "K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[suffix]
        if event_code in {"US_JOBLESS_CLAIMS", "US_CONTINUING_CLAIMS", "US_NFP"} and number >= 1_000:
            number /= 1_000
    return number if isfinite(number) else None


def _event_id(event: dict) -> str:
    url = str(event.get("url") or "").strip()
    path = urlparse(url).path.strip("/")
    if path.startswith("event/"):
        path = path.removeprefix("event/")
    source_id = path or str(event.get("id") or "").strip()
    if source_id:
        return source_id
    identity = "|".join(
        str(event.get(field) or "") for field in ("date", "time_utc", "title", "name")
    )
    if identity.strip("|"):
        return "generated:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    raise ValueError("FinanceCalendar event has no stable identifying fields.")


def _has_value(value: object) -> bool:
    return value is not None and str(value).strip().lower() not in {
        "",
        "-",
        "—",
        "n/a",
        "null",
    }


def _fetch_events(start: datetime, end: datetime) -> tuple[list[dict], dict]:
    response = http.request(
        "GET",
        CALENDAR_URL,
        params={
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
            "limit": 500,
        },
        timeout=30,
        cache_seconds=300,
    )
    body = response.json()
    events = body.get("events") if isinstance(body, dict) else None
    if not isinstance(events, list):
        raise TypeError("FinanceCalendar response does not contain an events list.")
    return events, body.get("attribution") if isinstance(body.get("attribution"), dict) else {}


def collect(conn, *, days_back: int = 7, days_ahead: int = 45) -> int:
    if not 0 <= days_back <= 92 or not 0 <= days_ahead <= 92 or days_back + days_ahead > 92:
        raise ValueError("FinanceCalendar date range must fit its documented 92-day maximum.")
    now = datetime.now(timezone.utc)
    events, attribution = _fetch_events(
        now - timedelta(days=days_back),
        now + timedelta(days=days_ahead),
    )
    retrieved = now.isoformat()
    changed = 0
    for event in events:
        if not isinstance(event, dict):
            raise TypeError("FinanceCalendar event entry is not an object.")
        event_id = _event_id(event)
        code = _event_code(event)
        release_time = event.get("time_utc")
        release_time_utc = iso_utc(release_time) if release_time else None
        period = str(event.get("period") or "").strip() or None
        forecast_text = event.get("consensus") if _has_value(event.get("consensus")) else None
        previous_text = event.get("prior") if _has_value(event.get("prior")) else None
        actual_text = event.get("actual") if _has_value(event.get("actual")) else None
        revised_text = (
            event.get("revised_previous")
            if _has_value(event.get("revised_previous"))
            else None
        )
        forecast = _number(forecast_text, code)
        previous = _number(previous_text, code)
        actual = _number(actual_text, code)
        revised_previous = _number(revised_text, code)
        impact = str(event.get("impact") or "").upper() or None
        release_status = "RELEASED" if _has_value(actual_text) else "SCHEDULED"
        raw_json = json.dumps(event, ensure_ascii=False, sort_keys=True)
        old = conn.execute(
            "SELECT * FROM calendar_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        old_payload = json.loads(old["raw_json"]) if old else {}
        is_revision = bool(
            old
            and any(
                old_payload.get(field) != event.get(field)
                for field in ("prior", "previous", "revised_previous", "revised_prior")
                if (
                    old_payload.get(field) != event.get(field)
                    and (
                        _has_value(old_payload.get(field))
                        or _has_value(event.get(field))
                    )
                )
            )
        )
        currency = (
            str(event["currency"]).strip()
            if _has_value(event.get("currency"))
            else None
        )
        country = (
            str(event["country"]).strip()
            if _has_value(event.get("country"))
            else None
        )
        provider_unit = (
            str(event["unit"]).strip()
            if _has_value(event.get("unit"))
            else None
        )
        effective_actual_text = (
            actual_text if _has_value(actual_text)
            else old["actual_text"] if old
            else None
        )
        validation_errors = [
            field
            for field, present in (
                ("currency", currency),
                ("country", country),
                ("period", period),
                ("unit", provider_unit),
                ("source_timestamp_utc", None),
            )
            if not present
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
              event_name=excluded.event_name, release_time_utc=excluded.release_time_utc,
              currency=COALESCE(excluded.currency, calendar_events.currency),
              country=COALESCE(excluded.country, calendar_events.country),
              period=COALESCE(excluded.period, calendar_events.period),
              forecast_text=COALESCE(excluded.forecast_text, calendar_events.forecast_text),
              forecast=COALESCE(excluded.forecast, calendar_events.forecast),
              previous_text=COALESCE(excluded.previous_text, calendar_events.previous_text),
              previous=COALESCE(excluded.previous, calendar_events.previous),
              actual_text=COALESCE(excluded.actual_text, calendar_events.actual_text),
              actual=COALESCE(excluded.actual, calendar_events.actual),
              revised_previous_text=COALESCE(
                excluded.revised_previous_text, calendar_events.revised_previous_text
              ),
              revised_previous=COALESCE(
                excluded.revised_previous, calendar_events.revised_previous
              ),
              unit=COALESCE(excluded.unit, calendar_events.unit),
              importance=COALESCE(excluded.importance, calendar_events.importance),
              retrieved_at_utc=excluded.retrieved_at_utc,
              release_status=excluded.release_status,
              is_revision=MAX(calendar_events.is_revision, excluded.is_revision),
              raw_json=excluded.raw_json
            """,
            (
                event_id,
                str(event.get("name") or event.get("title") or "Unknown event"),
                currency,
                country,
                release_time_utc,
                period,
                str(forecast_text) if forecast_text is not None else None,
                forecast,
                str(previous_text) if previous_text is not None else None,
                previous,
                str(actual_text) if actual_text is not None else None,
                actual,
                str(revised_text) if revised_text is not None else None,
                revised_previous,
                provider_unit,
                impact,
                SOURCE,
                str(event.get("url") or DOCS_URL),
                None,
                retrieved,
                "RELEASED" if _has_value(effective_actual_text) else release_status,
                int(is_revision),
                raw_json,
            ),
        )
        value_unit = BY_CODE[code].unit if code else provider_unit or "provider value"
        save_observation(
            conn,
            domain="economic",
            indicator="CALENDAR_EVENT",
            observation_period=event_id,
            value_text=str(event.get("title") or event.get("name") or ""),
            unit="event",
            source=SOURCE,
            source_url=str(event.get("url") or DOCS_URL),
            observed_utc=release_time_utc or retrieved,
            publication_status=release_status,
            metadata={
                "provider": SOURCE,
                "source_record_id": event_id,
                "source_timestamp_utc": None,
                "retrieved_at_utc": retrieved,
                "data_status": release_status,
                "is_revision": is_revision,
                "is_delayed": None,
                "validation_errors": validation_errors,
                "last_successful_sync_utc": retrieved,
                "event_code": code,
                "currency": currency,
                "country": country,
                "period": period,
                "forecast": forecast_text,
                "previous": previous_text,
                "actual": actual_text,
                "revised_previous": revised_text,
                "unit": provider_unit,
                "normalized_unit": value_unit,
                "importance": impact,
                "release_status": release_status,
                "all_day": event.get("all_day"),
            },
        )
        if code and release_time_utc:
            key_period = period or f"event:{event_id}"
            upsert_release(
                conn,
                event_code=code,
                period=key_period,
                release_time_utc=release_time_utc,
                previous=previous,
                forecast=forecast,
                actual=actual,
                revised_previous=revised_previous,
                actual_source=SOURCE if actual is not None else None,
                forecast_source=SOURCE if forecast is not None else None,
            )
            for field, raw, value in (
                ("FORECAST", forecast_text, forecast),
                ("PREVIOUS", previous_text, previous),
                ("ACTUAL", actual_text, actual),
                ("REVISED_PREVIOUS", revised_text, revised_previous),
            ):
                if raw is None:
                    continue
                save_observation(
                    conn,
                    domain="economic",
                    indicator=f"{code}_{field}",
                    observation_period=key_period,
                    value_numeric=value,
                    value_text=str(raw),
                    unit=value_unit,
                    source=SOURCE,
                    source_url=str(event.get("url") or DOCS_URL),
                    observed_utc=release_time_utc,
                    publication_status=release_status,
                    metadata={
                        "source_record_id": event_id,
                        "source_timestamp_utc": None,
                        "retrieved_at_utc": retrieved,
                        "data_status": release_status,
                        "is_revision": is_revision,
                        "is_delayed": None,
                        "validation_errors": [],
                        "attribution": attribution,
                    },
                )
        changed += 1
    conn.commit()
    return changed
