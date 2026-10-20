"""Bounded durable account context for note conversations; no provider/write ports."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, fields
from datetime import datetime
from decimal import Decimal
from typing import Any

from application.ports.account_snapshot_repository import AccountSnapshotRepository
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.instrument_unit_of_work import InstrumentUnitOfWork
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.instruments.models import Instrument
from domain.portfolio.models import AccountTransaction
from domain.portfolio.note_instruments import option_symbol_context


class NotePortfolioContext:
    TRANSACTION_FIELDS = tuple(field.name for field in fields(AccountTransaction))
    PAGE_SIZE = 6
    HISTORY_LIMIT = 1000
    COVERAGE_SCAN_LIMIT = 500

    def __init__(
        self,
        snapshots: AccountSnapshotRepository,
        transactions: AccountTransactionRepository,
        instrument_uow: Callable[[], InstrumentUnitOfWork] | None = None,
    ):
        self._snapshots, self._transactions = snapshots, transactions
        self._instrument_uow = instrument_uow

    def _coverage(self, accounts: set[tuple[VendorId, str]], *, now: datetime) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": "UNAVAILABLE",
            "basis": "DURABLE_SOURCE_WINDOWS_NOT_FULL_HISTORY",
            "receipts": [],
            "has_more": False,
            "account_scope": "KNOWN_CONTEXT_ACCOUNTS_ONLY",
            "warning_codes": [],
        }
        if not accounts:
            result["warning_codes"] = ["ACTIVITY_COVERAGE_ACCOUNT_SCOPE_UNAVAILABLE"]
            return result
        scoped = sorted(accounts, key=lambda value: (value[0].value, value[1]))[:200]
        try:
            rows = self._transactions.list_coverage(
                providers=tuple(
                    sorted({provider for provider, _ in scoped}, key=lambda p: p.value)
                ),
                account_refs=tuple(sorted({ref for _, ref in scoped})),
                limit=self.COVERAGE_SCAN_LIMIT + 1,
            )
            bounded = len(rows) > self.COVERAGE_SCAN_LIMIT or len(scoped) < len(accounts)
            valid = []
            warnings = set()
            if bounded:
                warnings.add("ACTIVITY_COVERAGE_SCAN_LIMIT_REACHED")
            for row in rows[: self.COVERAGE_SCAN_LIMIT]:
                if (row.provider, row.account_ref) not in accounts:
                    continue  # Never conflate equal account references across providers.
                if any(
                    value is not None and value > now
                    for value in (
                        row.fetched_at,
                        row.effective_end,
                        row.latest_event_at,
                        row.latest_snapshot_at,
                    )
                ):
                    warnings.add("FUTURE_ACTIVITY_COVERAGE_EXCLUDED")
                    continue
                valid.append(row)
            valid.sort(key=lambda row: (row.fetched_at, row.receipt_id), reverse=True)
            receipts = [
                {
                    "receipt_id": row.receipt_id,
                    "provider": row.provider.value,
                    "account_ref": row.account_ref,
                    "requested_start": row.requested_start.isoformat(),
                    "requested_end": row.requested_end.isoformat(),
                    "effective_start": row.effective_start.isoformat(),
                    "effective_end": row.effective_end.isoformat(),
                    "fetched_at": row.fetched_at.isoformat(),
                    "status": row.status.value,
                    "supported_kinds": [kind.value for kind in row.supported_kinds],
                    "unavailable_kinds": [kind.value for kind in row.unavailable_kinds],
                    "gap_codes": list(row.gap_codes),
                    "mapping_version": row.mapping_version,
                }
                for row in valid[: self.PAGE_SIZE]
            ]
            result.update(
                status="UNAVAILABLE"
                if not receipts
                else "PARTIAL"
                if bounded or len(valid) > self.PAGE_SIZE or warnings
                else "AVAILABLE",
                receipts=receipts,
                has_more=bounded or len(valid) > self.PAGE_SIZE,
                warning_codes=sorted(warnings),
            )
        except Exception:  # noqa: BLE001 - missing receipts must not erase source facts
            result["warning_codes"] = ["ACTIVITY_COVERAGE_UNAVAILABLE"]
        return result

    def get(
        self, instrument_id: str | None, *, now: datetime, cursor: str | None = None,
        transaction_start: datetime | None = None, transaction_end: datetime | None = None,
        transactions_only: bool = False,
    ) -> dict[str, Any]:
        if any(value is not None and value.utcoffset() is None
               for value in (transaction_start, transaction_end)):
            raise DataContractError("Transaction window must use aware times")
        if transaction_start and transaction_end and transaction_start > transaction_end:
            raise DataContractError("Transaction start must not follow end")
        if instrument_id is None:
            return {"status": "UNMAPPED", "items": [], "next_cursor": None}
        warnings = {"DURABLE_SNAPSHOTS_NOT_LIVE", "TRANSACTION_WINDOW_COVERAGE_UNVERIFIED"}
        masters: dict[str, Instrument] = {}
        metadata_status = "UNAVAILABLE"
        if self._instrument_uow is not None:
            try:
                with self._instrument_uow() as uow:
                    candidates = uow.instruments.note_option_candidates(instrument_id, limit=501)
                metadata_status = "AVAILABLE" if len(candidates) <= 500 else "PARTIAL"
                masters = {row.instrument_id: row for row in candidates[:500]}
            except Exception:  # noqa: BLE001 - source unavailable, retain explicitly unverified hints
                warnings.add("OPTION_MASTER_UNAVAILABLE")
        if metadata_status == "PARTIAL":
            warnings.add("OPTION_MASTER_LIMIT_REACHED")
        related_ids = tuple(
            key for key, row in masters.items() if row.underlying_instrument_id == instrument_id
        )
        excluded_ids = tuple(
            key
            for key, row in masters.items()
            if row.underlying_instrument_id not in (None, instrument_id)
        )

        def option_context(identity: str) -> dict[str, object] | None:
            if metadata_status == "PARTIAL" or identity in excluded_ids:
                return None
            hint = option_symbol_context(identity, instrument_id)
            master = masters.get(identity)
            if master is None or master.underlying_instrument_id != instrument_id:
                return hint
            return {
                "underlying_instrument_id": instrument_id,
                "expiration_date": hint["expiration_date"] if hint else None,
                "option_type": hint["option_type"] if hint else None,
                "strike": hint["strike"] if hint else None,
                "identity_basis": "DURABLE_INSTRUMENT_MASTER",
                "metadata_version": master.metadata_version,
                "multiplier": str(master.multiplier)
                if master.multiplier is not None and master.multiplier > 0
                else None,
                "terms_basis": "SYMBOL_HINTS_ONLY" if hint else "UNAVAILABLE",
                "delta": None,
                "warning_codes": ["OPTION_CONTRACT_TERMS_NOT_FULLY_VERIFIED"],
            }

        items: list[dict[str, Any]] = []
        snapshot_refs: list[str] = []
        accounts: set[tuple[VendorId, str]] = set()
        positions_status, transactions_status = "AVAILABLE", "AVAILABLE"
        try:
            snapshots = () if transactions_only else self._snapshots.latest_accounts()
            if not snapshots:
                positions_status = "UNAVAILABLE"
            for account in sorted(snapshots, key=lambda a: (a.provider.value, a.account_ref)):
                if account.fetched_at > now or account.account_as_of > now:
                    warnings.add("FUTURE_ACCOUNT_SNAPSHOT_EXCLUDED")
                    positions_status = "PARTIAL"
                    continue
                snapshot_refs.append(account.snapshot_id)
                accounts.add((account.provider, account.account_ref))
                for position in account.positions:
                    option = option_context(position.instrument_id)
                    if position.instrument_id != instrument_id and option is None:
                        if position.instrument_id.startswith("option:"):
                            warnings.add("OTHER_OPTION_POSITIONS_NOT_ASSOCIATED")
                        continue
                    codes = list(account.warning_codes)
                    if (
                        option is None
                        and position.side.value == "long"
                        and all(
                            v is not None
                            for v in (
                                position.market_value,
                                position.average_cost,
                                position.unrealized_pnl,
                            )
                        )
                    ):
                        assert position.market_value is not None
                        assert position.average_cost is not None
                        assert position.unrealized_pnl is not None
                        expected = position.market_value - position.quantity * position.average_cost
                        if abs(expected - position.unrealized_pnl) > Decimal("0.05"):
                            codes.append("BROKER_COST_PNL_BASIS_MISMATCH")
                    items.append(
                        {
                            "kind": "POSITION",
                            "provider": account.provider.value,
                            "account_ref": account.account_ref,
                            "snapshot_id": account.snapshot_id,
                            "account_as_of": account.account_as_of.isoformat(),
                            "fetched_at": account.fetched_at.isoformat(),
                            "age_seconds": int((now - account.fetched_at).total_seconds()),
                            "basis": "BROKER_REPORTED_SNAPSHOT",
                            "warning_codes": codes,
                            "position": asdict(position),
                            "option": option,
                        }
                    )
        except Exception:  # noqa: BLE001 - independent source failure remains visible
            items = []
            positions_status = "UNAVAILABLE"
        try:
            transactions = self._transactions.list_for_note(
                instrument_id,
                end=min(transaction_end, now) if transaction_end else now,
                limit=self.HISTORY_LIMIT + 1,
                related_instrument_ids=related_ids if metadata_status != "PARTIAL" else (),
                excluded_instrument_ids=excluded_ids,
                include_symbol_options=metadata_status != "PARTIAL",
            )
            if transaction_start:
                transactions = tuple(
                    row for row in transactions if row.occurred_at >= transaction_start
                )
            if len(transactions) > self.HISTORY_LIMIT:
                warnings.add("TRANSACTION_HISTORY_LIMIT_REACHED")
                transactions_status = "PARTIAL"
            accounts.update(
                (row.provider, row.account_ref) for row in transactions[: self.HISTORY_LIMIT]
            )
            items.extend(
                {
                    "kind": "TRANSACTION",
                    # Transaction fields are immutable scalar values; avoid deep-copying
                    # Decimals and aware timestamps across the full fingerprint window.
                    "transaction": {key: getattr(row, key) for key in self.TRANSACTION_FIELDS},
                    "option": option_context(row.instrument_id or ""),
                }
                for row in transactions[: self.HISTORY_LIMIT]
            )
        except Exception:  # noqa: BLE001 - retain positions if activity storage fails
            transactions_status = "UNAVAILABLE"
        coverage = self._coverage(accounts, now=now)
        # Pin page content, not wall time. Changed snapshots/backfills invalidate a
        # cursor instead of shifting rows underneath it. This is not a DB snapshot.
        stable = [{k: v for k, v in item.items() if k != "age_seconds"} for item in items]
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    instrument_id, transaction_start, transaction_end, transactions_only,
                    snapshot_refs,
                    positions_status,
                    transactions_status,
                    metadata_status,
                    coverage,
                    stable,
                ],
                default=str,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        offset = 0
        if cursor:
            try:
                token = json.loads(base64.urlsafe_b64decode(cursor))
                if token["fingerprint"] != fingerprint:
                    raise ValueError("changed")
                offset = token["offset"]
                if type(offset) is not int or not 0 <= offset < len(items):
                    raise ValueError("offset")
            except (ValueError, TypeError, KeyError):
                raise DataContractError(
                    "Account context changed or cursor is invalid; reload the first page"
                ) from None
        selected = items[offset : offset + self.PAGE_SIZE]
        next_offset = offset + len(selected)
        next_cursor = None
        if next_offset < len(items):
            next_cursor = base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "offset": next_offset,
                    }
                ).encode()
            ).decode()
        return {
            "status": "PARTIAL"
            if metadata_status == "PARTIAL"
            or any(
                status != "AVAILABLE" for status in (
                    (transactions_status,) if transactions_only
                    else (positions_status, transactions_status)
                )
            )
            else "AVAILABLE",
            "instrument_id": instrument_id,
            "window_start": transaction_start, "window_end": transaction_end,
            "option_metadata_status": metadata_status,
            "positions_status": positions_status,
            "transactions_status": transactions_status,
            "as_of": now.isoformat(),
            "basis": "DURABLE_CONTEXT_NOT_EXECUTION_LINK",
            "items": selected,
            "returned_count": len(selected),
            "total_available": len(items),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "warning_codes": sorted(warnings),
            "history_coverage": "UNVERIFIED",
            "transaction_coverage": coverage,
            "execution_effect": False,
        }
