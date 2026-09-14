"""User-requested SEC source snapshots and immutable valuation assumption Journal versions."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from threading import RLock
from typing import Any

from application.dto.us_research import FundamentalGetStatementsInput
from application.dto.valuation import ValuationCalculateInput, ValuationSaveInput
from application.ports.clock import Clock
from application.ports.research_unit_of_work import ResearchUnitOfWork
from application.services.journal_service import JournalService
from application.services.us_research_tool_coordinator import USResearchToolCoordinator
from domain.common.enums import JournalEntryType
from domain.common.errors import DataContractError, IdempotencyConflict
from domain.us_research.enums import USStatementFrequency
from domain.valuation.models import METHOD, calculate_eps_multiple

_HEADER = "# Valuation assumption snapshot\n\n```json\n"


class ValuationService:
    def __init__(
        self,
        research_uow_factory: Callable[[], ResearchUnitOfWork],
        statements: USResearchToolCoordinator,
        journal: JournalService,
        clock: Clock,
    ) -> None:
        self._uow = research_uow_factory
        self._statements = statements
        self._journal = journal
        self._clock = clock
        # Server-owned, expiring facts. A client can select but cannot edit them.
        self._sources: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _instrument(self, subject_id: str) -> str:
        with self._uow() as uow:
            subject = uow.subjects.get(subject_id)
            if subject is None or not subject.primary_instrument_id:
                raise DataContractError("A Research Subject with a US equity is required")
            instrument = subject.primary_instrument_id
        if not instrument.startswith("equity:US:"):
            raise DataContractError("Valuation supports US ordinary equities only")
        return instrument

    async def prepare(self, subject_id: str) -> dict[str, Any]:
        instrument = self._instrument(subject_id)
        now = self._clock.now()
        response = await self._statements.get_fundamental_statements(
            FundamentalGetStatementsInput(
                instrument_id=instrument, frequency=USStatementFrequency.ANNUAL, limit=4, as_of=now
            )
        )
        if not response.ok or response.data is None:
            raise DataContractError("Annual statements unavailable; no assumptions were filled")
        if response.data.instrument_id != instrument or not any(
            source.name == "sec_edgar" for source in response.sources
        ):
            raise DataContractError(
                "A SEC-filed annual source is required; fallback is unsupported"
            )
        eligible = [
            p
            for p in response.data.income
            if (
                p.filed_at is not None
                and p.filed_at <= now
                and p.period_start is not None
                and p.period_end <= p.filed_at.date()
                and 300 <= (p.period_end - p.period_start).days <= 400
                and p.accession
                and p.filing_form in {"10-K", "10-K/A"}
                and p.currency == "USD"
            )
        ]
        if not eligible:
            raise DataContractError(
                "Missing visible annual SEC filing, publication time or USD basis"
            )
        period = max(eligible, key=lambda p: (p.period_end, p.filed_at))
        eps = dict(period.line_items).get("eps_diluted")
        if eps is None or not Decimal(eps).is_finite() or Decimal(eps) <= 0:
            raise DataContractError("Latest eligible annual filing has no positive diluted EPS")
        balance = next(
            (
                p
                for p in response.data.balance_sheet
                if p.accession == period.accession and p.period_end == period.period_end
            ),
            None,
        )
        shares = dict(balance.line_items).get("shares_outstanding") if balance else None
        fact = {
            "instrument_id": instrument,
            "source": "sec_edgar",
            "eps_diluted": str(eps),
            "currency": "USD",
            "unit": "USD/share",
            "share_basis": "reported diluted EPS",
            "period_start": str(period.period_start),
            "period_end": str(period.period_end),
            "filed_at": period.filed_at.isoformat() if period.filed_at else None,
            "accession": period.accession,
            "filing_form": period.filing_form,
            "as_of": now.isoformat(),
            "shares_outstanding": str(shares) if shares else None,
            "shares_period_end": str(balance.period_end) if shares and balance else None,
            "request_id": response.request_id,
            "degraded": response.degraded,
            "warning_codes": [w.code for w in response.warnings],
        }
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sources = {k: v for k, v in self._sources.items() if v["expires_at"] > now}
            if len(self._sources) >= 64:
                del self._sources[next(iter(self._sources))]
            self._sources[token] = {
                "subject_id": subject_id,
                "source": fact,
                "expires_at": now + timedelta(hours=1),
            }
        return {
            "source_token": token,
            "source": fact,
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        }

    def calculate(self, subject_id: str, request: ValuationCalculateInput) -> dict[str, Any]:
        instrument = self._instrument(subject_id)
        with self._lock:
            cached = self._sources.get(request.source_token)
            if (
                cached is None
                or cached["subject_id"] != subject_id
                or cached["expires_at"] <= self._clock.now()
                or cached["source"]["instrument_id"] != instrument
            ):
                raise DataContractError(
                    "Source selection expired or changed; explicitly reload financials"
                )
            fact = dict(cached["source"])
        return {
            **calculate_eps_multiple(Decimal(fact["eps_diluted"]), request.assumptions.to_domain()),
            "source": fact,
            "assumptions": request.assumptions.model_dump(mode="json"),
            "warnings": [
                "USER_ASSUMPTIONS_NOT_CONFIRMED_JUDGMENT",
                "BUSINESS_MODEL_USER_ATTESTED",
                "AS_REPORTED_NOT_CURRENT_SPLIT_ADJUSTED",
                "NO_ENTERPRISE_OR_AGGREGATE_EQUITY_VALUE",
            ],
        }

    def history(self, subject_id: str) -> dict[str, Any]:
        self._instrument(subject_id)
        items = []
        offset = 0
        with self._uow() as uow:
            while True:
                entries = uow.journal.list(
                    subject_id=subject_id, as_of=None, limit=100, offset=offset
                )
                for entry in entries:
                    if "valuation_v1" not in entry.topic_tags or not entry.body_markdown.startswith(
                        _HEADER
                    ):
                        continue
                    try:
                        data = json.loads(entry.body_markdown[len(_HEADER) : -4])
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(data, dict) or data.get("method") != METHOD:
                        continue
                    items.append(
                        {
                            "version_id": entry.journal_id,
                            "created_at": entry.created_at.isoformat(),
                            "supersedes_version_id": entry.supersedes_journal_id,
                            "snapshot": data,
                        }
                    )
                if len(entries) < 100:
                    break
                offset += 100
        items.sort(key=lambda item: (item["created_at"], item["version_id"]), reverse=True)
        return {"items": items, "version_identity": "immutable journal_id; revisions may branch"}

    def save(self, subject_id: str, request: ValuationSaveInput) -> dict[str, Any]:
        if not request.authorization_note.strip() or not request.idempotency_key.strip():
            raise DataContractError("Explicit user authorization and idempotency are required")
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        key_hash = hashlib.sha256(request.idempotency_key.encode()).hexdigest()
        history = self.history(subject_id)["items"]
        for item in history:
            saved = item["snapshot"]
            if saved.get("idempotency_hash") == key_hash:
                if saved.get("request_digest") != digest:
                    raise IdempotencyConflict("Valuation save key was used with different inputs")
                return dict(item)
        if request.supersedes_version_id and not any(
            item["version_id"] == request.supersedes_version_id for item in history
        ):
            raise DataContractError("Previous valuation version does not belong to this Subject")
        result = self.calculate(subject_id, request)
        result.update(
            {
                "idempotency_hash": key_hash,
                "request_digest": digest,
                "supersedes_version_id": request.supersedes_version_id,
                "authorization_note": request.authorization_note,
                "authored_by": "user",
            }
        )
        archived = self._journal.append(
            subject_id=subject_id,
            entry_type=JournalEntryType.NOTE,
            title="Valuation assumption snapshot",
            body_markdown=_HEADER
            + json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n```",
            authored_by="user",
            confirmed_by="user",
            instrument_ids=(result["source"]["instrument_id"],),
            topic_tags=("valuation_v1",),
            related_entity_type=None,
            related_entity_id=None,
            supersedes_journal_id=request.supersedes_version_id,
            idempotency_key="valuation:" + subject_id + ":" + key_hash,
        )
        if not archived.ok or archived.data is None:
            raise DataContractError("Valuation version could not be saved")
        return {
            "version_id": archived.data.journal_id,
            "created_at": archived.data.created_at.isoformat(),
            "supersedes_version_id": request.supersedes_version_id,
            "snapshot": result,
        }
