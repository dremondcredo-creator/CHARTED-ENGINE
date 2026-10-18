"""Keyless official Treasury daily nominal and real yield curve data."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from cne.collectors import http
from cne.collectors.observations import period_timestamp, save_observation

BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
)
SOURCE_NOMINAL = "US Treasury Daily Treasury Par Yield Curve"
SOURCE_REAL = "US Treasury Daily Treasury Par Real Yield Curve"
DOCS_URL = "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics"


def endpoint(year: int, curve_type: str) -> str:
    return BASE_URL.format(year=year) + (
        f"?type={curve_type}&field_tdr_date_value={year}&page&_format=csv"
    )


def _csv_rows(url: str) -> list[dict[str, str]]:
    response = http.request("GET", url, timeout=30, cache_seconds=6 * 60 * 60)
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not rows or "Date" not in rows[0]:
        raise ValueError(f"Treasury CSV response has no dated yield rows: {url}")
    return rows


def _number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid Treasury yield value {value!r}.") from exc
    if not 0 < number < 100:
        raise ValueError(f"Treasury yield is outside the accepted range: {number}.")
    return number


def _save_curve(conn, rows: list[dict[str, str]], columns: dict[str, str], source: str, url: str) -> int:
    count = 0
    for row in rows:
        date = datetime.strptime(row["Date"], "%m/%d/%Y").date().isoformat()
        for indicator, column in columns.items():
            value = _number(row.get(column))
            if value is None:
                continue
            save_observation(
                conn,
                domain="market",
                indicator=indicator,
                observation_period=date,
                value_numeric=value,
                unit="%",
                source=source,
                source_url=url,
                observed_utc=period_timestamp(date),
                publication_status="DAILY",
                metadata={
                    "timeframe": "daily",
                    "frequency": "daily",
                    "measurement": "indicative closing market bid yield",
                    "source_timestamp_utc": None,
                    "is_delayed": True,
                    "intraday_confirmation_eligible": False,
                    "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            count += 1
    return count


def collect(conn) -> int:
    year = datetime.now(timezone.utc).year
    nominal_url = endpoint(year, "daily_treasury_yield_curve")
    real_url = endpoint(year, "daily_treasury_real_yield_curve")
    nominal = _csv_rows(nominal_url)
    real = _csv_rows(real_url)
    total = _save_curve(
        conn,
        nominal,
        {"US2Y_DAILY": "2 Yr", "US10Y_DAILY": "10 Yr"},
        SOURCE_NOMINAL,
        nominal_url,
    )
    total += _save_curve(
        conn,
        real,
        {"REAL10Y_DAILY": "10 YR"},
        SOURCE_REAL,
        real_url,
    )
    conn.commit()
    return total
