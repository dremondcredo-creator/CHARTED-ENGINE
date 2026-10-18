"""FRED observations for US GDP, effective rates and daily yields."""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import period_timestamp, save_observation
from cne.config import settings
from cne.db.database import upsert_release

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
DOCS_URL = "https://fred.stlouisfed.org/docs/api/fred/series_observations.html"
SOURCE = "Federal Reserve Bank of St. Louis · FRED"
POLL_INTERVAL_MINUTES = 6 * 60
PAGE_LIMIT = 1000

SERIES = (
    {
        "series_id": "A191RL1Q225SBEA",
        "indicator": "US_GDP_QOQ",
        "domain": "economic",
        "name": "Real GDP percent change from preceding quarter, annual rate",
        "unit": "%",
        "frequency": "quarterly",
        "release_code": "US_GDP_QOQ",
    },
    {
        "series_id": "DFF",
        "indicator": "FEDERAL_FUNDS_EFFECTIVE_RATE",
        "domain": "central_bank",
        "name": "Effective federal funds rate",
        "unit": "%",
        "frequency": "daily",
    },
    {
        "series_id": "DFEDTARL",
        "indicator": "FEDERAL_FUNDS_TARGET_LOWER",
        "domain": "central_bank",
        "name": "Federal funds target range - lower limit",
        "unit": "%",
        "frequency": "daily",
    },
    {
        "series_id": "DFEDTARU",
        "indicator": "FEDERAL_FUNDS_TARGET_UPPER",
        "domain": "central_bank",
        "name": "Federal funds target range - upper limit",
        "unit": "%",
        "frequency": "daily",
    },
    {
        "series_id": "DGS2",
        "indicator": "US2Y_FRED_DAILY",
        "domain": "market",
        "name": "Market yield on U.S. Treasury securities at 2-year constant maturity",
        "unit": "%",
        "frequency": "daily",
    },
    {
        "series_id": "DGS10",
        "indicator": "US10Y_FRED_DAILY",
        "domain": "market",
        "name": "Market yield on U.S. Treasury securities at 10-year constant maturity",
        "unit": "%",
        "frequency": "daily",
    },
    {
        "series_id": "DFII10",
        "indicator": "REAL10Y_FRED_DAILY",
        "domain": "market",
        "name": "Market yield on 10-year Treasury inflation-indexed securities",
        "unit": "%",
        "frequency": "daily",
    },
)


def _request(series_id: str) -> tuple[dict, str]:
    if not settings.fred_api_key:
        raise RuntimeError("FRED_API_KEY is not configured.")
    start = date(datetime.now(timezone.utc).year - settings.lookback_years, 1, 1)
    end = date.today()
    params = {
        "series_id": series_id,
        "api_key": settings.fred_api_key,
        "file_type": "json",
        "observation_start": start.isoformat(),
        "observation_end": end.isoformat(),
        "sort_order": "asc",
        "limit": str(PAGE_LIMIT),
        "offset": "0",
    }
    observations = []
    first_page = None
    offset = 0
    expected_count = None
    while expected_count is None or offset < expected_count:
        params["offset"] = str(offset)
        response = http.request(
            "GET",
            OBSERVATIONS_URL,
            params={**params},
            timeout=30,
            cache_seconds=POLL_INTERVAL_MINUTES * 60,
        )
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError(f"FRED response for {series_id} is not a JSON object.")
        if body.get("error_message"):
            message = str(body["error_message"])
            if settings.fred_api_key:
                message = message.replace(settings.fred_api_key, "[REDACTED]")
            raise ValueError(f"FRED {series_id}: {message}")
        page = body.get("observations")
        if not isinstance(page, list):
            raise TypeError(f"FRED response for {series_id} has no observations list.")
        if first_page is None:
            first_page = body
        count = body.get("count")
        if count is not None:
            try:
                expected_count = int(count)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"FRED response for {series_id} has an invalid count."
                ) from exc
        page_offset = body.get("offset", offset)
        try:
            page_offset = int(page_offset)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"FRED response for {series_id} has an invalid offset."
            ) from exc
        if page_offset != offset:
            raise ValueError(
                f"FRED response for {series_id} returned offset {page_offset}, "
                f"expected {offset}."
            )
        observations.extend(page)
        if expected_count is None:
            if len(page) < PAGE_LIMIT:
                break
            offset += len(page)
        else:
            offset += len(page)
            if offset < expected_count and not page:
                raise ValueError(
                    f"FRED response for {series_id} ended before count {expected_count}."
                )
    if first_page is None:
        raise ValueError(f"FRED returned no observation page for {series_id}.")
    body = {**first_page, "observations": observations}
    url = f"{OBSERVATIONS_URL}?series_id={series_id}&file_type=json"
    return body, url


def _period(row: dict, series: dict) -> str:
    observation_date = date.fromisoformat(str(row["date"]))
    if series["frequency"] == "quarterly":
        quarter = (observation_date.month - 1) // 3 + 1
        return f"{observation_date.year}-Q{quarter}"
    return observation_date.isoformat()


def _numeric_value(raw: object) -> float | None:
    if raw is None or raw == ".":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"FRED returned an invalid numeric observation: {raw!r}.") from exc
    if not isfinite(value):
        raise ValueError(f"FRED returned a non-finite numeric observation: {raw!r}.")
    return value


def _collect_series(conn, series: dict) -> int:
    body, url = _request(series["series_id"])
    retrieved = datetime.now(timezone.utc).isoformat()
    count = 0
    for row in body["observations"]:
        if not isinstance(row, dict) or not row.get("date"):
            raise TypeError(f"FRED returned a malformed {series['series_id']} observation.")
        value = _numeric_value(row.get("value"))
        if value is None:
            continue
        period = _period(row, series)
        vintage_start = row.get("realtime_start")
        vintage_end = row.get("realtime_end")
        save_observation(
            conn,
            domain=series["domain"],
            indicator=series["indicator"],
            observation_period=period,
            value_numeric=value,
            unit=series["unit"],
            source=SOURCE,
            source_url=url,
            observed_utc=period_timestamp(period),
            published_utc=None,
            publication_status="PUBLISHED",
            metadata={
                "provider": SOURCE,
                "source_record_id": f"{series['series_id']}:{period}:{vintage_start or ''}",
                "series_id": series["series_id"],
                "series_name": series["name"],
                "frequency": series["frequency"],
                "source_timestamp_utc": None,
                "source_vintage_start": vintage_start,
                "source_vintage_end": vintage_end,
                "retrieved_at_utc": retrieved,
                "data_status": "PUBLISHED",
                "is_revision": False,
                "is_delayed": series["frequency"] in {"daily", "quarterly"},
                "delay_class": series["frequency"].upper(),
                "timeframe": series["frequency"],
                "intraday_confirmation_eligible": False,
                "validation_errors": [],
            },
        )
        if series.get("release_code"):
            upsert_release(
                conn,
                event_code=series["release_code"],
                period=period,
                actual=value,
                actual_source=SOURCE,
            )
        count += 1
    return count


def collect(conn) -> int:
    if not settings.fred_api_key:
        raise RuntimeError("FRED_API_KEY is not configured.")
    total = 0
    for series in SERIES:
        total += _collect_series(conn, series)
    conn.commit()
    return total
