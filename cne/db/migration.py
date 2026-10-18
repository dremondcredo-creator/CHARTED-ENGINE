"""One-time, integrity-checked imports from SQLite into PostgreSQL."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cne.logging_setup import get_logger

log = get_logger(__name__)
_BATCH_SIZE = 500


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _snapshot(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(target_path)
    try:
        source.execute("PRAGMA busy_timeout=30000")
        source.backup(target, pages=256, sleep=0.05)
        target.commit()
    finally:
        target.close()
        source.close()


def _fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ordered_tables(source: sqlite3.Connection) -> list[str]:
    tables = [
        row[0]
        for row in source.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
        )
        if row[0] != "migration_imports"
    ]
    table_set = set(tables)
    dependencies = {
        table: {
            row[2]
            for row in source.execute(
                f"PRAGMA foreign_key_list({_quote_identifier(table)})"
            )
            if row[2] in table_set and row[2] != table
        }
        for table in tables
    }
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(table: str) -> None:
        if table in visited:
            return
        visited.add(table)
        for dependency in dependencies[table]:
            visit(dependency)
        ordered.append(table)

    for table in tables:
        visit(table)
    return ordered


def import_sqlite_databases(conn, source_paths: tuple[Path, ...]) -> list[dict]:
    """Import each SQLite source once, committing each complete source atomically."""
    reports = []
    for source_path in source_paths:
        source_path = source_path.expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(f"SQLite import source does not exist: {source_path}")

        with tempfile.TemporaryDirectory(prefix="cne-sqlite-import-") as temp_dir:
            snapshot_path = Path(temp_dir) / "snapshot.sqlite"
            _snapshot(source_path, snapshot_path)
            fingerprint = _fingerprint(snapshot_path)
            source_key = str(source_path.resolve())
            imported = conn.execute(
                "SELECT source_fingerprint, records_json FROM migration_imports "
                "WHERE source_key = ?",
                (source_key,),
            ).fetchone()
            conn.commit()
            if imported:
                reports.append({
                    "source": source_key,
                    "status": "already_imported",
                    "fingerprint": imported["source_fingerprint"],
                    "records": json.loads(imported["records_json"]),
                })
                continue

            source = sqlite3.connect(snapshot_path)
            source.row_factory = sqlite3.Row
            try:
                integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise RuntimeError(
                        f"SQLite integrity check failed for {source_key}: {integrity}"
                    )
                broken_references = source.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if broken_references:
                    raise RuntimeError(
                        f"SQLite foreign-key check found {len(broken_references)} "
                        f"violations in {source_key}."
                    )

                table_reports = {}
                for table in _ordered_tables(source):
                    quoted = _quote_identifier(table)
                    source_columns = [
                        row["name"]
                        for row in source.execute(f"PRAGMA table_info({quoted})")
                    ]
                    target_columns = [
                        row["column_name"]
                        for row in conn.execute(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = current_schema() AND table_name = ? "
                            "ORDER BY ordinal_position",
                            (table,),
                        ).fetchall()
                    ]
                    if not target_columns:
                        raise RuntimeError(
                            f"SQLite table {table!r} has no PostgreSQL schema counterpart."
                        )
                    unknown_columns = set(source_columns) - set(target_columns)
                    if unknown_columns:
                        raise RuntimeError(
                            f"SQLite table {table!r} has unmapped columns: "
                            f"{', '.join(sorted(unknown_columns))}"
                        )

                    columns = ", ".join(_quote_identifier(name) for name in source_columns)
                    placeholders = ", ".join("?" for _ in source_columns)
                    insert_sql = (
                        f"INSERT INTO {quoted} ({columns}) VALUES ({placeholders}) "
                        "ON CONFLICT DO NOTHING"
                    )
                    before = conn.execute(
                        f"SELECT COUNT(*) AS n FROM {quoted}"
                    ).fetchone()["n"]
                    cursor = source.execute(f"SELECT {columns} FROM {quoted}")
                    source_rows = 0
                    while batch := cursor.fetchmany(_BATCH_SIZE):
                        conn.executemany(
                            insert_sql,
                            [tuple(row) for row in batch],
                        )
                        source_rows += len(batch)
                    after = conn.execute(
                        f"SELECT COUNT(*) AS n FROM {quoted}"
                    ).fetchone()["n"]
                    table_reports[table] = {
                        "source_rows": source_rows,
                        "inserted_rows": after - before,
                    }

                for table in _ordered_tables(source):
                    quoted = _quote_identifier(table)
                    has_id = any(
                        row["name"] == "id"
                        for row in source.execute(f"PRAGMA table_info({quoted})")
                    )
                    if not has_id:
                        continue
                    sequence = conn.execute(
                        "SELECT pg_get_serial_sequence(?, 'id') AS sequence",
                        (table,),
                    ).fetchone()["sequence"]
                    if sequence:
                        conn.execute(
                            f"SELECT setval(?::regclass, "
                            f"COALESCE((SELECT MAX(id) FROM {quoted}), 1), "
                            f"EXISTS(SELECT 1 FROM {quoted}))",
                            (sequence,),
                        )

                conn.execute(
                    "INSERT INTO migration_imports "
                    "(source_key, source_fingerprint, records_json, imported_at_utc) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        source_key,
                        fingerprint,
                        json.dumps(table_reports, sort_keys=True),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                source.close()

            report = {
                "source": source_key,
                "status": "imported",
                "fingerprint": fingerprint,
                "records": table_reports,
            }
            reports.append(report)
            log.info(
                "Imported SQLite database %s (%d records across %d tables)",
                source_key,
                sum(item["inserted_rows"] for item in table_reports.values()),
                len(table_reports),
            )
    return reports
