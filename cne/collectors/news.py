"""Official central-bank news and speech feeds."""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin
from xml.etree import ElementTree

from cne.collectors import http
from cne.collectors.observations import parse_publication_date, save_observation

FEEDS = {
    "Federal Reserve · press releases": "https://www.federalreserve.gov/feeds/press_all.xml",
    "Federal Reserve · speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
    "European Central Bank": "https://www.ecb.europa.eu/rss/press.html",
}


def collect(conn) -> int:
    total = 0
    for source, feed_url in FEEDS.items():
        response = http.request(
            "GET",
            feed_url,
            timeout=20,
            cache_seconds=300,
        )
        root = ElementTree.fromstring(response.content)
        fetched_utc = datetime.now(timezone.utc).isoformat()
        channel = root.find("channel")
        if channel is None:
            raise ValueError(f"{source} RSS response has no channel.")
        for item in channel.findall("item")[:100]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or link or title).strip()
            if not title or not guid:
                continue
            published = parse_publication_date(item.findtext("pubDate"))
            category = (item.findtext("category") or "").strip()
            description = (item.findtext("description") or "").strip()
            save_observation(
                conn,
                domain="news",
                indicator=guid,
                value_text=title,
                unit="headline",
                source=source,
                source_url=urljoin(feed_url, link),
                observed_utc=published or fetched_utc,
                published_utc=published,
                publication_status=(
                    "PUBLISHED" if published else "PUBLICATION_TIME_UNKNOWN"
                ),
                observation_period=guid,
                metadata={
                    "feed_url": feed_url,
                    "category": category or None,
                    "description": description or None,
                },
            )
            total += 1
    conn.commit()
    return total
