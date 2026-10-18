"""Bureau of Economic Analysis NIPA real GDP observations."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from math import isfinite

import requests

from cne.collectors import http
from cne.collectors.observations import period_timestamp, save_observation
from cne.config import settings
from cne.db.database import upsert_release

DATA_URL = "https://apps.bea.gov/api/data/"
DOCS_URL = "https://apps.bea.gov/api/"
SOURCE = "U.S. Bureau of Economic Analysis · NIPA Table 1.1.1"
POLL_INTERVAL_MINUTES = 6 * 60
TABLE_NAME = "T10101"
GDP_LINE = "1"


def is_configured() -> bool:
    return bool(settings.bea_api_key)


def _years() -> str:
    current = datetime.now(timezone.utc).year
    return ",".join(
        str(year)
        for year in range(current - settings.lookback_years, current + 1)
    )


def _request() -> tuple[list[dict], str]:
    if not settings.bea_api_key:
        raise RuntimeError("BEA_API_KEY is not configured.")
    params = {
        "UserID": settings.bea_api_key,
        "method": "GetData",
        "datasetname": "NIPA",
        "TableName": TABLE_NAME,
        "Frequency": "Q",
        "Year": _years(),
        "ResultFormat": "JSON",
    }
    response = http.request(
        "GET",
        DATA_URL,
        params=params,
        timeout=30,
        cache_seconds=POLL_INTERVAL_MINUTES * 60,
    )
    try:
        body = response.json()
    except (ValueError, requests.exceptions.JSONDecodeError) as exc:
        raise ValueError(
            f"BEA returned a non-JSON response: {response.text[:300]!r}."
        ) from exc
    if not isinstance(body, dict):
        raise TypeError("BEA response is not a JSON object.")
    api = body.get("BEAAPI")
    results = api.get("Results") if isinstance(api, dict) else None
    if not isinstance(results, dict):
        raise TypeError("BEA response has no BEAAPI.Results object.")
    error = results.get("Error")
    if error:
        description = (
            error.get("APIErrorDescription")
            if isinstance(error, dict)
            else str(error)
        )
        description = str(description or "unspecified provider error")
        description = description.replace(settings.bea_api_key, "[REDACTED]")
        raise ValueError(f"BEA API: {description}")
    rows = results.get("Data")
    if not isinstance(rows, list):
        raise TypeError("BEA response has no Results.Data list.")
    url = f"{DATA_URL}?method=GetData&datasetname=NIPA&TableName={TABLE_NAME}"
    return rows, url


def _period(raw: object) -> str:
    match = re.fullmatch(r"(\d{4})Q([1-4])", str(raw or "").strip())
    if not match:
        raise ValueError(f"BEA returned an invalid quarterly period: {raw!r}.")
    return f"{match.group(1)}-Q{match.group(2)}"


def _number(raw: object) -> float | None:
    text = str(raw or "").strip()
    if not text or text in {"--", "n/a", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    normalized = text[1:-1] if negative else text
    try:
        value = float(normalized.replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"BEA returned a non-numeric GDP value: {raw!r}.") from exc
    if not isfinite(value):
        raise ValueError(f"BEA returned a non-finite GDP value: {raw!r}.")
    return -value if negative else value


def collect(conn) -> int:
    rows, source_url = _request()
    retrieved = datetime.now(timezone.utc).isoformat()
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("BEA Results.Data contains a non-object entry.")
        if str(row.get("LineNumber", "")).strip() != GDP_LINE:
            continue
        description = str(row.get("LineDescription", "")).strip().lower()
        if description and description != "gross domestic product":
            continue
        period = _period(row.get("TimePeriod"))
        actual = _number(row.get("DataValue"))
        if actual is None:
            continue
        selected.append((period, actual))
    if not selected:
        raise ValueError(
            "BEA NIPA response contains no numeric real-GDP observations "
            "for table T10101 line 1."
        )

    for period, actual in selected:
        save_observation(
            conn,
            domain="economic",
            indicator="US_GDP_QOQ_BEA",
            observation_period=period,
            value_numeric=actual,
            unit="% (annualized quarter-over-quarter)",
            source=SOURCE,
            source_url=source_url,
            observed_utc=period_timestamp(period),
            published_utc=None,
            publication_status="PUBLISHED",
            metadata={
                "provider": SOURCE,
                "source_record_id": f"NIPA:{TABLE_NAME}:Q:{period}",
                "source_timestamp_utc": None,
                "retrieved_at_utc": retrieved,
                "data_status": "PUBLISHED",
                "is_revision": False,
                "is_delayed": True,
                "timeframe": "quarterly",
                "table_name": TABLE_NAME,
                "line_number": GDP_LINE,
                "validation_errors": [],
                "intraday_confirmation_eligible": False,
            },
        )
        upsert_release(
            conn,
            event_code="US_GDP_QOQ",
            period=period,
            actual=actual,
            actual_source=SOURCE,
        )
    conn.commit()
    return len(selected)
