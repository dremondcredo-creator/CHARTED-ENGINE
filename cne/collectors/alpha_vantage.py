"""Alpha Vantage FX and precious-metal observations."""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cne.collectors import http
from cne.collectors.observations import iso_utc, period_timestamp, save_observation
from cne.config import settings

BASE_URL = "https://www.alphavantage.co/query"
DOCS_URL = "https://www.alphavantage.co/documentation/"
SOURCE = "Alpha Vantage"
POLL_INTERVAL_MINUTES = 6 * 60


def _request(function: str, **params: str) -> dict:
    if not settings.alpha_vantage_api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is not configured.")
    response = http.request(
        "GET",
        BASE_URL,
        params={
            "function": function,
            **params,
            "apikey": settings.alpha_vantage_api_key,
        },
        timeout=30,
        cache_seconds=POLL_INTERVAL_MINUTES * 60,
    )
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError(f"Alpha Vantage {function} response is not a JSON object.")
    if "Error Message" in body:
        raise ValueError(f"Alpha Vantage {function}: {body['Error Message']}")
    if "Note" in body or "Information" in body:
        message = body.get("Note") or body.get("Information")
        raise RuntimeError(f"Alpha Vantage {function}: {message}")
    return body


def _number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Alpha Vantage returned an invalid {field}.") from exc
    if not isfinite(number):
        raise ValueError(f"Alpha Vantage returned a non-finite {field}.")
    return number


def _source_timestamp(value: object, timezone_name: object = None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            timestamp = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if timestamp.tzinfo is None:
        if not timezone_name:
            return None
        try:
            timestamp = timestamp.replace(tzinfo=ZoneInfo(str(timezone_name)))
        except (ZoneInfoNotFoundError, ValueError):
            return None
    return iso_utc(timestamp)


def _endpoint_url(**params: str) -> str:
    from urllib.parse import urlencode

    return f"{BASE_URL}?{urlencode(params)}"


def _save_spot(
    conn,
    *,
    symbol: str,
    value: float,
    response: dict,
    unit: str,
    source_url: str,
    bid: float | None = None,
    ask: float | None = None,
) -> None:
    retrieved = datetime.now(timezone.utc).isoformat()
    source_timestamp = _source_timestamp(
        response.get("timestamp")
        or response.get("last_updated")
        or response.get("Last Refreshed"),
        response.get("timezone") or response.get("Time Zone"),
    )
    observed = source_timestamp or retrieved
    save_observation(
        conn,
        domain="market",
        indicator=f"{symbol}_ALPHA_SPOT",
        value_numeric=value,
        unit=unit,
        source=SOURCE,
        source_url=source_url,
        observed_utc=observed,
        publication_status="SPOT_QUOTE",
        observation_period=source_timestamp or retrieved,
        metadata={
            "provider": SOURCE,
            "source_record_id": f"{symbol}:{source_timestamp}" if source_timestamp else None,
            "source_timestamp_utc": source_timestamp,
            "retrieved_at_utc": retrieved,
            "data_status": "SOURCE_TIMESTAMP_UNAVAILABLE" if not source_timestamp else "LIVE_QUOTE",
            "is_revision": False,
            "is_delayed": None,
            "delay_class": "PROVIDER_TIMESTAMP" if source_timestamp else "UNVERIFIED",
            "timeframe": "spot quote",
            "bid": bid,
            "ask": ask,
            "spread": ask - bid if bid is not None and ask is not None else None,
            "intraday_confirmation_eligible": False,
            "validation_errors": [],
        },
    )


def _collect_gold(conn) -> int:
    body = _request("GOLD_SILVER_SPOT", symbol="XAU")
    if body.get("currency", "USD").upper() != "USD":
        raise ValueError("Alpha Vantage gold spot response is not denominated in USD.")
    value = _number(body.get("price"), "gold spot price")
    if value <= 0:
        raise ValueError("Alpha Vantage returned a non-positive gold spot price.")
    _save_spot(
        conn,
        symbol="XAUUSD",
        value=value,
        response=body,
        unit="USD/troy ounce",
        source_url=_endpoint_url(function="GOLD_SILVER_SPOT", symbol="XAU"),
    )
    return 1


def _collect_eurusd_quote(conn) -> int:
    body = _request(
        "CURRENCY_EXCHANGE_RATE",
        from_currency="EUR",
        to_currency="USD",
    )
    quote = body.get("Realtime Currency Exchange Rate")
    if not isinstance(quote, dict):
        raise TypeError("Alpha Vantage EUR/USD response has no exchange-rate object.")
    value = _number(quote.get("5. Exchange Rate"), "EUR/USD exchange rate")
    if value <= 0:
        raise ValueError("Alpha Vantage returned a non-positive EUR/USD rate.")
    bid_raw = quote.get("8. Bid Price")
    ask_raw = quote.get("9. Ask Price")
    bid = _number(bid_raw, "EUR/USD bid") if bid_raw else None
    ask = _number(ask_raw, "EUR/USD ask") if ask_raw else None
    if (bid is not None and bid <= 0) or (ask is not None and ask <= 0):
        raise ValueError("Alpha Vantage returned a non-positive EUR/USD bid or ask.")
    if bid is not None and ask is not None and ask < bid:
        raise ValueError("Alpha Vantage returned an EUR/USD ask below its bid.")
    _save_spot(
        conn,
        symbol="EURUSD",
        value=value,
        response=quote,
        unit="USD per EUR",
        source_url=_endpoint_url(
            function="CURRENCY_EXCHANGE_RATE",
            from_currency="EUR",
            to_currency="USD",
        ),
        bid=bid,
        ask=ask,
    )
    return 1


def _collect_eurusd_daily(conn) -> int:
    body = _request(
        "FX_DAILY",
        from_symbol="EUR",
        to_symbol="USD",
        outputsize="compact",
    )
    series = body.get("Time Series FX (Daily)")
    if not isinstance(series, dict) or not series:
        raise TypeError("Alpha Vantage FX_DAILY response has no daily candles.")
    rows = []
    for period, candle in series.items():
        try:
            datetime.strptime(period, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Alpha Vantage returned an invalid candle date: {period}.") from exc
        if not isinstance(candle, dict):
            raise TypeError(f"Alpha Vantage FX_DAILY candle {period} is invalid.")
        open_value = _number(candle.get("1. open"), f"{period} open")
        high_value = _number(candle.get("2. high"), f"{period} high")
        low_value = _number(candle.get("3. low"), f"{period} low")
        close_value = _number(candle.get("4. close"), f"{period} close")
        if (
            min(open_value, high_value, low_value, close_value) <= 0
            or high_value < max(open_value, close_value, low_value)
            or low_value > min(open_value, close_value, high_value)
        ):
            raise ValueError(f"Alpha Vantage returned an invalid OHLC candle for {period}.")
        rows.append((period, open_value, high_value, low_value, close_value))

    retrieved = datetime.now(timezone.utc).isoformat()
    url = _endpoint_url(
        function="FX_DAILY",
        from_symbol="EUR",
        to_symbol="USD",
        outputsize="compact",
    )
    for period, open_value, high_value, low_value, close_value in rows:
        save_observation(
            conn,
            domain="market",
            indicator="EURUSD_ALPHA_DAILY_CLOSE",
            observation_period=period,
            value_numeric=close_value,
            unit="USD per EUR",
            source=SOURCE,
            source_url=url,
            observed_utc=period_timestamp(period),
            publication_status="DAILY_OHLC",
            metadata={
                "provider": SOURCE,
                "source_record_id": f"EURUSD:{period}",
                "source_timestamp_utc": None,
                "retrieved_at_utc": retrieved,
                "data_status": "DAILY",
                "is_revision": False,
                "is_delayed": None,
                "delay_class": "DAILY_RESOLUTION",
                "timeframe": "daily",
                "open": open_value,
                "high": high_value,
                "low": low_value,
                "close": close_value,
                "volume": None,
                "intraday_confirmation_eligible": False,
                "validation_errors": [],
            },
        )
    return len(rows)


def collect(conn) -> int:
    if not settings.alpha_vantage_api_key:
        raise RuntimeError("ALPHA_VANTAGE_API_KEY is not configured.")
    count = _collect_gold(conn)
    count += _collect_eurusd_quote(conn)
    count += _collect_eurusd_daily(conn)
    conn.commit()
    return count
