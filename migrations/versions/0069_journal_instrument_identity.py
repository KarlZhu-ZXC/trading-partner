"""Repair Journal option/GDXU identities and register traded Instruments."""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_journal_instrument_identity"
down_revision: str | None = "0068_moomoo_instrument_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPTION_SYMBOL = re.compile(r"^[A-Z0-9.]{1,8}\d{6}[CP]\d+$")


def _canonical_option_id(instrument_id: str) -> str | None:
    parts = instrument_id.split(":", 2)
    if len(parts) != 3 or parts[1] != "US":
        return None
    symbol = parts[2].replace(" ", "").upper()
    return f"option:US:{symbol}" if _OPTION_SYMBOL.fullmatch(symbol) else None


def _instrument_exists(connection: sa.Connection, instrument_id: str) -> bool:
    return bool(connection.scalar(sa.text(
        "SELECT 1 FROM instruments WHERE instrument_id=:instrument_id LIMIT 1"
    ), {"instrument_id": instrument_id}))


def _register_observed_instrument(connection: sa.Connection, instrument_id: str) -> None:
    if _instrument_exists(connection, instrument_id):
        return
    parts = instrument_id.split(":", 2)
    if len(parts) != 3 or parts[0] not in {"equity", "etf", "option"} or parts[1] != "US":
        return
    asset_type, market, symbol = parts
    connection.execute(sa.text(
        "INSERT INTO instruments("
        "instrument_id,symbol,name,market,exchange,currency,timezone,asset_type,"
        "is_active,listing_status,metadata_version,created_at,updated_at) VALUES ("
        ":instrument_id,:symbol,:name,:market,'UNKNOWN','USD','America/New_York',"
        ":asset_type,1,'active',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    ), {
        "instrument_id": instrument_id,
        "symbol": symbol,
        "name": f"{symbol} (account-observed)",
        "market": market,
        "asset_type": asset_type,
    })


def _replace_account_identity(connection: sa.Connection, old: str, new: str) -> None:
    _register_observed_instrument(connection, new)
    connection.execute(sa.text(
        "UPDATE account_transactions SET instrument_id=:new, mapping_version=(CASE "
        "WHEN provider='moomoo' THEN 'moomoo_deals_v2' ELSE mapping_version END) "
        "WHERE instrument_id=:old"
    ), {"old": old, "new": new})
    connection.execute(sa.text(
        "DELETE FROM account_positions AS old_position WHERE instrument_id=:old AND EXISTS ("
        "SELECT 1 FROM account_positions AS canonical "
        "WHERE canonical.snapshot_id=old_position.snapshot_id "
        "AND canonical.instrument_id=:new)"
    ), {"old": old, "new": new})
    connection.execute(sa.text(
        "UPDATE account_positions SET instrument_id=:new WHERE instrument_id=:old"
    ), {"old": old, "new": new})
    connection.execute(sa.text(
        "UPDATE account_snapshots "
        "SET open_orders_json=replace(open_orders_json,:old_json,:new_json) "
        "WHERE open_orders_json LIKE :pattern"
    ), {
        "old_json": f'"instrument_id":"{old}"',
        "new_json": f'"instrument_id":"{new}"',
        "pattern": f'%"instrument_id":"{old}"%',
    })


def upgrade() -> None:
    connection = op.get_bind()
    if _instrument_exists(connection, "etf:US:GDXU"):
        _replace_account_identity(connection, "equity:US:GDXU", "etf:US:GDXU")

    observed_ids = tuple(connection.scalars(sa.text(
        "SELECT DISTINCT instrument_id FROM account_transactions"
    )))
    for instrument_id in observed_ids:
        canonical = _canonical_option_id(str(instrument_id))
        if canonical is not None and canonical != instrument_id:
            _replace_account_identity(connection, str(instrument_id), canonical)

    remaining_ids = tuple(connection.scalars(sa.text(
        "SELECT DISTINCT instrument_id FROM account_transactions"
    )))
    for instrument_id in remaining_ids:
        _register_observed_instrument(connection, str(instrument_id))

    connection.execute(sa.text(
        "DELETE FROM provider_cache WHERE instrument_id='equity:US:GDXU'"
    ))
    connection.execute(sa.text(
        "DELETE FROM instruments WHERE instrument_id='equity:US:GDXU' "
        "AND EXISTS (SELECT 1 FROM instruments WHERE instrument_id='etf:US:GDXU')"
    ))


def downgrade() -> None:
    # Identity repair is intentionally irreversible.  Restoring corrupt option
    # strings or the duplicate GDXU row would make durable account facts unsafe.
    pass
