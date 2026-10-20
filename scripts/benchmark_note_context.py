#!/usr/bin/env python3
"""Compare materialized vs current note-ledger reads on disposable synthetic data.

No owner configuration, providers, models or private account data are loaded.
The reference preserves the original aware-time filtering and deterministic ordering.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, insert, or_, select
from sqlalchemy.orm import Session

from domain.portfolio.models import AccountTransaction
from domain.portfolio.note_instruments import option_symbol_context
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.orm import AccountTransactionRow

INSTRUMENT = "equity:US:SYNM"
AS_OF = datetime(2026, 10, 1, tzinfo=UTC)


def reference(repository: SqlAlchemyAccountTransactionRepository, engine, limit: int):
    statement = select(AccountTransactionRow).where(
        or_(
            AccountTransactionRow.instrument_id == INSTRUMENT,
            AccountTransactionRow.instrument_id.startswith("option:US:SYNM", autoescape=True),
        )
    )
    with Session(engine) as session:
        values = [repository._hydrate(row) for row in session.scalars(statement)]
    values = [
        row
        for row in values
        if row.occurred_at <= AS_OF
        and (
            row.instrument_id == INSTRUMENT
            or option_symbol_context(row.instrument_id or "", INSTRUMENT)
        )
    ]
    values.sort(
        key=lambda row: (
            row.occurred_at,
            row.provider.value,
            row.account_ref,
            row.provider_transaction_id,
        ),
        reverse=True,
    )
    return tuple(values[:limit])


def measure(read: Callable[[], tuple[AccountTransaction, ...]], repeats: int):
    read()  # warm SQLite/mapper; timings are not cold-start measurements
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        read()
        samples.append(time.perf_counter() - start)
    tracemalloc.start()
    read()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "seconds": samples,
        "median_seconds": statistics.median(samples),
        "python_peak_bytes": peak,
    }


def run(rows: int, repeats: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="tp-note-benchmark-") as directory:
        engine = create_engine(f"sqlite:///{directory}/synthetic.sqlite3")
        try:
            AccountTransactionRow.__table__.create(engine)
            with engine.begin() as connection:
                for first in range(0, rows, 500):
                    batch = []
                    for index in range(first, min(first + 500, rows)):
                        instant = AS_OF - timedelta(seconds=index % 1000)
                        batch.append(
                            {
                                "provider": "schwab",
                                "account_ref": f"synthetic_{index % 3}",
                                "provider_transaction_id": f"synthetic_{index:08d}",
                                "instrument_id": INSTRUMENT
                                if index % 3
                                else "option:US:SYNM261218C00120000",
                                "kind": "trade",
                                "side": "buy",
                                "quantity": "1",
                                "price": "100",
                                "fees": None,
                                "currency": "USD",
                                "occurred_at": instant.isoformat(),
                                "cash_amount": None,
                                "source_type": "SYNTHETIC",
                                "mapping_version": "synthetic_v1",
                            }
                        )
                    connection.execute(insert(AccountTransactionRow), batch)
            repository = SqlAlchemyAccountTransactionRepository(engine)

            def old():
                return reference(repository, engine, 1001)

            def current():
                return repository.list_for_note(INSTRUMENT, end=AS_OF, limit=1001)

            assert old() == current(), "Result/order changed; measurements are invalid"
            return {
                "schema": "note-ledger-benchmark-v1",
                "rows": rows,
                "limit": 1001,
                "result_equal": True,
                "reference": measure(old, repeats),
                "current": measure(current, repeats),
                "boundary": "warm isolated SQLite repository read; Python allocation peak, not RSS",
            }
        finally:
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.rows <= 100000 or not 1 <= args.repeats <= 10:
        parser.error("rows must be 1..100000 and repeats 1..10")
    result = run(args.rows, args.repeats)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
