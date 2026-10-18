"""SQLite access + idempotent schema init."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from cne.config import settings
from cne.logging_setup import get_logger

log = get_logger(__name__)
SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_INIT_LOCK = Lock()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _migrate_structure_verdicts(conn: sqlite3.Connection) -> None:
    info = conn.execute("PRAGMA table_info(structure_verdicts)").fetchall()
    primary_key = tuple(
        row["name"]
        for row in sorted((row for row in info if row["pk"]), key=lambda row: row["pk"])
    )
    if primary_key == ("release_id", "symbol"):
        return

    conn.execute("BEGIN")
    try:
        conn.execute("""
            CREATE TABLE structure_verdicts_migrating (
                release_id        INTEGER NOT NULL,
                symbol            TEXT NOT NULL,
                phase             TEXT,
                nearest_level     REAL,
                nearest_kind      TEXT,
                distance_atr      REAL,
                structure_before  TEXT,
                structure_after   TEXT,
                event_class       TEXT,
                break_pct         REAL,
                held_retest       INTEGER,
                confirmation      TEXT,
                confirmation_score REAL,
                notes             TEXT,
                computed_utc      TEXT NOT NULL,
                PRIMARY KEY(release_id, symbol),
                FOREIGN KEY(release_id) REFERENCES releases(id)
            )
        """)
        conn.execute("""
            INSERT OR IGNORE INTO structure_verdicts_migrating
            SELECT release_id, symbol, phase, nearest_level, nearest_kind,
                   distance_atr, structure_before, structure_after, event_class,
                   break_pct, held_retest, confirmation, confirmation_score,
                   notes, computed_utc
            FROM structure_verdicts
        """)
        conn.execute("DROP TABLE structure_verdicts")
        conn.execute("""
            ALTER TABLE structure_verdicts_migrating RENAME TO structure_verdicts
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate_surprises(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(surprises)")}
    columns = {
        "normalized_surprise": "REAL",
        "historical_scale": "REAL",
        "normalization_n": "INTEGER",
        "normalization_status": "TEXT",
        "data_status": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE surprises ADD COLUMN {name} {definition}")


def _migrate_release_monitor_results(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"] for row in conn.execute("PRAGMA table_info(release_monitor_results)")
    }
    columns = {
        "previous_source": "TEXT",
        "previous_retrieved_at_utc": "TEXT",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE release_monitor_results ADD COLUMN {name} {definition}"
            )


def init_db() -> sqlite3.Connection:
    with _INIT_LOCK:
        conn = connect()
        try:
            conn.executescript(SCHEMA_PATH.read_text())
            _migrate_structure_verdicts(conn)
            _migrate_surprises(conn)
            _migrate_release_monitor_results(conn)
            conn.commit()
            log.info("database ready at %s", settings.db_path)
            return conn
        except Exception:
            conn.close()
            raise


def upsert_release(conn, *, event_code, period, release_time_utc=None,
                   previous=None, forecast=None, actual=None,
                   revised_previous=None, actual_source=None,
                   forecast_source=None):
    """Insert or merge. NULLs never overwrite existing values."""
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT id FROM releases WHERE event_code=? AND period=?",
        (event_code, period),
    ).fetchone()

    if row is None:
        conn.execute("""
            INSERT INTO releases
              (event_code, period, release_time_utc, previous, forecast,
               actual, revised_previous, actual_source, forecast_source,
               first_seen_utc, updated_utc)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (event_code, period, release_time_utc, previous, forecast,
              actual, revised_previous, actual_source, forecast_source,
              now, now))
    else:
        conn.execute("""
            UPDATE releases SET
                release_time_utc = COALESCE(?, release_time_utc),
                previous         = COALESCE(?, previous),
                forecast         = COALESCE(?, forecast),
                actual           = COALESCE(?, actual),
                revised_previous = COALESCE(?, revised_previous),
                actual_source    = COALESCE(?, actual_source),
                forecast_source  = COALESCE(?, forecast_source),
                updated_utc      = ?
            WHERE id = ?
        """, (release_time_utc, previous, forecast, actual,
              revised_previous, actual_source, forecast_source,
              now, row["id"]))
    conn.commit()


def record_observation(
    conn: sqlite3.Connection,
    *,
    domain: str,
    indicator: str,
    value_numeric: float | None = None,
    value_text: str | None = None,
    unit: str,
    source: str,
    source_url: str | None,
    observed_utc: str,
    published_utc: str | None = None,
    publication_status: str,
    observation_period: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Append one source observation with its source and time semantics."""
    fetched_utc = datetime.now(timezone.utc).isoformat()
    observation_metadata = dict(metadata or {})
    observation_metadata.setdefault("provider", source)
    observation_metadata.setdefault("source_record_id", None)
    observation_metadata.setdefault("retrieved_at_utc", fetched_utc)
    observation_metadata.setdefault("source_timestamp_utc", None)
    observation_metadata.setdefault("data_status", publication_status)
    observation_metadata.setdefault("is_revision", False)
    observation_metadata.setdefault("is_delayed", None)
    observation_metadata.setdefault("validation_errors", [])
    observation_metadata.setdefault("last_successful_sync_utc", fetched_utc)
    if observation_period is not None:
        previous = conn.execute(
            """
            SELECT value_numeric, value_text, unit, source_url, observed_utc,
                   published_utc, publication_status FROM data_observations
            WHERE domain = ? AND indicator = ? AND observation_period = ? AND source = ?
            ORDER BY fetched_utc DESC, id DESC LIMIT 1
            """,
            (domain, indicator, observation_period, source),
        ).fetchone()
        if previous and (
            previous["value_numeric"] == value_numeric
            and previous["value_text"] == value_text
            and previous["unit"] == unit
            and previous["source_url"] == source_url
            and previous["observed_utc"] == observed_utc
            and previous["published_utc"] == published_utc
            and previous["publication_status"] == publication_status
        ):
            return
        if previous and (
            previous["value_numeric"] != value_numeric
            or previous["value_text"] != value_text
        ):
            observation_metadata["is_revision"] = True
            observation_metadata["previous_value"] = (
                previous["value_numeric"]
                if previous["value_numeric"] is not None
                else previous["value_text"]
            )
    conn.execute(
        """
        INSERT OR REPLACE INTO data_observations
          (domain, indicator, observation_period, value_numeric, value_text,
           unit, source, source_url, observed_utc, published_utc, fetched_utc,
           publication_status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            domain,
            indicator,
            observation_period,
            value_numeric,
            value_text,
            unit,
            source,
            source_url,
            observed_utc,
            published_utc,
            fetched_utc,
            publication_status,
            json.dumps(observation_metadata, ensure_ascii=False, sort_keys=True),
        ),
    )