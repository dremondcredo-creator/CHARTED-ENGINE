"""Release-aligned price snapshots from the configured market-price provider."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from cne.collectors import prices
from cne.collectors.prices import bar_at_or_before
from cne.config import settings

PHASE_OFFSETS = {
    "PRE": timedelta(minutes=-15),
    "T0": timedelta(0),
    "T+5M": timedelta(minutes=5),
    "T+30M": timedelta(minutes=30),
    "T+2H": timedelta(hours=2),
}


def _release_time(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def capture_live(
    conn: sqlite3.Connection, release_id: int, phase: str
) -> int:
    if phase not in PHASE_OFFSETS:
        raise ValueError(f"Unsupported snapshot phase: {phase}")
    release = conn.execute(
        "SELECT release_time_utc FROM releases WHERE id = ?", (release_id,)
    ).fetchone()
    if not release or not release["release_time_utc"]:
        raise ValueError(f"Release {release_id} has no scheduled release timestamp.")

    release_time = _release_time(release["release_time_utc"])
    target = release_time + PHASE_OFFSETS[phase]
    now = datetime.now(timezone.utc)
    if target > now:
        raise RuntimeError(
            f"Cannot capture {phase} before its target time {target.isoformat()}."
        )
    total = 0
    missing = []
    for symbol in ("DXY", "GOLD", "EURUSD"):
        frame = prices.fetch_ohlc(
            symbol,
            period="2h",
            interval="5m",
            end=target,
        )
        bar = bar_at_or_before(frame, target - timedelta(minutes=5))
        if bar is None:
            missing.append(symbol)
            continue
        bar_time = datetime.fromisoformat(bar["ts"].replace("Z", "+00:00"))
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=timezone.utc)
        bar_close = bar_time + timedelta(minutes=5)
        age = target - bar_close
        if age > timedelta(minutes=15):
            missing.append(symbol)
            continue

        baseline = None
        if phase != "PRE":
            baseline_row = conn.execute(
                """
                SELECT price FROM price_snapshots
                WHERE release_id = ? AND phase = 'PRE' AND symbol = ?
                """,
                (release_id, symbol),
            ).fetchone()
            baseline = baseline_row["price"] if baseline_row else None
        price = bar["close"]
        change_abs = price - baseline if baseline else (0.0 if phase == "PRE" else None)
        change_pct = (
            change_abs / baseline * 100
            if baseline and change_abs is not None
            else (0.0 if phase == "PRE" else None)
        )
        conn.execute(
            """
            INSERT INTO price_snapshots
              (release_id, phase, symbol, price, change_pct, change_abs,
               bar_ts_utc, source, captured_utc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(release_id, phase, symbol) DO UPDATE SET
              price=excluded.price,
              change_pct=excluded.change_pct,
              change_abs=excluded.change_abs,
              bar_ts_utc=excluded.bar_ts_utc,
              source=excluded.source,
              captured_utc=excluded.captured_utc
            """,
            (
                release_id,
                phase,
                symbol,
                price,
                change_pct,
                change_abs,
                bar_time.isoformat(),
                prices.source_name(),
                now.isoformat(),
            ),
        )
        total += 1
    conn.commit()
    if not total:
        raise RuntimeError(
            f"BiQuote has no sufficiently fresh closed 5-minute bars for "
            f"{', '.join(missing) or 'DXY, Gold, and EUR/USD'} at "
            f"{target.isoformat()}."
        )
    return total


def backfill_all(conn: sqlite3.Connection) -> int:
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(days=min(settings.snapshot_days_back, 60))
    ).isoformat()
    releases = conn.execute(
        """
        SELECT id, release_time_utc FROM releases
        WHERE release_time_utc IS NOT NULL
          AND release_time_utc >= ?
        ORDER BY release_time_utc
        """,
        (cutoff,),
    ).fetchall()
    total = 0
    for release in releases:
        release_time = _release_time(release["release_time_utc"])
        now = datetime.now(timezone.utc)
        phases = [
            phase
            for phase, offset in PHASE_OFFSETS.items()
            if release_time + offset <= now
        ]
        for phase in phases:
            total += capture_live(conn, release["id"], phase)
    return total
