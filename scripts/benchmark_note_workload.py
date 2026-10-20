#!/usr/bin/env python3
"""Measure synthetic Note Desk reads, SQL counts and memory after a bounded workload.

Fresh sample processes have an allowlisted environment, a disposable current ORM
schema and blocked networking. Fixture writes are excluded from read timings/counts;
memory includes imports and fixture preparation. No owner data or model is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

NOW = datetime(2026, 10, 1, tzinfo=UTC)
STOCK = "equity:US:SYNM"


class FixtureClock:
    def now(self) -> datetime:
        return NOW


def sample(notes: int, transactions: int) -> dict:
    import socket

    def block(*args, **kwargs):
        raise AssertionError("synthetic note workload must not contact the network")

    socket.socket.connect = block
    socket.socket.connect_ex = block
    from sqlalchemy import event, insert

    from application.services.note_desk_service import NoteDeskService
    from application.services.note_portfolio_context import NotePortfolioContext
    from bootstrap import build_application
    from domain.common.enums import VendorId
    from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
    from domain.external_note.models import (
        AttributedNoteBlock,
        ExternalNoteIdentity,
        ExternalNoteRevision,
    )
    from domain.portfolio.enums import AccountEnvironment
    from domain.portfolio.models import AccountSnapshot
    from infrastructure.config.settings import AppSettings
    from infrastructure.persistence.account_snapshot_repository import (
        SqlAlchemyAccountSnapshotRepository,
    )
    from infrastructure.persistence.account_transaction_repository import (
        SqlAlchemyAccountTransactionRepository,
    )
    from infrastructure.persistence.external_note_repository import SqlAlchemyExternalNoteRepository
    from infrastructure.persistence.metadata import Base
    from infrastructure.persistence.orm import AccountTransactionRow
    from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
    from infrastructure.system.id_generator import Uuid7IdGenerator
    from infrastructure.system.redactor import DefaultSecretRedactor

    with tempfile.TemporaryDirectory(prefix="tp-note-workload-") as directory:
        settings = AppSettings(
            _env_file=None,
            app_name="synthetic-workload",
            app_env="test",
            log_level="INFO",
            database_url=f"sqlite:///{directory}/fixture.sqlite3",
            mcp_server_name="synthetic",
            default_timezone="UTC",
            provider_timeout_seconds=5,
            runtime_root=Path(directory),
        )
        container = build_application(settings)
        engine = container.resources.database.engine
        try:
            Base.metadata.create_all(engine)
            repository = SqlAlchemyExternalNoteRepository(engine)
            for index in range(notes):
                note_id = f"synthetic_note_{index}"
                at = NOW - timedelta(seconds=index)
                repository.append_identity(
                    ExternalNoteIdentity(
                        note_id,
                        "MOOMOO_NOTE",
                        str(index),
                        "Synthetic note",
                        STOCK,
                        at,
                        at,
                    )
                )
                for version in (1, 2):
                    body = f"Synthetic note {index}, version {version}"
                    repository.append_revision(
                        ExternalNoteRevision(
                            f"synthetic_revision_{index}_{version}",
                            note_id,
                            version,
                            hashlib.sha256(body.encode()).hexdigest(),
                            str(version),
                            "Synthetic note",
                            "Synthetic summary",
                            body,
                            NoteCoverage.FULL,
                            at,
                            at,
                            "PRIVATE",
                            (),
                            (),
                            (AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", body),),
                        )
                    )
            snapshots = SqlAlchemyAccountSnapshotRepository(engine)
            snapshots.append_account(
                AccountSnapshot(
                    "synthetic_snapshot",
                    "synthetic_account",
                    VendorId.SCHWAB,
                    AccountEnvironment.REAL,
                    "USD",
                    NOW,
                    NOW,
                    None,
                    None,
                    None,
                    None,
                    (),
                    (),
                    False,
                    (),
                )
            )
            with engine.begin() as connection:
                for first in range(0, transactions, 500):
                    connection.execute(
                        insert(AccountTransactionRow),
                        [
                            {
                                "provider": "schwab",
                                "account_ref": "synthetic_account",
                                "provider_transaction_id": f"synthetic_{index:08d}",
                                "instrument_id": STOCK,
                                "kind": "trade",
                                "side": "buy",
                                "quantity": "1",
                                "price": "100",
                                "fees": None,
                                "currency": "USD",
                                "occurred_at": (NOW - timedelta(seconds=index)).isoformat(),
                                "cash_amount": None,
                                "source_type": "SYNTHETIC",
                                "mapping_version": "synthetic_v1",
                            }
                            for index in range(first, min(first + 500, transactions))
                        ],
                    )
            clock, redactor = FixtureClock(), DefaultSecretRedactor()
            desk = NoteDeskService(
                repository,
                lambda: SqlAlchemyResearchUnitOfWork(engine, clock, Uuid7IdGenerator(), redactor),
                clock,
                redactor,
                NotePortfolioContext(snapshots, SqlAlchemyAccountTransactionRepository(engine)),
            )
            statements: list[str] = []

            def capture(connection, cursor, statement, parameters, context, executemany):
                statements.append(statement.lstrip().split()[0].upper())

            event.listen(engine, "before_cursor_execute", capture)
            phases = {}

            def measure(name, read):
                statements.clear()
                started = time.perf_counter()
                result = read()
                phases[name] = {
                    "seconds": time.perf_counter() - started,
                    "selects": statements.count("SELECT"),
                    "business_writes": sum(
                        statements.count(v) for v in ("INSERT", "UPDATE", "DELETE")
                    ),
                }
                assert phases[name]["business_writes"] == 0
                return result

            inbox = measure("inbox", lambda: desk.inbox(limit=30))
            assert len(inbox["items"]) == min(notes, 30)
            note = measure("note", lambda: desk.note("synthetic_revision_0_2"))
            assert note["content"] is None
            context = note["portfolio_context"]
            assert context["history_coverage"] == "UNVERIFIED"
            if context["next_cursor"]:
                measure(
                    "context_next",
                    lambda: desk.note(
                        "synthetic_revision_0_2",
                        context_cursor=context["next_cursor"],
                    ),
                )
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            loaded_rss = None
            try:
                rss = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(os.getpid())],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=5,
                )
                if rss.returncode == 0 and rss.stdout.strip().isdecimal():
                    loaded_rss = int(rss.stdout.strip()) * 1024
            except (OSError, subprocess.TimeoutExpired):
                pass  # Restricted hosts must report unknown, not substitute peak RSS.
            return {
                "phases": phases,
                "peak_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
                "business_loaded_rss_bytes": loaded_rss,
                "business_loaded_rss_basis": "ps_after_reads" if loaded_rss else "unavailable",
                "context_total_available": context["total_available"],
                "context_returned": context["returned_count"],
                "loaded_numerical_modules": [
                    name for name in ("numpy", "pandas", "talib", "yfinance") if name in sys.modules
                ],
            }
        finally:
            container.close()


def run(notes: int, transactions: int, repeats: int) -> dict:
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT") if key in os.environ}
    environment.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"), PYTHONDONTWRITEBYTECODE="1"
    )
    samples = []
    with tempfile.TemporaryDirectory(prefix="tp-note-workload-runner-") as directory:
        environment["TMPDIR"] = directory
        for _ in range(repeats):
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--sample",
                    "--notes",
                    str(notes),
                    "--transactions",
                    str(transactions),
                ],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if result.returncode:
                raise RuntimeError(f"Synthetic workload sample failed: {result.stderr}")
            samples.append(json.loads(result.stdout))
    return {
        "schema": "note-workload-benchmark-v1",
        "fixture": {
            "notes": notes,
            "revisions_per_note": 2,
            "transactions": transactions,
            "accounts": 1,
            "positions": 0,
            "inbox_limit": 30,
        },
        "boundary": (
            "fresh process; warm synthetic SQLite reads; memory includes fixture setup; "
            "excludes UI, model discussion, save and actual human task time"
        ),
        "samples": samples,
        "median_phase_seconds": {
            name: statistics.median(row["phases"][name]["seconds"] for row in samples)
            for name in samples[0]["phases"]
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--notes", type=int, default=200)
    parser.add_argument("--transactions", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (
        not 1 <= args.notes <= 500
        or not 0 <= args.transactions <= 100000
        or not 1 <= args.repeats <= 10
    ):
        parser.error("notes must be 1..500, transactions 0..100000, repeats 1..10")
    result = (
        sample(args.notes, args.transactions)
        if args.sample
        else run(args.notes, args.transactions, args.repeats)
    )
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
