"""Keyless Eurostat dataset connector for euro-area GDP and employment."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import iso_utc, period_timestamp, save_observation
from cne.db.database import upsert_release

BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
SOURCE = "Eurostat public data API"
DATASETS = {
    "gdp": (
        "namq_10_gdp",
        {
            "freq": "Q",
            "unit": "CLV_PCH_PRE",
            "s_adj": "SCA",
            "na_item": "B1GQ",
            "geo": "EA21",
            "lastTimePeriod": "12",
        },
    ),
    "employment": (
        "namq_10_a10_e",
        {
            "freq": "Q",
            "unit": "THS_PER",
            "nace_r2": "TOTAL",
            "s_adj": "SCA",
            "na_item": "EMP_DC",
            "geo": "EA21",
            "lastTimePeriod": "13",
        },
    ),
    "hicp": (
        "prc_hicp_manr",
        {
            "freq": "M",
            "unit": "RCH_A",
            "coicop": "CP00",
            "geo": "EA20",
            "lastTimePeriod": "24",
        },
    ),
}


def _fetch(dataset: str, params: dict[str, str]) -> dict:
    response = http.request(
        "GET",
        BASE_URL + dataset,
        params=params,
        timeout=30,
        cache_seconds=6 * 60 * 60,
    )
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("dimension"), dict):
        raise TypeError(f"Eurostat returned invalid JSON-stat data for {dataset}.")
    return body


def _flat_index(body: dict, time_code: str) -> int:
    dimensions = body["dimension"]
    ids = body["id"]
    sizes = body["size"]
    indices = []
    for dimension in ids:
        category = dimensions[dimension]["category"]["index"]
        if dimension == "time":
            indices.append(category[time_code])
        elif len(category) == 1:
            indices.append(next(iter(category.values())))
        else:
            raise ValueError(
                f"Eurostat query returned unfiltered dimension {dimension}."
            )
    flat = 0
    for position, index in enumerate(indices):
        stride = 1
        for size in sizes[position + 1 :]:
            stride *= size
        flat += index * stride
    return flat


def _values(body: dict) -> list[tuple[str, float]]:
    values = body.get("value")
    if not isinstance(values, dict):
        raise TypeError("Eurostat response contains no numeric values.")
    time_codes = body["dimension"]["time"]["category"]["index"]
    results = []
    for period in time_codes:
        raw = values.get(str(_flat_index(body, period)))
        if raw is None:
            continue
        value = float(raw)
        if not isfinite(value):
            continue
        results.append((period, value))
    return sorted(results)


def _source_updated(body: dict) -> str | None:
    updated = body.get("updated")
    try:
        return iso_utc(str(updated)) if updated else None
    except (TypeError, ValueError):
        return None


def _quarter_index(period: str) -> int:
    year_text, quarter_text = period.split("-Q", maxsplit=1)
    return int(year_text) * 4 + int(quarter_text)


def _save_series(
    conn,
    *,
    indicator: str,
    values: list[tuple[str, float]],
    unit: str,
    dataset: str,
    source_updated: str | None,
) -> None:
    for period, value in values:
        save_observation(
            conn,
            domain="economic",
            indicator=indicator,
            observation_period=period,
            value_numeric=value,
            unit=unit,
            source=SOURCE,
            source_url=BASE_URL + dataset,
            observed_utc=period_timestamp(period),
            published_utc=None,
            publication_status="PUBLISHED",
            metadata={
                "dataset": dataset,
                "source_timestamp_utc": None,
                "dataset_updated_utc": source_updated,
                "timeframe": (
                    "monthly" if dataset == "prc_hicp_manr" else "quarterly"
                ),
                "geography": "EA20" if dataset == "prc_hicp_manr" else "EA21",
                "is_delayed": True,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )


def collect(conn) -> int:
    gdp_dataset, gdp_params = DATASETS["gdp"]
    employment_dataset, employment_params = DATASETS["employment"]
    hicp_dataset, hicp_params = DATASETS["hicp"]
    gdp_body = _fetch(gdp_dataset, gdp_params)
    employment_body = _fetch(employment_dataset, employment_params)
    hicp_body = _fetch(hicp_dataset, hicp_params)
    gdp_values = _values(gdp_body)
    employment_levels = _values(employment_body)
    hicp_values = _values(hicp_body)
    _save_series(
        conn,
        indicator="EU_GDP_QOQ",
        values=gdp_values,
        unit="%",
        dataset=gdp_dataset,
        source_updated=_source_updated(gdp_body),
    )

    employment_changes = []
    for (period, value), (previous_period, previous) in zip(
        employment_levels[1:], employment_levels
    ):
        if _quarter_index(period) - _quarter_index(previous_period) != 1 or previous == 0:
            continue
        employment_changes.append((period, (value / previous - 1) * 100))
    _save_series(
        conn,
        indicator="EU_EMPLOYMENT_LEVEL",
        values=employment_levels,
        unit="thousand persons",
        dataset=employment_dataset,
        source_updated=_source_updated(employment_body),
    )
    _save_series(
        conn,
        indicator="EU_EMPLOYMENT_QOQ",
        values=employment_changes,
        unit="%",
        dataset=employment_dataset,
        source_updated=_source_updated(employment_body),
    )
    _save_series(
        conn,
        indicator="EU_CPI_YOY",
        values=hicp_values,
        unit="% y/y",
        dataset=hicp_dataset,
        source_updated=_source_updated(hicp_body),
    )
    for event_code, values in (
        ("EU_GDP_QOQ", gdp_values),
        ("EU_EMPLOYMENT_QOQ", employment_changes),
        ("EU_CPI_YOY", hicp_values),
    ):
        for period, value in values:
            upsert_release(
                conn,
                event_code=event_code,
                period=period,
                actual=round(value, 4),
                actual_source=SOURCE,
            )
    conn.commit()
    return (
        len(gdp_values)
        + len(employment_levels)
        + len(employment_changes)
        + len(hicp_values)
    )
