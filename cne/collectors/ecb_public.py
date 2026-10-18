"""Keyless ECB Data Portal daily policy-rate and FX reference feeds."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import period_timestamp, save_observation

BASE_URL = "https://data-api.ecb.europa.eu/service/data/"
SOURCE = "European Central Bank Data Portal"
POLICY_SERIES = "FM/D.U2.EUR.4F.KR.DFR.LEV"
FX_SERIES = "EXR/D.USD.EUR.SP00.A"
DOCS_URL = "https://data.ecb.europa.eu/help/api/overview"


def _fetch_csv(series: str, last_observations: int = 60) -> tuple[list[dict[str, str]], str]:
    url = BASE_URL + series
    response = http.request(
        "GET",
        url,
        params={
            "format": "csvdata",
            "lastNObservations": str(last_observations),
        },
        timeout=30,
        cache_seconds=6 * 60 * 60,
    )
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    if not rows or "TIME_PERIOD" not in rows[0] or "OBS_VALUE" not in rows[0]:
        raise ValueError(f"ECB Data Portal returned invalid CSV for {series}.")
    return rows, response.url


def _valid_value(raw: str) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def collect_policy_rate(conn) -> int:
    rows, url = _fetch_csv(POLICY_SERIES, 60)
    selected = rows[-1:]
    for row in selected:
        value = _valid_value(row["OBS_VALUE"])
        if value is None:
            raise ValueError("ECB deposit facility rate is not numeric.")
        period = row["TIME_PERIOD"]
        save_observation(
            conn,
            domain="central_bank",
            indicator="ECB_DEPOSIT_FACILITY_RATE",
            observation_period=period,
            value_numeric=value,
            unit="%",
            source=SOURCE,
            source_url=url,
            observed_utc=period_timestamp(period),
            publication_status="DAILY",
            metadata={
                "series_id": POLICY_SERIES,
                "source_timestamp_utc": None,
                "timeframe": "daily",
                "is_delayed": True,
                "intraday_confirmation_eligible": False,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
    conn.commit()
    return len(selected)


def collect_fx_reference(conn) -> int:
    rows, url = _fetch_csv(FX_SERIES, 90)
    written = 0
    for row in rows:
        value = _valid_value(row["OBS_VALUE"])
        if value is None:
            continue
        period = row["TIME_PERIOD"]
        save_observation(
            conn,
            domain="market",
            indicator="EURUSD_ECB_REFERENCE",
            observation_period=period,
            value_numeric=value,
            unit="USD per EUR (ECB reference rate)",
            source=SOURCE,
            source_url=url,
            observed_utc=period_timestamp(period),
            publication_status="DAILY_REFERENCE",
            metadata={
                "series_id": FX_SERIES,
                "title": row.get("TITLE_COMPL"),
                "fixing_time_local": "14:15 CET/CEST",
                "source_timestamp_utc": None,
                "timeframe": "daily",
                "is_delayed": True,
                "intraday_confirmation_eligible": False,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        written += 1
    conn.commit()
    return written
