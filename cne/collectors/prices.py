"""Market-price helpers backed by BiQuote's indicative intraday feed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from cne.collectors import biquote

SYMBOLS = {
    "DXY": {},
    "US2Y": {},
    "US10Y": {},
    "GOLD": {},
    "EURUSD": {},
    "REAL10Y": {},
    "VIX": {},
}
PRICE_UNITS = {
    "DXY": "index points",
    "US2Y": "%",
    "US10Y": "%",
    "GOLD": "USD/troy ounce",
    "EURUSD": "USD per EUR",
    "REAL10Y": "%",
    "VIX": "index points",
}
MAX_MARKET_BAR_AGE = timedelta(minutes=15)


def source_name() -> str:
    return biquote.SOURCE


def source_url(symbol: str) -> str:
    if symbol in biquote.SYMBOLS:
        return biquote.OHLC_URL.format(symbol=biquote.SYMBOLS[symbol][0])
    return (
        "https://home.treasury.gov/policy-issues/financing-the-government/"
        "interest-rate-statistics"
    )


def publication_status(frame=None) -> str:
    if frame is None or frame.empty:
        return "UNAVAILABLE"
    return "LIVE" if not is_stale(frame.index[-1]) else "DELAYED"


def unit_for(symbol: str) -> str:
    try:
        return PRICE_UNITS[symbol]
    except KeyError as exc:
        raise KeyError(f"Unknown symbol {symbol}") from exc


def is_stale(timestamp, *, now=None) -> bool:
    observed = pd.Timestamp(timestamp).to_pydatetime()
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (
        observed > current + timedelta(minutes=1)
        or current - observed > MAX_MARKET_BAR_AGE
    )


def fetch_ohlc(symbol, *, period="5d", interval="5m", end=None):
    if symbol not in SYMBOLS:
        raise KeyError(f"Unknown symbol {symbol}")
    return biquote.fetch_ohlc(
        symbol,
        period=period,
        interval=interval,
        end=end,
    )


def bar_at_or_before(df, ts):
    if df.empty:
        return None
    prior = df[df.index <= ts]
    if prior.empty:
        return None
    row = prior.iloc[-1]
    return {
        "close": float(row["Close"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "open": float(row["Open"]),
        "ts": prior.index[-1].isoformat(),
    }


def latest_price(symbol):
    if symbol not in SYMBOLS:
        raise KeyError(f"Unknown symbol {symbol}")
    quotes = biquote.fetch_quotes()
    provider_symbol = biquote.SYMBOLS[symbol][0]
    if symbol not in quotes:
        raise RuntimeError(f"BiQuote returned no latest quote for {provider_symbol}.")
    return quotes[symbol]["mid"]


def latest_prices():
    quotes = biquote.fetch_quotes()
    return {symbol: quote["mid"] for symbol, quote in quotes.items()}


def collect(conn):
    return biquote.collect(conn)
