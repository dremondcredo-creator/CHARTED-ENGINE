"""BLS API collector — actuals for US labour/inflation."""
from __future__ import annotations

from datetime import datetime, timezone

from cne.collectors import http
from cne.collectors.observations import period_timestamp, save_observation
from cne.config import settings
from cne.db.database import upsert_release

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES = {
    "CPI_SA":      "CUSR0000SA0",
    "CORE_CPI_SA": "CUSR0000SA0L1E",
    "PPI_FD_SA":   "WPSFD4",
    "CORE_PPI_SA": "WPSFD4131",
    "NFP_LEVEL":   "CES0000000001",
    "UNEMP_RATE":  "LNS14000000",
    "AHE_LEVEL":   "CES0500000003",
}
SERIES_UNITS = {
    "CPI_SA": "index",
    "CORE_CPI_SA": "index",
    "PPI_FD_SA": "index",
    "CORE_PPI_SA": "index",
    "NFP_LEVEL": "K",
    "UNEMP_RATE": "%",
    "AHE_LEVEL": "USD/hour",
}
DERIVED_FROM = {
    "US_CPI_YOY": "CPI_SA",
    "US_CPI_MOM": "CPI_SA",
    "US_CORE_CPI_YOY": "CORE_CPI_SA",
    "US_CORE_CPI_MOM": "CORE_CPI_SA",
    "US_PPI_YOY": "PPI_FD_SA",
    "US_CORE_PPI_YOY": "CORE_PPI_SA",
    "US_NFP": "NFP_LEVEL",
    "US_UNEMPLOYMENT": "UNEMP_RATE",
    "US_AHE_MOM": "AHE_LEVEL",
}
TRANSFORMATIONS = {
    "US_CPI_YOY": "12-month percentage change",
    "US_CPI_MOM": "1-month percentage change",
    "US_CORE_CPI_YOY": "12-month percentage change",
    "US_CORE_CPI_MOM": "1-month percentage change",
    "US_PPI_YOY": "12-month percentage change",
    "US_CORE_PPI_YOY": "12-month percentage change",
    "US_NFP": "change in monthly payroll level",
    "US_UNEMPLOYMENT": "published level",
    "US_AHE_MOM": "1-month percentage change",
}


def is_configured():
    return True


def _post(series_ids, start, end):
    payload = {"seriesid": series_ids, "startyear": str(start), "endyear": str(end)}
    body = http.request("POST", BLS_URL, json_body=payload, timeout=30).json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {body.get('message')}")
    return body["Results"]["series"]


def _to_monthly(data):
    out = []
    for d in data:
        if not d["period"].startswith("M"):
            continue
        month = int(d["period"][1:])
        if not 1 <= month <= 12:
            continue
        try:
            val = float(d["value"])
        except (TypeError, ValueError):
            continue
        out.append((f"{int(d['year']):04d}-{int(d['period'][1:]):02d}", val))
    out.sort(key=lambda x: x[0])
    return out


def _pct_change(series, months):
    out = {}
    for i in range(months, len(series)):
        p, v = series[i]
        _, vp = series[i - months]
        if vp: out[p] = (v / vp - 1) * 100
    return out


def _diff(series):
    out = {}
    for i in range(1, len(series)):
        p, v = series[i]
        _, vp = series[i - 1]
        out[p] = v - vp
    return out


def collect(conn):
    before = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
    year = datetime.now(timezone.utc).year
    series_list = _post(list(SERIES.values()), year - settings.lookback_years, year)
    by_id = {s["seriesID"]: s["data"] for s in series_list}

    monthly_cache = {}

    def monthly(key):
        if key in monthly_cache:
            return monthly_cache[key]
        rows = _to_monthly(by_id[SERIES[key]])
        for period, value in rows:
            save_observation(
                conn,
                domain="economic",
                indicator=key,
                observation_period=period,
                value_numeric=value,
                unit=SERIES_UNITS[key],
                source="BLS Public Data API",
                source_url=BLS_URL,
                observed_utc=period_timestamp(f"{period}-01"),
                publication_status="PUBLISHED",
                metadata={
                    "series_id": SERIES[key],
                    "period_type": "monthly",
                    "publication_time_available": False,
                },
            )
        monthly_cache[key] = rows
        return rows

    def save_release(event_code, period, value):
        actual = round(value, 1 if event_code == "US_NFP" else 2)
        upsert_release(
            conn,
            event_code=event_code,
            period=period,
            actual=actual,
            actual_source="BLS Public Data API",
        )
        save_observation(
            conn,
            domain="economic",
            indicator=event_code,
            observation_period=period,
            value_numeric=actual,
            unit={
                "US_NFP": "K",
                "US_CPI_YOY": "%",
                "US_CPI_MOM": "%",
                "US_CORE_CPI_YOY": "%",
                "US_CORE_CPI_MOM": "%",
                "US_PPI_YOY": "%",
                "US_CORE_PPI_YOY": "%",
                "US_UNEMPLOYMENT": "%",
                "US_AHE_MOM": "%",
            }[event_code],
            source="BLS Public Data API (derived)",
            source_url=BLS_URL,
            observed_utc=period_timestamp(f"{period}-01"),
            publication_status="PUBLISHED",
            metadata={
                "derived_from_series": SERIES[DERIVED_FROM[event_code]],
                "transformation": TRANSFORMATIONS[event_code],
                "publication_time_available": False,
            },
        )

    releases = (
        ("US_CPI_YOY", _pct_change(monthly("CPI_SA"), 12)),
        ("US_CPI_MOM", _pct_change(monthly("CPI_SA"), 1)),
        ("US_CORE_CPI_YOY", _pct_change(monthly("CORE_CPI_SA"), 12)),
        ("US_CORE_CPI_MOM", _pct_change(monthly("CORE_CPI_SA"), 1)),
        ("US_PPI_YOY", _pct_change(monthly("PPI_FD_SA"), 12)),
        ("US_CORE_PPI_YOY", _pct_change(monthly("CORE_PPI_SA"), 12)),
        ("US_NFP", _diff(monthly("NFP_LEVEL"))),
        ("US_UNEMPLOYMENT", dict(monthly("UNEMP_RATE"))),
        ("US_AHE_MOM", _pct_change(monthly("AHE_LEVEL"), 1)),
    )
    for event_code, values in releases:
        for period, value in values.items():
            save_release(event_code, period, value)

    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
    return after - before