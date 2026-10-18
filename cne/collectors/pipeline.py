"""Workflow 01 collection orchestration and source coverage."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

from cne.collectors import (
    alpha_vantage,
    bea,
    biquote,
    biquote_calendar,
    bls,
    calendar,
    ecb_public,
    eurostat,
    finance_calendar,
    fred,
    gold_api,
    news,
    treasury,
)
from cne.config import settings

_INTERVALS_MINUTES = {
    "biquote_calendar": biquote_calendar.POLL_INTERVAL_MINUTES,
    "financecalendar": 5,
    "calendar_csv": 5,
    "bls": 24 * 60,
    "treasury": 6 * 60,
    "eurostat": 6 * 60,
    "ecb_policy": 6 * 60,
    "ecb_fx": 6 * 60,
    "gold_quote": 5,
    "biquote": biquote.POLL_INTERVAL_MINUTES,
    "alpha_vantage": alpha_vantage.POLL_INTERVAL_MINUTES,
    "fred": fred.POLL_INTERVAL_MINUTES,
    "bea": bea.POLL_INTERVAL_MINUTES,
    "bank_news": 60,
}

SOURCE_CATALOG = (
    {
        "id": "biquote_calendar",
        "name": "Economic calendar & consensus",
        "domain": "economic",
        "provider": "BiQuote economic calendar",
        "source_url": (
            "https://biquote.io/api/calendar?from={UTC_DATE}&to={UTC_DATE}"
            "&countries=US,EU&limit=500"
        ),
        "docs_url": "https://biquote.io/docs/",
        "terms_url": "https://biquote.io/terms.html",
        "access_requirements": "Public GET endpoint; no key, signup, or authentication.",
        "fields_collected": [
            "Event IDs and names",
            "Country, currency, scheduled UTC time, reporting period, importance, units",
            "Forecast, previous, actual, revised previous, upstream publisher and source URL",
            "Provider record ID, retrieval timestamp, missing-field and revision status",
        ],
        "frequency": "Every 5 minutes; provider edge cache is 5 minutes",
        "max_age_minutes": 30,
        "missing_fields": [
            "Provider does not supply an exact source publication timestamp",
            "Forecasts, actuals, and revisions may be null for individual events",
            "Calendar coverage and consensus are upstream MQL5 data, not official releases",
        ],
        "limitation": (
            "No key or signup. The source may change or delay data and provides no SLA; "
            "the 500-row endpoint cap is handled by splitting capped date ranges. "
            "Forecast values are never filled when absent. Calendar data is upstream, "
            "not an official release source."
        ),
    },
    {
        "id": "financecalendar",
        "name": "Economic calendar fallback",
        "domain": "economic",
        "provider": "FinanceCalendar public API",
        "source_url": "https://www.financecalendar.com/wp-json/fc/v1/calendar",
        "docs_url": "https://www.financecalendar.com/api/",
        "terms_url": "https://www.financecalendar.com/api/",
        "access_requirements": (
            "Public endpoint; no API key. Visible provider attribution is required."
        ),
        "fields_collected": [
            "Event name, scheduled UTC time, forecast, previous, actual, impact and URL"
        ],
        "frequency": "Only when BiQuote calendar collection fails",
        "max_age_minutes": 30,
        "missing_fields": [
            "Currency and country are not exposed by this API",
            "Data period, units and source publication timestamps are often absent",
            "Forecast and actual may be null; revisions are not exposed",
        ],
        "limitation": (
            "No API key; 92-day date-range limit and visible attribution link required. "
            "Fallback events retain their separate source IDs and are not merged with "
            "BiQuote provider records."
        ),
    },
    {
        "id": "calendar_csv",
        "name": "Optional calendar fallback",
        "domain": "economic",
        "provider": "User-maintained data/calendar.csv",
        "source_url": "data/calendar.csv",
        "frequency": "Only after the public calendar endpoint fails",
        "max_age_minutes": None,
        "missing_fields": ["Automated forecast and actual updates"],
        "limitation": "Manual fallback only; it is not required while FinanceCalendar is reachable.",
    },
    {
        "id": "bls",
        "name": "Official US inflation & employment",
        "domain": "economic",
        "provider": "US Bureau of Labor Statistics Public Data API v2",
        "source_url": "https://api.bls.gov/publicAPI/v2/timeseries/data/",
        "frequency": "Daily (one grouped request; unauthenticated request quota applies)",
        "max_age_minutes": 36 * 60,
        "missing_fields": [
            "Forecast consensus",
            "Exact publication timestamp",
            "Historical revisions are not preserved as separate releases",
            "Initial/continuing claims and retail sales are not collected by this connector",
        ],
        "limitation": (
            "No API key; the public endpoint is rate-limited. Published period dates "
            "are not intraday release timestamps."
        ),
    },
    {
        "id": "treasury",
        "name": "US nominal & real Treasury yields",
        "domain": "market",
        "provider": "US Treasury daily yield curve CSV",
        "source_url": (
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            f"daily-treasury-rates.csv/{datetime.now(timezone.utc).year}/all"
            f"?type=daily_treasury_yield_curve&field_tdr_date_value="
            f"{datetime.now(timezone.utc).year}&page&_format=csv"
        ),
        "docs_url": "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics",
        "frequency": "Daily, collected every 6 hours",
        "max_age_minutes": 72 * 60,
        "missing_fields": [
            "Intraday yields and release-window snapshots",
            "Bid/ask, spread and volume",
        ],
        "limitation": (
            "Public CSV, no key; daily closing indicative bid yields only. Never used "
            "as minute-level market confirmation."
        ),
    },
    {
        "id": "eurostat",
        "name": "Euro-area GDP & employment",
        "domain": "economic",
        "provider": "Eurostat dissemination API",
        "source_url": (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
            "namq_10_gdp?freq=Q&unit=CLV_PCH_PRE&s_adj=SCA&na_item=B1GQ"
            "&geo=EA21&lastTimePeriod=12"
        ),
        "docs_url": "https://ec.europa.eu/eurostat/web/main/data/web-services",
        "frequency": "Every 6 hours; source series are quarterly",
        "max_age_minutes": 72 * 60,
        "missing_fields": [
            "Consensus forecasts",
            "Exact publication timestamps",
            (
                "Current EA21 HICP observations; the verified HICP dataset currently "
                "returns EA20 only, with its latest reporting periods ending in 2025"
            ),
        ],
        "limitation": (
            "Official keyless JSON-stat endpoints. Employment requires SCA adjustment "
            "to return observations. HICP uses the EA20 aggregate because the live "
            "Eurostat API returns no EA21 observations for the selected dataset."
        ),
    },
    {
        "id": "ecb_policy",
        "name": "ECB deposit facility rate",
        "domain": "central_bank",
        "provider": "ECB Data Portal SDMX API",
        "source_url": (
            "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"
            "?format=csvdata&lastNObservations=60"
        ),
        "docs_url": "https://data.ecb.europa.eu/help/api/overview",
        "frequency": "Every 6 hours; daily series",
        "max_age_minutes": 72 * 60,
        "missing_fields": ["Market-implied rate expectations", "Intraday policy repricing"],
        "limitation": "Official no-key daily series; not a market-expectations feed.",
    },
    {
        "id": "ecb_fx",
        "name": "EUR/USD reference rate",
        "domain": "market",
        "provider": "ECB Data Portal reference exchange rate",
        "source_url": (
            "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
            "?format=csvdata&lastNObservations=90"
        ),
        "docs_url": "https://data.ecb.europa.eu/help/api/overview",
        "frequency": "Daily reference fixing, checked every 6 hours",
        "max_age_minutes": 72 * 60,
        "missing_fields": [
            "Intraday EUR/USD quote",
            "Bid/ask, OHLC, spread and volume",
        ],
        "limitation": (
            "Official daily reference fixing (around 14:15 CET/CEST); excluded from "
            "release-window market confirmation."
        ),
    },
    {
        "id": "gold_quote",
        "name": "XAU/USD spot quote",
        "domain": "market",
        "provider": "Gold API",
        "source_url": "https://api.gold-api.com/price/XAU",
        "docs_url": "https://gold-api.com/docs",
        "frequency": "Every 5 minutes; endpoint documents no authentication",
        "max_age_minutes": 15,
        "missing_fields": ["Bid/ask, OHLC, spread, volume and independently verified latency"],
        "limitation": (
            "Free quote endpoint; provider disclaims accuracy guarantees. Latency is "
            "unverified, so quote is context only and never live confirmation."
        ),
    },
    {
        "id": "alpha_vantage",
        "name": "Alpha Vantage FX & gold",
        "domain": "market",
        "provider": "Alpha Vantage market-data API",
        "source_url": "https://www.alphavantage.co/query",
        "docs_url": "https://www.alphavantage.co/documentation/",
        "frequency": (
            "Every 6 hours; 3 API calls per sync (up to 12 requests/day)"
        ),
        "max_age_minutes": 12 * 60,
        "missing_fields": [
            "Intraday FX candles (documented as premium)",
            "DXY and intraday Treasury yields",
            "FX volume; spot quote latency is not independently verified",
        ],
        "limitation": (
            "Optional key required. Standard free service is limited to 25 API "
            "requests/day; quotes and daily bars are context, not event-window "
            "confirmation or trade signals."
        ),
    },
    {
        "id": "bank_news",
        "name": "Fed & ECB publications",
        "domain": "news",
        "provider": "Federal Reserve and European Central Bank RSS",
        "source_url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "docs_url": "https://www.federalreserve.gov/feeds/feeds.htm",
        "frequency": "Hourly",
        "max_age_minutes": 3 * 60,
        "missing_fields": ["Meeting probabilities and quantitative policy expectations"],
        "limitation": "Official RSS context, not a structured consensus or tone score.",
    },
    {
        "id": "biquote",
        "name": "Intraday market prices · Gold, EUR/USD & DXY",
        "domain": "market",
        "provider": "BiQuote indicative broker feed",
        "source_url": "https://biquote.io/api/latest?symbols=EURUSD&symbols=XAUUSD&symbols=DXY",
        "docs_url": "https://biquote.io/docs/",
        "frequency": "Latest quotes every 5 minutes; 1m–1d OHLC fetched as needed",
        "max_age_minutes": 15,
        "missing_fields": [
            "US 2Y, US 10Y and real-yield quotes",
            "Exchange-traded volume (provider volume is not reported)",
        ],
        "limitation": (
            "No key or signup required. Quotes are indicative broker prices with no "
            "SLA; closed or stale quotes are labeled and excluded from live confirmation. "
            "Not an official DXY benchmark or exchange-licensed feed."
        ),
    },
    {
        "id": "fed_policy",
        "name": "Federal Reserve policy rate",
        "domain": "central_bank",
        "provider": "FRED target-range series · DFEDTARL / DFEDTARU",
        "source_url": "https://api.stlouisfed.org/fred/series/observations",
        "docs_url": "https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        "frequency": "Daily FRED series; collected every 6 hours when configured",
        "max_age_minutes": 12 * 60,
        "missing_fields": [
            "Market-implied rate expectations and meeting probabilities",
            "Exact announcement time from these FRED series",
        ],
        "limitation": (
            "Optional FRED API key required. Target lower/upper series provide the "
            "range; Fed RSS remains the source for statements and announcement context."
        ),
    },
    {
        "id": "fred",
        "name": "FRED US macro, rates & yields",
        "domain": "economic",
        "provider": "Federal Reserve Bank of St. Louis · FRED API",
        "source_url": "https://api.stlouisfed.org/fred/series/observations",
        "docs_url": "https://fred.stlouisfed.org/docs/api/fred/series_observations.html",
        "frequency": "Every 6 hours; 7 series requests per sync, paginated when needed",
        "max_age_minutes": 12 * 60,
        "missing_fields": [
            "Consensus forecasts and exact release-publication timestamps",
            "Fed statement text and market-implied policy expectations",
            "Intraday yield quotes, bid/ask, spread and volume",
        ],
        "limitation": (
            "Requires a registered FRED API key. GDP is quarter-over-quarter "
            "annualized; DFF is the effective rate, while DFEDTARL/DFEDTARU are the "
            "target range. FRED yields/rates are daily or quarterly context."
        ),
    },
    {
        "id": "bea",
        "name": "BEA NIPA real GDP",
        "domain": "economic",
        "provider": "Bureau of Economic Analysis API · NIPA",
        "source_url": "https://apps.bea.gov/api/data/",
        "docs_url": "https://apps.bea.gov/api/",
        "frequency": "Every 6 hours when configured; quarterly series",
        "max_age_minutes": 120 * 24 * 60,
        "missing_fields": [
            "Consensus forecasts",
            "Exact release-publication timestamp",
            "Historical vintages as separate records",
        ],
        "limitation": (
            "Requires a registered BEA API UserID stored as BEA_API_KEY. Uses NIPA "
            "Table T10101, line 1. Not live-response-verified until a valid key is "
            "configured; the collector status remains NOT_CONFIGURED otherwise."
        ),
    },
    {
        "id": "intraday_yields",
        "name": "Intraday Treasury yields",
        "domain": "market",
        "provider": "No verified free public no-key endpoint",
        "source_url": "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics",
        "docs_url": "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics",
        "frequency": "Unavailable",
        "max_age_minutes": None,
        "missing_fields": ["Intraday US 2Y, US 10Y and real-yield quotes"],
        "limitation": "Verified Treasury CSV feeds are daily and cannot confirm minute-scale reactions.",
        "unavailable": True,
    },
)


def _safe_error(source: str, error: Exception) -> str:
    detail = re.sub(
        r"(?i)(api_key=|apikey=|userid=|[?&]c=)[^&\s]+",
        r"\1[REDACTED]",
        str(error),
    )
    return f"{type(error).__name__}: {detail[:500]}"


def _last_success(conn: sqlite3.Connection, source: str) -> str | None:
    row = conn.execute(
        """
        SELECT finished_utc FROM collection_runs
        WHERE source = ? AND status IN ('OK', 'FALLBACK')
        ORDER BY finished_utc DESC, id DESC LIMIT 1
        """,
        (source,),
    ).fetchone()
    return row["finished_utc"] if row else None


def _run_source(
    conn: sqlite3.Connection,
    source: str,
    collect,
    *,
    interval_minutes: int | None = None,
) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    last_success = _last_success(conn, source)
    if interval_minutes is not None and last_success:
        last_time = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - last_time).total_seconds() / 60
        if age_minutes < interval_minutes:
            return {
                "source": source,
                "status": "FRESH",
                "records": 0,
                "error": None,
                "finished_utc": started,
                "last_successful_sync_utc": last_success,
            }

    before = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
    try:
        records = int(collect(conn))
        status = "OK"
        error = None
    except Exception as exc:
        status = "ERROR"
        error = _safe_error(source, exc)
        after = conn.execute("SELECT COUNT(*) FROM data_observations").fetchone()[0]
        records = max(0, after - before)
    finished = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO collection_runs
          (source, started_utc, finished_utc, status, records, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source, started, finished, status, records, error),
    )
    conn.commit()
    return {
        "source": source,
        "status": status,
        "records": records,
        "error": error,
        "finished_utc": finished,
        "last_successful_sync_utc": _last_success(conn, source),
    }


def collect_all(conn: sqlite3.Connection) -> list[dict]:
    primary_calendar = _run_source(
        conn,
        "biquote_calendar",
        biquote_calendar.collect,
        interval_minutes=_INTERVALS_MINUTES["biquote_calendar"],
    )
    results = [primary_calendar]
    if primary_calendar["status"] == "ERROR":
        finance_fallback = _run_source(
            conn,
            "financecalendar",
            finance_calendar.collect,
            interval_minutes=_INTERVALS_MINUTES["financecalendar"],
        )
        if finance_fallback["status"] == "OK":
            finance_fallback["status"] = "FALLBACK"
        results.append(finance_fallback)
        if (
            finance_fallback["status"] == "ERROR"
            and calendar.settings.calendar_csv.exists()
        ):
            csv_fallback = _run_source(
                conn,
                "calendar_csv",
                calendar.collect,
                interval_minutes=_INTERVALS_MINUTES["calendar_csv"],
            )
            if csv_fallback["status"] == "OK":
                csv_fallback["status"] = "FALLBACK"
            results.append(csv_fallback)
    results.extend(
        (
            _run_source(conn, "bls", bls.collect, interval_minutes=_INTERVALS_MINUTES["bls"]),
            _run_source(conn, "treasury", treasury.collect, interval_minutes=_INTERVALS_MINUTES["treasury"]),
            _run_source(conn, "eurostat", eurostat.collect, interval_minutes=_INTERVALS_MINUTES["eurostat"]),
            _run_source(conn, "ecb_policy", ecb_public.collect_policy_rate, interval_minutes=_INTERVALS_MINUTES["ecb_policy"]),
            _run_source(conn, "ecb_fx", ecb_public.collect_fx_reference, interval_minutes=_INTERVALS_MINUTES["ecb_fx"]),
            _run_source(conn, "gold_quote", gold_api.collect, interval_minutes=_INTERVALS_MINUTES["gold_quote"]),
            _run_source(conn, "biquote", biquote.collect, interval_minutes=_INTERVALS_MINUTES["biquote"]),
            _run_source(conn, "bank_news", news.collect, interval_minutes=_INTERVALS_MINUTES["bank_news"]),
        )
    )
    if settings.fred_api_key:
        results.append(
            _run_source(
                conn,
                "fred",
                fred.collect,
                interval_minutes=_INTERVALS_MINUTES["fred"],
            )
        )
    if settings.bea_api_key:
        results.append(
            _run_source(
                conn,
                "bea",
                bea.collect,
                interval_minutes=_INTERVALS_MINUTES["bea"],
            )
        )
    if settings.alpha_vantage_api_key:
        results.append(
            _run_source(
                conn,
                "alpha_vantage",
                alpha_vantage.collect,
                interval_minutes=_INTERVALS_MINUTES["alpha_vantage"],
            )
        )
    results.append(_run_source(conn, "analysis", refresh_analysis))
    return results


def refresh_analysis(conn: sqlite3.Connection) -> int:
    from cne.core import macro, market, surprise

    updated = surprise.recompute_all(conn)
    market.run_all(conn)
    for country in ("US", "EU"):
        macro.persist_snapshot(conn, macro.compute_macro_score(conn, country, 30))
    return updated


def sync_release_data(conn: sqlite3.Connection) -> list[dict]:
    calendar_result = _run_source(
        conn,
        "biquote_calendar",
        biquote_calendar.collect,
        interval_minutes=_INTERVALS_MINUTES["biquote_calendar"],
    )
    runs = [calendar_result]
    if calendar_result["status"] == "ERROR":
        fallback = _run_source(
            conn,
            "financecalendar",
            lambda db: finance_calendar.collect(db, days_back=2, days_ahead=1),
            interval_minutes=_INTERVALS_MINUTES["financecalendar"],
        )
        if fallback["status"] == "OK":
            fallback["status"] = "FALLBACK"
        runs.append(fallback)
        if fallback["status"] == "ERROR" and calendar.settings.calendar_csv.exists():
            csv_fallback = _run_source(
                conn, "calendar_csv", calendar.collect,
                interval_minutes=_INTERVALS_MINUTES["calendar_csv"],
            )
            if csv_fallback["status"] == "OK":
                csv_fallback["status"] = "FALLBACK"
            runs.append(csv_fallback)
    runs.extend(
        (
            _run_source(
                conn, "bls", bls.collect, interval_minutes=_INTERVALS_MINUTES["bls"]
            ),
            _run_source(conn, "analysis", refresh_analysis),
        )
    )
    return runs


def _latest_run(conn: sqlite3.Connection, source_id: str) -> dict | None:
    row = conn.execute(
        """
        SELECT started_utc, finished_utc, status, records, error
        FROM collection_runs WHERE source = ?
        ORDER BY finished_utc DESC, id DESC LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["last_successful_sync_utc"] = _last_success(conn, source_id)
    return result


def _calendar_event_code(event: dict) -> str | None:
    source = str(event.get("source") or "")
    event_name = str(event.get("event_name") or "")
    if source.startswith(biquote_calendar.SOURCE):
        currency = str(event.get("currency") or "").upper()
        country = str(event.get("country") or "").upper()
        country_code = (
            "US" if currency == "USD" else
            "EU" if currency == "EUR" and ("EURO" in country or "EU" == country) else
            country
        )
        return biquote_calendar._event_code(country_code, event_name)
    if source.startswith(finance_calendar.SOURCE):
        return finance_calendar._event_code(
            {"name": event_name, "title": event_name}
        )
    return None


def coverage(conn: sqlite3.Connection) -> dict:
    now = datetime.now(timezone.utc)
    sources = []
    for item in SOURCE_CATALOG:
        run_source = "fred" if item["id"] == "fed_policy" else item["id"]
        latest_run = _latest_run(conn, run_source)
        if item.get("unavailable"):
            latest_run = {
                "started_utc": None,
                "finished_utc": None,
                "last_successful_sync_utc": None,
                "status": "UNAVAILABLE",
                "records": 0,
                "error": item["limitation"],
            }
        elif (
            item["id"] == "alpha_vantage" and not settings.alpha_vantage_api_key
            or item["id"] in {"fred", "fed_policy"} and not settings.fred_api_key
            or item["id"] == "bea" and not settings.bea_api_key
        ):
            if latest_run is None:
                latest_run = {
                    "started_utc": None,
                    "finished_utc": None,
                    "last_successful_sync_utc": None,
                    "status": "NOT_CONFIGURED",
                    "records": 0,
                    "error": None,
                }
            else:
                latest_run["status"] = "NOT_CONFIGURED"
                latest_run["error"] = None
        elif latest_run is None:
            latest_run = {
                "started_utc": None,
                "finished_utc": None,
                "last_successful_sync_utc": None,
                "status": "NOT_COLLECTED",
                "records": 0,
                "error": None,
            }
        max_age = item.get("max_age_minutes")
        last_success = latest_run.get("last_successful_sync_utc")
        if max_age is not None and last_success:
            age = (
                now - datetime.fromisoformat(last_success.replace("Z", "+00:00"))
            ).total_seconds() / 60
            latest_run["freshness_minutes"] = max(0, round(age))
            if age > max_age and latest_run["status"] not in {"ERROR", "UNAVAILABLE"}:
                latest_run["status"] = "STALE"
        else:
            latest_run["freshness_minutes"] = None
        latest_run["max_age_minutes"] = max_age
        sources.append({**item, "latest_run": latest_run})

    recent = [
        {
            **row,
            "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else None,
        }
        for row in conn.execute(
            """
            SELECT id, domain, indicator, observation_period, value_numeric,
                   value_text, unit, source, source_url, observed_utc,
                   published_utc, fetched_utc, publication_status, metadata_json
            FROM data_observations
            ORDER BY fetched_utc DESC, id DESC LIMIT 200
            """
        ).fetchall()
    ]
    all_events = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_id, event_name, currency, country, release_time_utc, period,
                   forecast_text, forecast, previous_text, previous, actual_text,
                   actual, revised_previous_text, revised_previous, unit, importance,
                   source, source_url, source_timestamp_utc, retrieved_at_utc,
                   release_status, is_revision
            FROM calendar_events
            ORDER BY COALESCE(release_time_utc, retrieved_at_utc) DESC LIMIT 100
            """
        ).fetchall()
    ]
    biquote_event_keys = {
        (event.get("release_time_utc"), _calendar_event_code(event))
        for event in all_events
        if str(event.get("source") or "").startswith(biquote_calendar.SOURCE)
        and event.get("release_time_utc")
        and _calendar_event_code(event)
    }
    events = [
        event
        for event in all_events
        if not (
            str(event.get("source") or "").startswith(finance_calendar.SOURCE)
            and event.get("release_time_utc")
            and _calendar_event_code(event)
            and (
                event.get("release_time_utc"),
                _calendar_event_code(event),
            )
            in biquote_event_keys
        )
    ]
    preparations = []
    for row in conn.execute(
        """
        SELECT id, event_key, window_minutes, release_id, event_name, currency,
               country, period, scheduled_release_utc, captured_at_utc, source,
               source_retrieved_at_utc, source_timestamp_utc, forecast, previous,
               revised_previous, is_revision, forecast_available_pre_release,
               data_quality_status, missing_fields_json, policy_context_json,
               fundamental_context_json, scenarios_json
        FROM pre_release_preparations
        ORDER BY scheduled_release_utc ASC, window_minutes DESC LIMIT 300
        """
    ).fetchall():
        preparation = dict(row)
        for column, target in (
            ("missing_fields_json", "missing_fields"),
            ("policy_context_json", "policy_context"),
            ("fundamental_context_json", "fundamental_context"),
            ("scenarios_json", "scenarios"),
        ):
            preparation[target] = json.loads(preparation.pop(column))
        preparation["market_snapshots"] = [
            {
                **dict(market_row),
                "quality_details": json.loads(market_row["quality_details_json"]),
            }
            for market_row in conn.execute(
                """
                SELECT instrument, source_symbol, price, bid, ask, spread, timeframe,
                       source, source_timestamp_utc, captured_at_utc, data_status,
                       is_delayed, source_quality, quality_details_json
                FROM pre_release_market_snapshots
                WHERE event_key = ? AND window_minutes = ?
                ORDER BY instrument
                """,
                (preparation["event_key"], preparation["window_minutes"]),
            ).fetchall()
        ]
        preparations.append(preparation)
    runs = [
        dict(row)
        for row in conn.execute(
            """
            SELECT source, started_utc, finished_utc, status, records, error
            FROM collection_runs ORDER BY finished_utc DESC, id DESC LIMIT 50
            """
        ).fetchall()
    ]
    return {
        "sources": sources,
        "observations": recent,
        "calendar_events": events,
        "preparations": preparations,
        "preparation_windows_minutes": list(settings.preparation_windows_minutes),
        "runs": runs,
    }
