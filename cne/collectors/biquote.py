"""Keyless BiQuote market quotes and OHLC data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import iso_utc, save_observation

BASE_URL = "https://biquote.io"
LATEST_URL = f"{BASE_URL}/api/latest"
OHLC_URL = f"{BASE_URL}/api/{{symbol}}/ohlc"
DOCS_URL = f"{BASE_URL}/docs/"
TERMS_URL = f"{BASE_URL}/terms.html"
SOURCE = "BiQuote indicative broker feed"
POLL_INTERVAL_MINUTES = 5

SYMBOLS = {
    "DXY": ("DXY", "index points"),
    "GOLD": ("XAUUSD", "USD/troy ounce"),
    "EURUSD": ("EURUSD", "USD per EUR"),
}
INTERVALS = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
MAX_AGE_SECONDS = 15 * 60
PAGE_BARS = 900


def _number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"BiQuote returned an invalid {field}.") from exc
    if not isfinite(number):
        raise ValueError(f"BiQuote returned a non-finite {field}.")
    return number


def _timestamp(value: object, field: str) -> str:
    if not value:
        raise ValueError(f"BiQuote response is missing {field}.")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"BiQuote returned an invalid {field}.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"BiQuote returned a timestamp without a timezone: {field}.")
    return iso_utc(parsed)


def fetch_quotes() -> dict[str, dict]:
    symbols = [provider_symbol for provider_symbol, _unit in SYMBOLS.values()]
    response = http.request(
        "GET",
        LATEST_URL,
        params={"symbols": symbols, "allowStale": "true"},
        timeout=20,
        cache_seconds=30,
    )
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError("BiQuote latest response is not a JSON object.")

    retrieved_at = datetime.now(timezone.utc)
    retrieved = retrieved_at.isoformat()
    quotes = {}
    for symbol, (provider_symbol, _unit) in SYMBOLS.items():
        raw = body.get(provider_symbol)
        if raw is None:
            continue
        if not isinstance(raw, dict) or raw.get("symbol") != provider_symbol:
            raise TypeError(f"BiQuote returned an invalid {provider_symbol} quote.")
        bid = _number(raw.get("bid"), f"{provider_symbol} bid")
        ask = _number(raw.get("ask"), f"{provider_symbol} ask")
        if bid <= 0 or ask < bid:
            raise ValueError(f"BiQuote returned an invalid {provider_symbol} spread.")
        mid = _number(raw.get("mid"), f"{provider_symbol} mid")
        if mid <= 0 or not bid <= mid <= ask:
            raise ValueError(f"BiQuote returned an invalid {provider_symbol} midpoint.")
        source_timestamp = _timestamp(raw.get("timestamp"), "quote timestamp")
        parsed_timestamp = datetime.fromisoformat(source_timestamp)
        if parsed_timestamp > retrieved_at + timedelta(minutes=1):
            raise ValueError(f"BiQuote returned a future {provider_symbol} timestamp.")
        age = _number(raw.get("quoteAgeSeconds"), "quote age")
        if age < 0 or age > 10 * 365 * 24 * 60 * 60:
            raise ValueError(f"BiQuote returned an invalid {provider_symbol} quote age.")
        spread = _number(raw.get("spread"), f"{provider_symbol} spread")
        if spread < 0 or abs(spread - (ask - bid)) > max(1e-8, spread * 0.01):
            raise ValueError(f"BiQuote returned an inconsistent {provider_symbol} spread.")
        state = raw.get("marketState")
        stale_flag = raw.get("stale")
        if state not in {"open", "closed"} or not isinstance(stale_flag, bool):
            raise ValueError(f"BiQuote returned an invalid {provider_symbol} market state.")
        measured_age = max(0.0, (retrieved_at - parsed_timestamp).total_seconds())
        quotes[symbol] = {
            "symbol": provider_symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread": spread,
            "high": _number(raw.get("high"), f"{provider_symbol} high"),
            "low": _number(raw.get("low"), f"{provider_symbol} low"),
            "source_timestamp_utc": source_timestamp,
            "retrieved_at_utc": retrieved,
            "quote_age_seconds": age,
            "market_state": state,
            "is_stale": (
                stale_flag
                or age > MAX_AGE_SECONDS
                or measured_age > MAX_AGE_SECONDS
            ),
            "day_diff_percent": (
                _number(raw["dayDiffPercent"], f"{provider_symbol} day change")
                if raw.get("dayDiffPercent") is not None
                else None
            ),
            "description": str(raw.get("description") or ""),
            "exchange": str(raw.get("exchange") or ""),
            "provider_source": str(raw.get("source") or ""),
        }
    return quotes


def collect(conn) -> int:
    quotes = fetch_quotes()
    if not quotes:
        raise ValueError("BiQuote returned no quotes for DXY, XAUUSD, or EURUSD.")

    for symbol, quote in quotes.items():
        provider_symbol, unit = SYMBOLS[symbol]
        status = (
            "STALE"
            if quote["is_stale"]
            else "MARKET_CLOSED"
            if quote["market_state"] == "closed"
            else "LIVE"
        )
        save_observation(
            conn,
            domain="market",
            indicator=f"{symbol}_BIQUOTE_SPOT",
            value_numeric=quote["mid"],
            unit=unit,
            source=SOURCE,
            source_url=(
                f"{LATEST_URL}?symbols={provider_symbol}&allowStale=true"
            ),
            observed_utc=quote["source_timestamp_utc"],
            publication_status=status,
            observation_period=quote["source_timestamp_utc"],
            metadata={
                "provider": "BiQuote",
                "source_record_id": f"{provider_symbol}:{quote['source_timestamp_utc']}",
                "source_timestamp_utc": quote["source_timestamp_utc"],
                "retrieved_at_utc": quote["retrieved_at_utc"],
                "data_status": status,
                "is_revision": False,
                "is_delayed": quote["is_stale"] or quote["market_state"] == "closed",
                "delay_class": (
                    "STALE" if quote["is_stale"] else
                    "MARKET_CLOSED" if quote["market_state"] == "closed" else
                    "CURRENT"
                ),
                "market_state": quote["market_state"],
                "quote_age_seconds": quote["quote_age_seconds"],
                "bid": quote["bid"],
                "ask": quote["ask"],
                "mid": quote["mid"],
                "spread": quote["spread"],
                "high": quote["high"],
                "low": quote["low"],
                "day_diff_percent": quote["day_diff_percent"],
                "exchange": quote["exchange"],
                "provider_source": quote["provider_source"],
                "timeframe": "tick",
                "volume": None,
                "tick_volume": None,
                "intraday_confirmation_eligible": (
                    not quote["is_stale"] and quote["market_state"] == "open"
                ),
                "validation_errors": [],
            },
        )
    conn.commit()
    return len(quotes)


def fetch_ohlc(
    symbol: str,
    *,
    period: str = "5d",
    interval: str = "5m",
    end: datetime | None = None,
):
    import pandas as pd

    if symbol not in SYMBOLS:
        raise RuntimeError(
            f"BiQuote does not provide a verified intraday feed for {symbol}."
        )
    if interval not in INTERVALS:
        raise ValueError(f"Unsupported BiQuote OHLC interval: {interval}.")
    try:
        duration = pd.Timedelta(period).to_pytimedelta()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid OHLC lookback period: {period!r}.") from exc
    if duration.total_seconds() <= 0:
        raise ValueError("OHLC lookback period must be positive.")

    now = datetime.now(timezone.utc)
    if end is not None and end.tzinfo is None:
        raise ValueError("OHLC end timestamp must include a timezone.")
    end_at = (end or now).astimezone(timezone.utc)
    if end_at > now + timedelta(minutes=1):
        raise ValueError("OHLC end timestamp cannot be in the future.")
    start = end_at - duration
    step_seconds = INTERVALS[interval]
    chunk = step_seconds * PAGE_BARS
    url = OHLC_URL.format(symbol=SYMBOLS[symbol][0])
    bars_by_time = {}
    cursor = start
    while cursor < end_at:
        chunk_end = min(cursor.timestamp() + chunk, end_at.timestamp())
        chunk_end_at = datetime.fromtimestamp(chunk_end, tz=timezone.utc)
        response = http.request(
            "GET",
            url,
            params={
                "interval": interval,
                "from": cursor.isoformat().replace("+00:00", "Z"),
                "to": chunk_end_at.isoformat().replace("+00:00", "Z"),
                "limit": "1000",
            },
            timeout=30,
            cache_seconds=30,
        )
        body = response.json()
        if (
            not isinstance(body, dict)
            or body.get("symbol") != SYMBOLS[symbol][0]
            or body.get("interval") != interval
            or not isinstance(body.get("bars"), list)
        ):
            raise TypeError(f"BiQuote returned an invalid {symbol} OHLC response.")
        for raw in body["bars"]:
            if not isinstance(raw, dict):
                raise TypeError(f"BiQuote returned an invalid {symbol} OHLC bar.")
            timestamp = pd.Timestamp(_timestamp(raw.get("openTime"), "bar open time"))
            if timestamp < pd.Timestamp(cursor) or timestamp > pd.Timestamp(chunk_end_at):
                continue
            if raw.get("isOpen"):
                continue
            values = {
                "Open": _number(raw.get("open"), "bar open"),
                "High": _number(raw.get("high"), "bar high"),
                "Low": _number(raw.get("low"), "bar low"),
                "Close": _number(raw.get("close"), "bar close"),
                "Volume": _number(raw.get("volume", 0), "bar volume"),
                "TickVolume": _number(raw.get("tickVolume", 0), "bar tick volume"),
            }
            if (
                min(values["Open"], values["High"], values["Low"], values["Close"]) <= 0
                or values["High"] < max(values["Open"], values["Low"], values["Close"])
                or values["Low"] > min(values["Open"], values["High"], values["Close"])
            ):
                raise ValueError(f"BiQuote returned invalid {symbol} OHLC values.")
            previous = bars_by_time.get(timestamp)
            if previous is None:
                bars_by_time[timestamp] = values
        cursor = chunk_end_at

    if not bars_by_time:
        raise ValueError(f"BiQuote returned no closed {interval} bars for {symbol}.")
    frame = pd.DataFrame.from_dict(bars_by_time, orient="index")
    frame.index = pd.DatetimeIndex(frame.index, name="timestamp")
    return frame.sort_index()
