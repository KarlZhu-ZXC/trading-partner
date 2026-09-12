"""Complete local Journal reads; public MCP limits remain unchanged.

Reconstruction and overrides see all opening activity through the end cutoff.
Only then is the close/open date cohort selected, matching Behavior semantics.
The Console paginates this complete local cohort; no upstream calls occur here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from application.dto.account_transactions import AccountGetTransactionsInput, TradeCycleQueryInput
from application.services.account_transaction_coordinator import AccountTransactionCoordinator


async def journal_history(
    service: AccountTransactionCoordinator,
    *,
    account_refs: list[str],
    instrument_ids: list[str],
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    query = TradeCycleQueryInput(
        account_refs=tuple(account_refs),
        instrument_ids=tuple(instrument_ids),
        start=start,
        end=end,
    )
    transactions = service.list_durable_transactions(
        AccountGetTransactionsInput(end=end),
        complete_history=True,
    ).model_dump(mode="json")
    cycles = service.get_trade_cycles(query, complete_history=True).model_dump(mode="json")
    tx_data = transactions.get("data") or {}
    all_rows = tx_data.get("transactions", [])
    options = {
        "account_refs": sorted({row["account_ref"] for row in all_rows}),
        "instrument_ids": sorted(
            {row["instrument_id"] for row in all_rows if row["instrument_id"]}
        ),
    }
    scoped_rows = [
        row
        for row in all_rows
        if (not account_refs or row["account_ref"] in account_refs)
        and (not instrument_ids or row["instrument_id"] in instrument_ids)
    ]
    if transactions.get("ok"):
        # Keep pre-period fills separately for exact selected Cycle activity paths.
        tx_data["transactions"] = [
            row
            for row in scoped_rows
            if start is None or datetime.fromisoformat(row["occurred_at"]) >= start
        ]
        tx_data["history_complete"] = True  # local read completeness, not broker coverage
    cycle_data = cycles.get("data") or {}
    if cycles.get("ok"):
        cycle_data["cycles"] = [
            row for row in cycle_data.get("cycles", []) if _in_period(row, start, end)
        ]
        cycle_data["history_complete"] = True
    if transactions.get("ok"):
        cycle_activity_keys = {
            (row["provider"], row["account_ref"], activity_id)
            for row in cycle_data.get("cycles", [])
            for activity_id in row["activity_ids"]
        }
        tx_data["cycle_activities"] = [
            row
            for row in scoped_rows
            if start is not None
            and datetime.fromisoformat(row["occurred_at"]) < start
            and (row["provider"], row["account_ref"], row["provider_transaction_id"])
            in cycle_activity_keys
        ]
    return {"transactions": transactions, "trade_cycles": cycles, "filter_options": options}


def _in_period(row: dict[str, Any], start: datetime | None, end: datetime | None) -> bool:
    value = row.get("closed_at") or row.get("opened_at")
    if value is None:
        return start is None and end is None
    timestamp = datetime.fromisoformat(value)
    return (start is None or timestamp >= start) and (end is None or timestamp <= end)
