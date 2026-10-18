"""Immutable, release-relative preparation snapshots for upcoming calendar events."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import requests

from cne.collectors import biquote, biquote_calendar, finance_calendar
from cne.core import macro
from cne.core.events import BY_CODE

_MARKET_INSTRUMENTS = (
    ("XAUUSD", "GOLD", "quote", True),
    ("EURUSD", "EURUSD", "quote", True),
    ("DXY", "DXY", "quote", True),
    ("US02Y", "US2Y", "daily_yield", True),
    ("US10Y", "US10Y", "daily_yield", True),
    ("US_REAL_YIELD", "REAL10Y", "daily_real_yield", False),
)
_YIELD_INDICATORS = {
    "US02Y": ("US2Y_DAILY", "US2Y_FRED_DAILY"),
    "US10Y": ("US10Y_DAILY", "US10Y_FRED_DAILY"),
    "US_REAL_YIELD": ("REAL10Y_DAILY", "REAL10Y_FRED_DAILY"),
}
_YIELD_SOURCE_SYMBOLS = {
    "US02Y": {"US2Y_DAILY": "2 Yr", "US2Y_FRED_DAILY": "DGS2"},
    "US10Y": {"US10Y_DAILY": "10 Yr", "US10Y_FRED_DAILY": "DGS10"},
    "US_REAL_YIELD": {"REAL10Y_DAILY": "10 YR", "REAL10Y_FRED_DAILY": "DFII10"},
}
_MAX_DAILY_YIELD_AGE = timedelta(hours=72)
_MAX_EVENT_AGE = timedelta(minutes=15)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _calendar_code(event: dict) -> str | None:
    source = str(event.get("source") or "")
    name = str(event.get("event_name") or "")
    currency = str(event.get("currency") or "").upper()
    if source.startswith(biquote_calendar.SOURCE):
        country = "US" if currency == "USD" else "EU" if currency == "EUR" else ""
        return biquote_calendar._event_code(country, name)
    if source.startswith(finance_calendar.SOURCE):
        return finance_calendar._event_code({"name": name, "title": name})
    return None


def _find_event(conn: sqlite3.Connection, event_key: str) -> dict:
    event = conn.execute(
        "SELECT * FROM calendar_events WHERE event_id = ?", (event_key,)
    ).fetchone()
    if event:
        result = dict(event)
        result["release_id"] = None
        code = _calendar_code(result)
        if code and result.get("period"):
            release = conn.execute(
                "SELECT id FROM releases WHERE event_code = ? AND period = ?",
                (code, result["period"]),
            ).fetchone()
            result["release_id"] = release["id"] if release else None
        result["event_code"] = code
        result["unit"] = result.get("unit")
        return result

    if event_key.startswith("release:") and event_key.removeprefix("release:").isdecimal():
        row = conn.execute(
            """
            SELECT r.id AS release_id, r.event_code, r.period, r.release_time_utc,
                   r.forecast, r.previous, r.revised_previous, r.forecast_source,
                   r.updated_utc, e.name AS event_name, e.country, e.unit
            FROM releases r JOIN event_definitions e ON e.code = r.event_code
            WHERE r.id = ?
            """,
            (int(event_key.removeprefix("release:")),),
        ).fetchone()
        if row:
            return {
                **dict(row),
                "event_key": event_key,
                "currency": {"US": "USD", "EU": "EUR"}.get(row["country"]),
                "source": row["forecast_source"] or "Engine release record",
                "source_url": None,
                "source_retrieved_at_utc": row["updated_utc"],
                "source_timestamp_utc": None,
                "is_revision": bool(row["revised_previous"] is not None),
                "actual": None,
            }
    raise ValueError(f"Upcoming calendar event {event_key!r} no longer exists.")


def _pre_release_macro(conn: sqlite3.Connection, country: str, as_of: datetime) -> dict:
    rows = conn.execute(
        """
        SELECT r.event_code, r.period, r.release_time_utc, s.impact
        FROM releases r
        JOIN surprises s ON s.release_id = r.id
        JOIN event_definitions e ON e.code = r.event_code
        WHERE e.country = ? AND r.actual IS NOT NULL AND r.release_time_utc < ?
          AND r.updated_utc <= ?
        ORDER BY r.release_time_utc DESC
        """,
        (country, as_of.isoformat(), as_of.isoformat()),
    ).fetchall()
    buckets = {"inflation": 0.0, "labour": 0.0, "growth": 0.0, "policy": 0.0}
    contributions = []
    for row in rows:
        event = BY_CODE.get(row["event_code"])
        impact = row["impact"]
        if not event or not impact:
            continue
        points = (
            macro.IMPACT_VALUE[impact]
            * macro.CATEGORY_WEIGHTS.get(event.category, 0.0)
            * macro.TIER_WEIGHTS.get(event.tier, 1.0)
        )
        buckets[event.category] = buckets.get(event.category, 0.0) + points
        contributions.append(
            {
                "event_code": event.code,
                "period": row["period"],
                "impact": impact,
                "points": round(points, 3),
                "release_time_utc": row["release_time_utc"],
            }
        )
    total = sum(buckets.values())
    return {
        "country": country,
        "as_of_utc": as_of.isoformat(),
        "source_release_count": len(rows),
        "total": round(total, 3),
        "label": macro._label(total),
        **{name: round(value, 3) for name, value in buckets.items()},
        "contributions": contributions,
        "final_signal": None,
    }


def _policy_context(
    conn: sqlite3.Connection, currency: str | None, as_of: datetime
) -> list[dict]:
    if currency == "USD":
        indicators = (
            "FEDERAL_FUNDS_EFFECTIVE_RATE",
            "FEDERAL_FUNDS_TARGET_LOWER",
            "FEDERAL_FUNDS_TARGET_UPPER",
        )
    elif currency == "EUR":
        indicators = ("ECB_DEPOSIT_FACILITY_RATE",)
    else:
        return []
    placeholders = ",".join("?" for _ in indicators)
    rows = conn.execute(
        f"""
        SELECT indicator, value_numeric, value_text, unit, source, source_url,
               observed_utc, published_utc, fetched_utc, publication_status,
               metadata_json
        FROM data_observations
        WHERE domain = 'central_bank' AND indicator IN ({placeholders})
          AND fetched_utc <= ?
        ORDER BY fetched_utc DESC, id DESC
        """,
        (*indicators, as_of.isoformat()),
    ).fetchall()
    latest: dict[str, dict] = {}
    for row in rows:
        if row["indicator"] not in latest:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            latest[row["indicator"]] = item
    return list(latest.values())


def _daily_yield(conn: sqlite3.Connection, instrument: str, as_of: datetime) -> dict | None:
    indicators = _YIELD_INDICATORS[instrument]
    placeholders = ",".join("?" for _ in indicators)
    row = conn.execute(
        f"""
        SELECT indicator, value_numeric, unit, source, source_url, observed_utc,
               fetched_utc, publication_status, metadata_json
        FROM data_observations
        WHERE domain = 'market' AND indicator IN ({placeholders})
          AND value_numeric IS NOT NULL AND fetched_utc <= ?
        ORDER BY observed_utc DESC, fetched_utc DESC, id DESC LIMIT 1
        """,
        (*indicators, as_of.isoformat()),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item


def _market_snapshots(
    conn: sqlite3.Connection, event_key: str, window_minutes: int, captured: datetime
) -> tuple[list[dict], list[str]]:
    try:
        quotes = biquote.fetch_quotes()
        quote_error = None
    except (
        requests.RequestException,
        ValueError,
        TypeError,
        RuntimeError,
    ) as exc:
        quotes = {}
        quote_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    snapshots = []
    missing = []
    for instrument, symbol, kind, required in _MARKET_INSTRUMENTS:
        snapshot = {
            "event_key": event_key,
            "window_minutes": window_minutes,
            "instrument": instrument,
            "source_symbol": symbol,
            "price": None,
            "bid": None,
            "ask": None,
            "spread": None,
            "timeframe": None,
            "source": None,
            "source_timestamp_utc": None,
            "captured_at_utc": captured.isoformat(),
            "data_status": "UNAVAILABLE",
            "is_delayed": None,
            "source_quality": "UNAVAILABLE",
            "quality_details": {},
        }
        if kind == "quote":
            source_symbol = biquote.SYMBOLS[symbol][0]
            snapshot["source_symbol"] = source_symbol
            quote = quotes.get(symbol)
            if quote:
                stale = bool(quote["is_stale"])
                closed = quote["market_state"] == "closed"
                status = "STALE" if stale else "MARKET_CLOSED" if closed else "LIVE"
                snapshot.update(
                    {
                        "price": quote["mid"],
                        "bid": quote["bid"],
                        "ask": quote["ask"],
                        "spread": quote["spread"],
                        "timeframe": "tick",
                        "source": biquote.SOURCE,
                        "source_timestamp_utc": quote["source_timestamp_utc"],
                        "data_status": status,
                        "is_delayed": stale or closed,
                        "source_quality": (
                            "STALE_INDICATIVE"
                            if stale
                            else "CLOSED_INDICATIVE"
                            if closed
                            else "CURRENT_INDICATIVE"
                        ),
                        "quality_details": {
                            "quote_age_seconds": quote["quote_age_seconds"],
                            "market_state": quote["market_state"],
                            "provider_source": quote["provider_source"],
                            "exchange": quote["exchange"],
                        },
                    }
                )
                if required and (stale or closed):
                    missing.append(f"{instrument}_FRESH_LIVE_FEED")
            else:
                snapshot["quality_details"] = {
                    "provider_error": quote_error or "Provider omitted this instrument."
                }
                if required:
                    missing.append(f"{instrument}_FEED")
        else:
            row = _daily_yield(conn, instrument, captured)
            if row:
                observed = _parse_time(row["observed_utc"])
                age = captured - observed
                stale = age < timedelta(0) or age > _MAX_DAILY_YIELD_AGE
                status = "STALE" if stale else "DAILY"
                snapshot["source_symbol"] = _YIELD_SOURCE_SYMBOLS[instrument].get(
                    row["indicator"], row["indicator"]
                )
                snapshot.update(
                    {
                        "price": row["value_numeric"],
                        "timeframe": "daily",
                        "source": row["source"],
                        "source_timestamp_utc": row["metadata"].get(
                            "source_timestamp_utc"
                        ),
                        "data_status": status,
                        "is_delayed": True,
                        "source_quality": "STALE_DAILY_CONTEXT" if stale else "DAILY_CONTEXT",
                        "quality_details": {
                            "observation_utc": row["observed_utc"],
                            "retrieved_at_utc": row["fetched_utc"],
                            "age_hours": round(age.total_seconds() / 3600, 2),
                            "intraday_confirmation_eligible": False,
                        },
                    }
                )
                if required and stale:
                    missing.append(f"{instrument}_FRESH_DAILY_DATA")
            else:
                snapshot["quality_details"] = {
                    "provider_error": "No stored daily observation is available."
                }
                if required:
                    missing.append(f"{instrument}_FEED")
        snapshots.append(snapshot)
    return snapshots, missing


def capture_preparation(
    conn: sqlite3.Connection, event_key: str, window_minutes: int
) -> dict:
    if window_minutes <= 0:
        raise ValueError("Preparation window must be a positive number of minutes.")
    existing = conn.execute(
        """
        SELECT release_id, data_quality_status, missing_fields_json
        FROM pre_release_preparations
        WHERE event_key = ? AND window_minutes = ?
        """,
        (event_key, window_minutes),
    ).fetchone()
    if existing:
        return {
            "event_key": event_key,
            "window_minutes": window_minutes,
            "release_id": existing["release_id"],
            "status": existing["data_quality_status"],
            "missing_fields": json.loads(existing["missing_fields_json"]),
            "already_captured": True,
        }

    event = _find_event(conn, event_key)
    scheduled = _parse_time(event.get("release_time_utc") or "")
    captured = datetime.now(timezone.utc)
    if captured >= scheduled:
        raise RuntimeError(
            f"Pre-release preparation for {event_key} was invoked at or after "
            f"scheduled release {scheduled.isoformat()}."
        )

    source_retrieved = event.get("retrieved_at_utc") or event.get(
        "source_retrieved_at_utc"
    )
    source_timestamp = event.get("source_timestamp_utc")
    source = str(event.get("source") or event.get("forecast_source") or "Unknown source")
    forecast = event.get("forecast")
    previous = event.get("previous")
    revised_previous = event.get("revised_previous")
    is_revision = bool(event.get("is_revision") or revised_previous is not None)
    forecast_pre_release = forecast is not None and captured < scheduled
    code = event.get("event_code")
    definition = BY_CODE.get(code) if code else None
    country = (
        definition.country
        if definition
        else "US" if event.get("currency") == "USD"
        else "EU" if event.get("currency") == "EUR"
        else event.get("country")
    )

    missing = []
    if not forecast_pre_release:
        missing.append("CONSENSUS_FORECAST")
    if not source_retrieved:
        missing.append("CALENDAR_RETRIEVAL_TIMESTAMP")
    else:
        retrieved_time = _parse_time(source_retrieved)
        if captured - retrieved_time > _MAX_EVENT_AGE:
            missing.append("CALENDAR_DATA_FRESHNESS")
    if definition is None:
        missing.append("MAPPED_EVENT_DEFINITION")

    market_snapshots, market_missing = _market_snapshots(
        conn, event_key, window_minutes, captured
    )
    missing.extend(market_missing)
    policy_context = _policy_context(conn, event.get("currency"), captured)
    if event.get("currency") in {"USD", "EUR"} and not policy_context:
        missing.append("CENTRAL_BANK_POLICY_CONTEXT")

    fundamental_context = (
        _pre_release_macro(conn, country, captured)
        if country in {"US", "EU"}
        else {
            "country": country,
            "as_of_utc": captured.isoformat(),
            "status": "UNAVAILABLE_FOR_CURRENCY",
            "final_signal": None,
        }
    )
    scenarios = {}
    higher_is = definition.higher_is if definition else None
    for scenario, surprise_direction in (
        ("actual_above_forecast", "ABOVE"),
        ("actual_near_forecast", "NEAR"),
        ("actual_below_forecast", "BELOW"),
    ):
        if not forecast_pre_release:
            bias = None
            scenario_status = "UNAVAILABLE_FORECAST"
        elif surprise_direction == "NEAR":
            bias = "NEUTRAL"
            scenario_status = "CONDITIONAL"
        elif higher_is is None:
            bias = None
            scenario_status = "UNMAPPED_EVENT"
        else:
            bias = (
                higher_is
                if surprise_direction == "ABOVE"
                else "BEARISH"
                if higher_is == "BULLISH"
                else "BULLISH"
                if higher_is == "BEARISH"
                else "NEUTRAL"
            )
            scenario_status = "CONDITIONAL"
        scenarios[scenario] = {
            "status": scenario_status,
            "actual_relation": surprise_direction,
            "domestic_currency_bias_if_released": bias,
            "economic_direction": higher_is,
            "final_signal": None,
            "requires_actual_and_market_reaction": True,
        }

    data_quality = "COMPLETE" if not missing else "INCOMPLETE"
    release_id = event.get("release_id")
    conn.execute(
        """
        INSERT INTO pre_release_preparations
          (event_key, window_minutes, release_id, event_name, currency, country,
           period, scheduled_release_utc, captured_at_utc, source,
           source_retrieved_at_utc, source_timestamp_utc, forecast, previous,
           revised_previous, is_revision, forecast_available_pre_release,
           data_quality_status, missing_fields_json, policy_context_json,
           fundamental_context_json, scenarios_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_key,
            window_minutes,
            release_id,
            str(event.get("event_name") or event.get("event_code") or event_key),
            event.get("currency"),
            country,
            event.get("period"),
            scheduled.isoformat(),
            captured.isoformat(),
            source,
            source_retrieved,
            source_timestamp,
            forecast if forecast_pre_release else None,
            previous,
            revised_previous,
            int(is_revision),
            int(forecast_pre_release),
            data_quality,
            _json(missing),
            _json(policy_context),
            _json(fundamental_context),
            _json(scenarios),
        ),
    )
    conn.executemany(
        """
        INSERT INTO pre_release_market_snapshots
          (event_key, window_minutes, instrument, source_symbol, price, bid, ask,
           spread, timeframe, source, source_timestamp_utc, captured_at_utc,
           data_status, is_delayed, source_quality, quality_details_json)
        VALUES (:event_key, :window_minutes, :instrument, :source_symbol, :price,
                :bid, :ask, :spread, :timeframe, :source, :source_timestamp_utc,
                :captured_at_utc, :data_status, :is_delayed, :source_quality,
                :quality_details_json)
        """,
        [
            {
                **snapshot,
                "quality_details_json": _json(snapshot.pop("quality_details")),
            }
            for snapshot in market_snapshots
        ],
    )
    conn.commit()
    return {
        "event_key": event_key,
        "window_minutes": window_minutes,
        "release_id": release_id,
        "status": data_quality,
        "missing_fields": missing,
        "market_snapshots": len(market_snapshots),
        "already_captured": False,
    }
