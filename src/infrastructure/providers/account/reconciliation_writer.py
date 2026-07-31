"""Owner-only JSON writer for redacted broker reconciliation drafts."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

from domain.attribution.reconciliation_models import BrokerRealizedReconciliation
from domain.common.errors import PersistenceError


def _json_default(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported reconciliation value: {type(value).__name__}")


class OwnerOnlyBrokerReconciliationWriter:
    """Create immutable permission-restricted drafts outside Git history."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def write_draft(self, value: BrokerRealizedReconciliation) -> str:
        receipt_root = self._root / "receipts"
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._root, 0o700)
        os.chmod(receipt_root, 0o700)
        account_digest = hashlib.sha256(value.durable_account_ref.encode()).hexdigest()[:12]
        timestamp = value.generated_at.strftime("%Y%m%dT%H%M%S%fZ")
        filename = (
            f"schwab-realized-{value.period_start:%Y%m%d}-{value.period_end:%Y%m%d}-"
            f"{account_digest}-{timestamp}.json"
        )
        target = receipt_root / filename
        payload = json.dumps(
            dataclasses.asdict(value),
            default=_json_default,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(target, 0o600)
        except OSError:
            raise PersistenceError(
                "failed to persist broker reconciliation draft",
                code="SCHWAB_RECONCILIATION_DRAFT_WRITE_ERROR",
                retryable=False,
            ) from None
        return f"receipts/{filename}"
