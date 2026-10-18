from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from cne.db import database


def test_concurrent_database_initialization_is_serialized(tmp_path, monkeypatch):
    monkeypatch.setattr(
        database,
        "settings",
        replace(database.settings, data_dir=tmp_path, db_path=tmp_path / "shared.db"),
    )

    def initialize():
        connection = database.init_db()
        connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(initialize) for _ in range(2)]
        for future in futures:
            future.result()
