import sqlite3

from cne.db.migration import _ordered_tables, _snapshot


def test_snapshot_includes_committed_wal_records(tmp_path):
    source_path = tmp_path / "source.sqlite"
    snapshot_path = tmp_path / "snapshot.sqlite"
    source = sqlite3.connect(source_path)
    source.execute("PRAGMA journal_mode=WAL")
    source.execute("CREATE TABLE records (value TEXT NOT NULL)")
    source.execute("INSERT INTO records VALUES ('preserved')")
    source.commit()
    try:
        _snapshot(source_path, snapshot_path)
    finally:
        source.close()

    snapshot = sqlite3.connect(snapshot_path)
    try:
        assert snapshot.execute("SELECT value FROM records").fetchone()[0] == "preserved"
    finally:
        snapshot.close()


def test_source_tables_are_ordered_by_foreign_key_dependencies(tmp_path):
    source = sqlite3.connect(tmp_path / "source.sqlite")
    source.execute(
        "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))"
    )
    source.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    try:
        assert _ordered_tables(source) == ["parent", "child"]
    finally:
        source.close()
