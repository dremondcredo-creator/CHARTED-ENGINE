"""No-key Gold API spot quote connector (latency is not independently verified)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite

from cne.collectors import http
from cne.collectors.observations import save_observation

PRICE_URL = "https://api.gold-api.com/price/XAU"
DOCS_URL = "https://gold-api.com/docs"
SOURCE = "Gold API · XAU spot quote"
MAX_AGE = timedelta(minutes=15)


def fetch_quote() -> dict:
    response = http.request(
        "GET",
        PRICE_URL,
        timeout=20,
        cache_seconds=60,
    )
    body = response.json()
    if not isinstance(body, dict) or body.get("symbol") != "XAU" or body.get("currency") != "USD":
        raise TypeError("Gold API did not return the expected XAU/USD quote fields.")
    try:
        price = float(body["price"])
        source_time = datetime.fromisoformat(
            str(body["updatedAt"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Gold API quote is missing a valid price or updatedAt.") from exc
    if not isfinite(price) or price <= 0:
        raise ValueError("Gold API returned an invalid XAU price.")
    now = datetime.now(timezone.utc)
    if source_time > now + timedelta(minutes=1):
        raise ValueError("Gold API quote timestamp is in the future.")
    return {
        "price": price,
        "source_timestamp_utc": source_time.isoformat(),
        "retrieved_at_utc": now.isoformat(),
        "is_stale": now - source_time > MAX_AGE,
    }


def collect(conn) -> int:
    quote = fetch_quote()
    status = "STALE" if quote["is_stale"] else "LATENCY_UNVERIFIED"
    save_observation(
        conn,
        domain="market",
        indicator="XAUUSD_SPOT_QUOTE",
        value_numeric=quote["price"],
        unit="USD/troy ounce",
        source=SOURCE,
        source_url=PRICE_URL,
        observed_utc=quote["source_timestamp_utc"],
        publication_status=status,
        metadata={
            "provider": "Gold API",
            "source_record_id": f"XAU:{quote['source_timestamp_utc']}",
            "source_timestamp_utc": quote["source_timestamp_utc"],
            "retrieved_at_utc": quote["retrieved_at_utc"],
            "data_status": status,
            "is_revision": False,
            "is_delayed": None,
            "delay_class": "UNVERIFIED",
            "provider_claims_realtime": True,
            "has_bid": False,
            "has_ask": False,
            "has_ohlc": False,
            "has_spread": False,
            "timeframe": "single quote",
            "validation_errors": [],
        },
    )
    conn.commit()
    return 1
