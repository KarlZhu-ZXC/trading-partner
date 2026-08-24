from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import text

from infrastructure.persistence.database import create_engine_from_url


def test_sqlite_wal_handles_bounded_cross_process_style_writers(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'wal-concurrency.db'}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE wal_probe (writer INTEGER NOT NULL, ordinal INTEGER NOT NULL)")
        )

    def write_batch(writer: int) -> None:
        for ordinal in range(20):
            with engine.begin() as connection:
                connection.execute(
                    text("INSERT INTO wal_probe (writer, ordinal) VALUES (:writer, :ordinal)"),
                    {"writer": writer, "ordinal": ordinal},
                )

    with ThreadPoolExecutor(max_workers=8) as pool:
        tuple(pool.map(write_batch, range(8)))

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM wal_probe")).scalar_one() == 160
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        checkpoint = tuple(
            int(value)
            for value in connection.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)").one()
        )
    assert checkpoint[0] == 0
    assert checkpoint[1] >= checkpoint[2] >= 0
    engine.dispose()
