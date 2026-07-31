"""Strict owner-only parser for Schwab Realized Gain/Loss lot-detail CSV exports."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from domain.attribution.reconciliation_models import (
    BrokerRealizedAccountSummary,
    BrokerRealizedLot,
    BrokerRealizedStatement,
)
from domain.common.errors import DataContractError

_MAX_FILE_BYTES = 10 * 1024 * 1024
_MISSING = frozenset({"", "-", "--", "n/a", "na", "not available"})
_AGGREGATE_SYMBOLS = frozenset(
    {
        "total",
        "subtotal",
        "security subtotal",
        "short term total",
        "long term total",
    }
)
_TITLE_MARKERS = (
    "realized gain/loss",
    "realized gain or (loss)",
    "lot details",
)


def _normalized_header(value: str) -> str:
    text = value.strip().lower().replace("&", " and ")
    text = text.replace("$", " dollar ").replace("%", " percent ")
    text = re.sub(r"[()]", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


_ALIASES: dict[str, frozenset[str]] = {
    "symbol": frozenset({"symbol", "ticker"}),
    "opened_date": frozenset(
        {"opened date", "acquired opened date", "date acquired", "open date"}
    ),
    "closed_date": frozenset(
        {"closed date", "closed date time", "date sold", "close date"}
    ),
    "quantity": frozenset({"quantity", "quantity par", "shares"}),
    "total_proceeds": frozenset({"total proceeds", "proceeds"}),
    "cost_basis": frozenset({"cost basis", "cost basis adjusted", "cost"}),
    "realized_pnl": frozenset(
        {
            "gain loss",
            "gain loss dollar",
            "realized p l",
            "realized p and l",
            "realized dollar p and l",
            "realized gain loss",
            "realized gain or loss adjusted",
        }
    ),
    "long_term_pnl": frozenset({"lt gain loss", "long term gain loss"}),
    "short_term_pnl": frozenset({"st gain loss", "short term gain loss"}),
    "term": frozenset({"term", "hold period"}),
    "cost_basis_method": frozenset(
        {"cost basis method", "cost basis method used", "cost method"}
    ),
    "wash_sale_disallowed": frozenset(
        {"disallowed loss", "wash sale loss disallowed", "wash sale adjustment"}
    ),
}
_REQUIRED = frozenset(
    {
        "symbol",
        "closed_date",
        "quantity",
        "total_proceeds",
        "cost_basis",
        "realized_pnl",
    }
)


def _column_map(row: list[str]) -> dict[str, int] | None:
    normalized = [_normalized_header(item) for item in row]
    mapping: dict[str, int] = {}
    for canonical, aliases in _ALIASES.items():
        positions = [idx for idx, value in enumerate(normalized) if value in aliases]
        if len(positions) > 1:
            raise DataContractError(
                "Schwab statement contains duplicate recognized columns",
                code="SCHWAB_STATEMENT_FORMAT_ERROR",
                details={"column": canonical},
            )
        if positions:
            mapping[canonical] = positions[0]
    return mapping if _REQUIRED.issubset(mapping) else None


def _cell(row: list[str], mapping: dict[str, int], field: str) -> str:
    index = mapping.get(field)
    return row[index].strip() if index is not None and index < len(row) else ""


def _optional_decimal(value: str, *, field: str) -> Decimal | None:
    text = value.strip()
    if text.lower() in _MISSING:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.replace("$", "").replace(",", "").replace(" ", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise DataContractError(
            "Schwab statement contains an invalid numeric value",
            code="SCHWAB_STATEMENT_FORMAT_ERROR",
            details={"field": field},
        ) from None
    if not parsed.is_finite():
        raise DataContractError(
            "Schwab statement contains a non-finite numeric value",
            code="SCHWAB_STATEMENT_FORMAT_ERROR",
            details={"field": field},
        )
    return -parsed if negative and parsed > 0 else parsed


def _required_decimal(value: str, *, field: str) -> Decimal:
    parsed = _optional_decimal(value, field=field)
    if parsed is None:
        raise DataContractError(
            "Schwab statement is missing a required numeric value",
            code="SCHWAB_STATEMENT_FORMAT_ERROR",
            details={"field": field},
        )
    return parsed


def _optional_date(value: str, *, field: str) -> date | None:
    text = value.strip()
    if text.lower() in _MISSING:
        return None
    date_part = text.split(" as of", 1)[0].split(" ", 1)[0]
    for pattern in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(date_part, pattern).date()
        except ValueError:
            continue
    raise DataContractError(
        "Schwab statement contains an invalid date",
        code="SCHWAB_STATEMENT_FORMAT_ERROR",
        details={"field": field},
    )


def _required_date(value: str, *, field: str) -> date:
    parsed = _optional_date(value, field=field)
    if parsed is None:
        raise DataContractError(
            "Schwab statement is missing a required date",
            code="SCHWAB_STATEMENT_FORMAT_ERROR",
            details={"field": field},
        )
    return parsed


def _account_ref(label: str, *, section: int, source_sha256: str) -> str:
    identity = label.strip() or f"unlabeled:{section}:{source_sha256}"
    digest = hashlib.sha256(f"schwab:statement-account:{identity}".encode()).hexdigest()[:32]
    return f"schwab_statement_{digest}"


def _complete_sum(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal(0))


class SchwabRealizedGainLossCsvParser:
    """Parse only files under the gitignored owner-only reconciliation root."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def parse_realized_gain_loss(self, relative_path: str) -> BrokerRealizedStatement:
        source = self._secure_source(relative_path)
        payload = source.read_bytes()
        source_sha256 = hashlib.sha256(payload).hexdigest()
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise DataContractError(
                "Schwab statement CSV must be UTF-8 encoded",
                code="SCHWAB_STATEMENT_FORMAT_ERROR",
            ) from None
        rows = [list(row) for row in csv.reader(io.StringIO(text))]
        lots, warnings = self._parse_rows(rows, source_sha256=source_sha256)
        accounts = self._summaries(lots)
        return BrokerRealizedStatement(
            source_sha256=source_sha256,
            source_byte_count=len(payload),
            currency="USD",
            accounts=accounts,
            lots=lots,
            warning_codes=tuple(sorted(warnings)),
        )

    def _secure_source(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise DataContractError(
                "Schwab statement filename is required",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            )
        requested = Path(relative_path)
        if requested.is_absolute() or requested.suffix.lower() != ".csv":
            raise DataContractError(
                "Schwab statement must be a relative CSV filename",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            )
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)
        unresolved = self._root / requested
        if unresolved.is_symlink():
            raise DataContractError(
                "Schwab statement symlinks are forbidden",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            )
        try:
            resolved = unresolved.resolve(strict=True)
        except FileNotFoundError:
            raise DataContractError(
                "Schwab statement file was not found",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            ) from None
        if not resolved.is_relative_to(self._root) or not resolved.is_file():
            raise DataContractError(
                "Schwab statement path is outside the reconciliation root",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            )
        size = resolved.stat().st_size
        if size < 1 or size > _MAX_FILE_BYTES:
            raise DataContractError(
                "Schwab statement file size is invalid",
                code="SCHWAB_STATEMENT_FILE_SECURITY_ERROR",
            )
        os.chmod(resolved, 0o600)
        return resolved

    def _parse_rows(
        self, rows: list[list[str]], *, source_sha256: str
    ) -> tuple[tuple[BrokerRealizedLot, ...], set[str]]:
        lots: list[BrokerRealizedLot] = []
        warnings: set[str] = set()
        mapping: dict[str, int] | None = None
        pending_account_label = ""
        current_account_ref = ""
        section = 0
        fingerprints: set[tuple[object, ...]] = set()
        saw_header_candidate = False

        for row in rows:
            nonempty = [cell.strip() for cell in row if cell.strip()]
            if not nonempty:
                continue
            candidate = _column_map(row)
            if candidate is not None:
                saw_header_candidate = True
                mapping = candidate
                section += 1
                current_account_ref = _account_ref(
                    pending_account_label,
                    section=section,
                    source_sha256=source_sha256,
                )
                if not pending_account_label:
                    warnings.add("SCHWAB_STATEMENT_ACCOUNT_LABEL_UNAVAILABLE")
                pending_account_label = ""
                continue

            first = nonempty[0]
            first_lower = first.lower()
            if len(nonempty) == 1 and not any(marker in first_lower for marker in _TITLE_MARKERS):
                if "no transactions" in first_lower:
                    continue
                pending_account_label = first
                mapping = None
                continue
            if mapping is None:
                continue

            symbol = _cell(row, mapping, "symbol").strip().upper()
            if not symbol or symbol.lower() in _AGGREGATE_SYMBOLS or "subtotal" in symbol.lower():
                continue
            closed_text = _cell(row, mapping, "closed_date")
            if not closed_text.strip():
                raise DataContractError(
                    "Schwab statement lot is missing its closed date",
                    code="SCHWAB_STATEMENT_FORMAT_ERROR",
                    details={"field": "closed_date"},
                )
            opened_date = _optional_date(_cell(row, mapping, "opened_date"), field="opened_date")
            if opened_date is None:
                warnings.add("SCHWAB_STATEMENT_OPENED_DATE_UNAVAILABLE")
            cost_basis = _optional_decimal(_cell(row, mapping, "cost_basis"), field="cost_basis")
            realized_pnl = _optional_decimal(
                _cell(row, mapping, "realized_pnl"), field="realized_pnl"
            )
            if cost_basis is None:
                warnings.add("SCHWAB_STATEMENT_COST_BASIS_UNAVAILABLE")
            if realized_pnl is None:
                warnings.add("SCHWAB_STATEMENT_REALIZED_PNL_UNAVAILABLE")
            lot = BrokerRealizedLot(
                statement_account_ref=current_account_ref,
                symbol=symbol,
                opened_date=opened_date,
                closed_date=_required_date(closed_text, field="closed_date"),
                quantity=_required_decimal(_cell(row, mapping, "quantity"), field="quantity"),
                total_proceeds=_required_decimal(
                    _cell(row, mapping, "total_proceeds"), field="total_proceeds"
                ),
                cost_basis=cost_basis,
                realized_pnl=realized_pnl,
                long_term_pnl=_optional_decimal(
                    _cell(row, mapping, "long_term_pnl"), field="long_term_pnl"
                ),
                short_term_pnl=_optional_decimal(
                    _cell(row, mapping, "short_term_pnl"), field="short_term_pnl"
                ),
                term=_cell(row, mapping, "term") or None,
                cost_basis_method=_cell(row, mapping, "cost_basis_method") or None,
                wash_sale_disallowed=_optional_decimal(
                    _cell(row, mapping, "wash_sale_disallowed"),
                    field="wash_sale_disallowed",
                ),
            )
            fingerprint = (
                lot.statement_account_ref,
                lot.symbol,
                lot.opened_date,
                lot.closed_date,
                lot.quantity,
                lot.total_proceeds,
                lot.cost_basis,
                lot.realized_pnl,
            )
            if fingerprint in fingerprints:
                raise DataContractError(
                    "Schwab statement contains a duplicate closed lot",
                    code="SCHWAB_STATEMENT_FORMAT_ERROR",
                    details={"rule": "unique_lot"},
                )
            fingerprints.add(fingerprint)
            lots.append(lot)

        if not saw_header_candidate:
            raise DataContractError(
                "Schwab Realized Gain/Loss header was not recognized",
                code="SCHWAB_STATEMENT_FORMAT_ERROR",
                details={"required_columns": tuple(sorted(_REQUIRED))},
            )
        if not lots:
            raise DataContractError(
                "Schwab Realized Gain/Loss export contains no closed lots",
                code="SCHWAB_STATEMENT_NO_DATA",
            )
        lots.sort(
            key=lambda item: (
                item.statement_account_ref,
                item.closed_date,
                item.opened_date or date.min,
                item.symbol,
            )
        )
        return tuple(lots), warnings

    @staticmethod
    def _summaries(
        lots: tuple[BrokerRealizedLot, ...],
    ) -> tuple[BrokerRealizedAccountSummary, ...]:
        grouped: dict[str, list[BrokerRealizedLot]] = defaultdict(list)
        for lot in lots:
            grouped[lot.statement_account_ref].append(lot)
        return tuple(
            BrokerRealizedAccountSummary(
                statement_account_ref=account_ref,
                lot_count=len(values),
                first_closed_date=min(item.closed_date for item in values),
                last_closed_date=max(item.closed_date for item in values),
                total_proceeds=sum((item.total_proceeds for item in values), Decimal(0)),
                total_cost_basis=_complete_sum([item.cost_basis for item in values]),
                total_realized_pnl=_complete_sum([item.realized_pnl for item in values]),
                total_long_term_pnl=_complete_sum([item.long_term_pnl for item in values]),
                total_short_term_pnl=_complete_sum([item.short_term_pnl for item in values]),
                total_wash_sale_disallowed=_complete_sum(
                    [item.wash_sale_disallowed for item in values]
                ),
            )
            for account_ref, values in sorted(grouped.items())
        )
