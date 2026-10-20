"""Synthetic account joins and stable, non-writing note context pages."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text

from application.services.note_portfolio_context import NotePortfolioContext
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.portfolio.enums import (
    AccountEnvironment,
    AccountPositionSide,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountPosition, AccountSnapshot, AccountTransaction
from domain.portfolio.note_instruments import option_symbol_context
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.metadata import Base
from interfaces.shared.result_compaction import compact_mcp_result

NOW = datetime(2026, 10, 1, tzinfo=UTC)
STOCK = "equity:US:SYNM"
OPTION = "option:US:SYNM261218C00120000"


def position(instrument=STOCK):
    return AccountPosition(
        instrument,
        AccountPositionSide.LONG,
        Decimal(3),
        None,
        Decimal(100),
        None,
        None,
        None,
        Decimal(330),
        Decimal(17),
        None,
        "USD",
    )


def account(ref, positions):
    return AccountSnapshot(
        "snapshot_" + ref,
        ref,
        VendorId.SCHWAB,
        AccountEnvironment.REAL,
        "USD",
        NOW,
        NOW,
        None,
        None,
        None,
        None,
        positions,
        (),
        False,
        (),
    )


def transaction(index, instrument=STOCK, ref="synthetic_account"):
    return AccountTransaction(
        str(index),
        ref,
        VendorId.SCHWAB,
        instrument,
        AccountTransactionKind.TRADE,
        AccountTransactionSide.BUY,
        Decimal(1),
        Decimal(100),
        None,
        "USD",
        NOW,
    )


def coverage_receipt(identity, *, ref="one", provider=VendorId.SCHWAB, at=NOW):
    from domain.portfolio.enums import AccountActivityCoverageStatus
    from domain.portfolio.models import AccountActivityCoverageReceipt

    return AccountActivityCoverageReceipt(
        receipt_id=identity,
        provider=provider,
        account_ref=ref,
        requested_start=NOW - timedelta(days=30),
        requested_end=NOW,
        effective_start=NOW - timedelta(days=14),
        effective_end=NOW,
        earliest_event_at=None,
        latest_event_at=None,
        event_count=0,
        inserted_count=0,
        duplicate_count=0,
        snapshot_count=0,
        earliest_snapshot_at=None,
        latest_snapshot_at=None,
        mapping_version="synthetic_v1",
        supported_kinds=(AccountTransactionKind.TRADE,),
        unavailable_kinds=(AccountTransactionKind.DIVIDEND,),
        status=AccountActivityCoverageStatus.INCOMPLETE,
        gap_codes=("SYNTHETIC_HISTORY_WINDOW_LIMIT",),
        fetched_at=at,
    )


def service():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountTransactionRepository(engine)
    snapshots = MagicMock()
    snapshots.latest_accounts.return_value = (
        account("one", (position(),)),
        account("two", (position(OPTION),)),
    )
    return engine, repository, snapshots, NotePortfolioContext(snapshots, repository)


def test_related_activity_is_filtered_before_limit_and_context_pages_do_not_write():
    engine, repository, _, context = service()
    repository.append_many(
        tuple(transaction(i, "equity:US:OTHER", ref="unrelated_account") for i in range(1100))
    )
    repository.append_many(tuple(transaction(i) for i in range(15)))
    repository.append_many(
        (transaction(20, OPTION), transaction(21, "option:US:SYNMORE261218C00120000"))
    )
    writes = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda c, cur, stmt, p, ctx, many: (
            writes.append(stmt)
            if stmt.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
            else None
        ),
    )
    result = context.get(STOCK, now=NOW)
    rows = list(result["items"])
    while result["next_cursor"]:
        result = context.get(STOCK, now=NOW + timedelta(seconds=1), cursor=result["next_cursor"])
        rows.extend(result["items"])
    assert len(rows) == 18
    assert (
        len(
            {
                (r["transaction"]["account_ref"], r["transaction"]["provider_transaction_id"])
                for r in rows
                if r["kind"] == "TRANSACTION"
            }
        )
        == 16
    )
    assert not writes
    assert rows[0]["warning_codes"] == ["BROKER_COST_PNL_BASIS_MISMATCH"]
    assert rows[1]["option"]["multiplier"] is None
    assert result["history_coverage"] == "UNVERIFIED"
    engine.dispose()


def test_backfill_or_snapshot_change_invalidates_cursor_instead_of_skipping_rows():
    engine, repository, snapshots, context = service()
    repository.append_many(tuple(transaction(i) for i in range(8)))
    page = context.get(STOCK, now=NOW)
    snapshots.latest_accounts.return_value = (account("changed", (position(),)),)
    with pytest.raises(DataContractError, match="changed"):
        context.get(STOCK, now=NOW, cursor=page["next_cursor"])
    for cursor in ["!", "W10=", "bnVsbA=="]:
        with pytest.raises(DataContractError):
            context.get(STOCK, now=NOW, cursor=cursor)
    engine.dispose()


def test_source_failure_and_unmapped_identity_do_not_claim_no_holdings():
    engine, repository, snapshots, context = service()
    assert context.get(None, now=NOW)["status"] == "UNMAPPED"
    snapshots.latest_accounts.side_effect = RuntimeError("private source failure")
    repository.append_many((transaction(1),))
    result = context.get(STOCK, now=NOW)
    assert result["positions_status"] == "UNAVAILABLE" and len(result["items"]) == 1
    assert "private source" not in str(result)
    engine.dispose()


def test_durable_receipt_windows_keep_exact_sources_and_do_not_certify_full_history():
    engine, repository, snapshots, context = service()
    try:
        snapshots.latest_accounts.return_value = (
            account("one", (position(),)),
            replace(account("two", (position(),)), provider=VendorId.MOOMOO),
        )
        repository.append_coverage(
            (
                coverage_receipt("synthetic_known"),
                coverage_receipt("synthetic_wrong_provider", provider=VendorId.MOOMOO),
                coverage_receipt("synthetic_future", at=NOW + timedelta(seconds=1)),
            )
        )
        value = context.get(STOCK, now=NOW)
        coverage = value["transaction_coverage"]
        assert coverage["status"] == "PARTIAL"
        assert coverage["warning_codes"] == ["FUTURE_ACTIVITY_COVERAGE_EXCLUDED"]
        assert len(coverage["receipts"]) == 1
        receipt = coverage["receipts"][0]
        assert receipt["receipt_id"] == "synthetic_known"
        assert receipt["provider"] == "schwab" and receipt["account_ref"] == "one"
        assert receipt["requested_start"] == (NOW - timedelta(days=30)).isoformat()
        assert receipt["effective_start"] == (NOW - timedelta(days=14)).isoformat()
        assert receipt["unavailable_kinds"] == ["dividend"]
        assert receipt["gap_codes"] == ["SYNTHETIC_HISTORY_WINDOW_LIMIT"]
        assert value["history_coverage"] == "UNVERIFIED"
    finally:
        engine.dispose()


def test_coverage_backfill_invalidates_existing_context_cursor_and_is_bounded():
    engine, repository, _, context = service()
    try:
        repository.append_many(tuple(transaction(i) for i in range(8)))
        previous = context.get(STOCK, now=NOW)
        assert previous["transaction_coverage"]["status"] == "UNAVAILABLE"
        repository.append_coverage(
            tuple(
                coverage_receipt(f"synthetic_receipt_{i}", at=NOW - timedelta(seconds=i))
                for i in range(7)
            )
        )
        with pytest.raises(DataContractError, match="changed"):
            context.get(STOCK, now=NOW, cursor=previous["next_cursor"])
        current = context.get(STOCK, now=NOW)
        assert len(current["transaction_coverage"]["receipts"]) == 6
        assert current["transaction_coverage"]["has_more"]
        assert current["transaction_coverage"]["status"] == "PARTIAL"
        assert current["history_coverage"] == "UNVERIFIED"
    finally:
        engine.dispose()


def test_coverage_failure_does_not_erase_positions_or_transactions(monkeypatch):
    engine, repository, _, context = service()
    try:
        repository.append_many((transaction(1),))

        def unavailable(**kwargs):
            raise RuntimeError("sensitive upstream detail")

        monkeypatch.setattr(repository, "list_coverage", unavailable)
        current = context.get(STOCK, now=NOW)
        assert len(current["items"]) == 3
        assert current["positions_status"] == current["transactions_status"] == "AVAILABLE"
        assert current["transaction_coverage"]["status"] == "UNAVAILABLE"
        assert current["transaction_coverage"]["warning_codes"] == ["ACTIVITY_COVERAGE_UNAVAILABLE"]
        assert "sensitive" not in str(current)
    finally:
        engine.dispose()


def test_coverage_scan_overflow_remains_partial_and_caps_transport(monkeypatch):
    engine, repository, _, context = service()
    try:
        query = MagicMock(
            return_value=tuple(
                coverage_receipt(f"synthetic_{i}") for i in range(context.COVERAGE_SCAN_LIMIT + 1)
            )
        )
        monkeypatch.setattr(repository, "list_coverage", query)
        value = context.get(STOCK, now=NOW)["transaction_coverage"]
        assert query.call_args.kwargs["limit"] == 501
        assert value["status"] == "PARTIAL" and value["has_more"]
        assert len(value["receipts"]) == 6
        assert value["warning_codes"] == ["ACTIVITY_COVERAGE_SCAN_LIMIT_REACHED"]
    finally:
        engine.dispose()


def test_streaming_read_still_validates_corrupt_future_candidates():
    engine, repository, _, _ = service()
    try:
        repository.append_many((replace(transaction(1), occurred_at=NOW + timedelta(days=1)),))
        with engine.begin() as connection:
            connection.execute(text("UPDATE account_transactions SET quantity = '0'"))
        with pytest.raises(DataContractError, match="positive"):
            repository.list_for_note(STOCK, end=NOW, limit=1)
    finally:
        engine.dispose()


def test_legacy_timezone_and_future_facts_are_filtered_by_instant():
    engine, repository, snapshots, context = service()
    repository.append_many(
        (transaction(1), replace(transaction(2), occurred_at=NOW + timedelta(days=1)))
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE account_transactions SET occurred_at = :at "
                "WHERE provider_transaction_id = '1'"
            ),
            {"at": "2026-10-01T08:00:00+08:00"},
        )
    snapshots.latest_accounts.return_value = (
        replace(
            account("future", (position(),)),
            account_as_of=NOW + timedelta(days=1),
            fetched_at=NOW + timedelta(days=1),
        ),
    )
    result = context.get(STOCK, now=NOW)
    assert (
        len(result["items"]) == 1
        and result["items"][0]["transaction"]["provider_transaction_id"] == "1"
    )
    assert result["positions_status"] == "PARTIAL"
    engine.dispose()


@pytest.mark.parametrize(
    "symbol",
    [
        "option:US:SYNM1261218C00120000",
        "option:US:SYNM261332C00120000",
        "option:US:SYNMORE261218C00120000",
    ],
)
def test_ambiguous_adjusted_or_other_option_roots_do_not_match(symbol):
    assert option_symbol_context(symbol, STOCK) is None


def test_compaction_never_preserves_cursor_after_dropping_financial_rows():
    engine, repository, _, context = service()
    repository.append_many(tuple(transaction(i) for i in range(10)))
    value = {
        "ok": True,
        "data": {"note_revision_id": "synthetic", "portfolio_context": context.get(STOCK, now=NOW)},
    }
    compact = compact_mcp_result(
        value, capability="view_get", arguments={"request": {"operation": "note"}}, max_bytes=1500
    )
    page = compact["data"]["portfolio_context"]
    assert page["status"] == "UNAVAILABLE" and page["next_cursor"] is None and page["items"] == []
    engine.dispose()


def test_technical_adapters_do_not_load_optional_engines_until_used():
    import subprocess
    import sys

    script = """
import sys
from infrastructure.technical import TALibIndicatorEngine, MatplotlibChartRenderer
TALibIndicatorEngine(); MatplotlibChartRenderer()
assert 'talib' not in sys.modules
assert 'numpy' not in sys.modules
assert 'matplotlib' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_large_ledger_streams_core_batches_and_preserves_exact_newest_set():
    from sqlalchemy import insert
    from sqlalchemy.orm import Session

    from infrastructure.persistence.orm import AccountTransactionRow

    engine, repository, _, _ = service()
    with engine.begin() as connection:
        connection.execute(
            insert(AccountTransactionRow),
            [
                {
                    "provider": "schwab",
                    "account_ref": f"synthetic_{i % 2}",
                    "provider_transaction_id": f"transaction_{i:04d}",
                    "instrument_id": STOCK,
                    "kind": "trade",
                    "side": "buy",
                    "quantity": "1",
                    "price": "100",
                    "fees": None,
                    "currency": "USD",
                    "occurred_at": NOW.isoformat(),
                    "source_type": "SYNTHETIC",
                    "mapping_version": "synthetic_v1",
                }
                for i in range(3000)
            ],
        )
    retained = []

    def loaded(session, instance):
        if isinstance(instance, AccountTransactionRow):
            retained.append(len(session.identity_map))

    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        rows = repository.list_for_note(STOCK, end=NOW, limit=7)
    finally:
        event.remove(Session, "loaded_as_persistent", loaded)
    assert not retained  # Core mappings avoid an ORM instance per candidate row.
    assert [(r.account_ref, r.provider_transaction_id) for r in rows] == [
        ("synthetic_1", f"transaction_{i:04d}") for i in range(2999, 2985, -2)
    ]
    engine.dispose()


def test_streaming_order_preserves_microseconds_offsets_and_cross_account_ties():
    engine, repository, _, _ = service()
    instant = NOW - timedelta(seconds=1)
    repository.append_many(
        (
            replace(transaction(1, ref="synthetic_a"), occurred_at=instant),
            replace(transaction(1, ref="synthetic_z"), occurred_at=instant),
            replace(transaction(2), occurred_at=instant + timedelta(microseconds=100)),
            replace(transaction(3), occurred_at=instant + timedelta(microseconds=200)),
            replace(transaction(4), occurred_at=NOW + timedelta(microseconds=1)),
            transaction(5, "option:US:SYNMORE261218C00120000"),
        )
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE account_transactions SET occurred_at = :at "
                "WHERE provider_transaction_id = '3'"
            ),
            {"at": "2026-10-01T07:59:59.000200+08:00"},
        )
    rows = repository.list_for_note(STOCK, end=NOW, limit=4)
    assert [(row.provider_transaction_id, row.account_ref) for row in rows] == [
        ("3", "synthetic_account"),
        ("2", "synthetic_account"),
        ("1", "synthetic_z"),
        ("1", "synthetic_a"),
    ]
    engine.dispose()


def test_durable_option_master_includes_adjusted_contracts_and_excludes_conflicting_roots():
    from conftest import FixedClock
    from domain.common.enums import AssetType, Market
    from domain.instruments.models import Instrument
    from infrastructure.persistence.instrument_unit_of_work import SqlAlchemyInstrumentUnitOfWork

    engine, repository, snapshots, _ = service()
    adjusted = "option:US:SYNM1261218C00120000"
    other = "equity:US:OTHER"

    def instrument(identity, underlying=None, multiplier=None):
        asset, _, symbol = identity.split(":", 2)
        return Instrument(
            identity,
            symbol,
            "Synthetic contract",
            Market.US,
            "SYNTHETIC",
            "USD",
            "UTC",
            AssetType(asset),
            underlying_instrument_id=underlying,
            multiplier=multiplier,
        )

    def uow_factory():
        return SqlAlchemyInstrumentUnitOfWork(engine, FixedClock(NOW))

    with uow_factory() as uow:
        for row in [
            instrument(STOCK),
            instrument(other),
            instrument(adjusted, STOCK, Decimal(25)),
            instrument(OPTION, other, Decimal(100)),
        ]:
            uow.instruments.upsert_instrument(row)
        uow.commit()
    snapshots.latest_accounts.return_value = (
        account("synthetic", (position(adjusted), position(OPTION))),
    )
    repository.append_many((transaction(1, adjusted), transaction(2, OPTION)))
    context = NotePortfolioContext(snapshots, repository, uow_factory)
    result = context.get(STOCK, now=NOW)
    assert result["option_metadata_status"] == "AVAILABLE"
    assert len(result["items"]) == 2
    for row in result["items"]:
        assert row["option"]["identity_basis"] == "DURABLE_INSTRUMENT_MASTER"
        assert row["option"]["multiplier"] == "25"
        assert row["option"]["expiration_date"] is None
        assert row["option"]["strike"] is None and row["option"]["delta"] is None
    engine.dispose()


def test_incomplete_master_does_not_fall_back_to_possibly_conflicting_option_symbols():
    engine, repository, snapshots, _ = service()
    uow = MagicMock()
    uow.__enter__.return_value = uow
    master = MagicMock(instrument_id=OPTION, underlying_instrument_id=STOCK)
    uow.instruments.note_option_candidates.return_value = tuple(master for _ in range(501))
    repository.append_many((transaction(1), transaction(2, OPTION)))
    context = NotePortfolioContext(snapshots, repository, lambda: uow)
    result = context.get(STOCK, now=NOW)
    assert result["status"] == "PARTIAL" and result["option_metadata_status"] == "PARTIAL"
    assert all(row["option"] is None for row in result["items"])
    assert len(result["items"]) == 2  # stock position and stock trade only
    uow.commit.assert_not_called()
    engine.dispose()


def test_note_history_period_uses_only_saved_transactions_and_binds_cursor_to_window():
    engine, repository, snapshots, context = service()
    try:
        rows = tuple(transaction(i) for i in range(1, 11))
        repository.append_many(rows)
        start = min(row.occurred_at for row in rows)
        end = max(row.occurred_at for row in rows)
        snapshots.latest_accounts = MagicMock(side_effect=AssertionError("No position read"))
        page = context.get(STOCK, now=NOW, transaction_start=start, transaction_end=end,
                           transactions_only=True)
        assert page["transactions_status"] == "AVAILABLE"
        assert all(row["kind"] == "TRANSACTION" for row in page["items"])
        assert page["next_cursor"]
        snapshots.latest_accounts.assert_not_called()
        with pytest.raises(DataContractError):
            context.get(STOCK, now=NOW, transaction_start=start - timedelta(days=1),
                        transaction_end=end, transactions_only=True, cursor=page["next_cursor"])
        empty = context.get(STOCK, now=NOW, transaction_start=start - timedelta(days=2),
                            transaction_end=start - timedelta(days=1), transactions_only=True)
        assert empty["items"] == []
        with pytest.raises(DataContractError):
            context.get(STOCK, now=NOW, transaction_start=NOW.replace(tzinfo=None))
    finally:
        engine.dispose()
