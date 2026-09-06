"""Deterministic transaction-versus-plan Trade Retro orchestration."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime

from application.dto.error_mapper import to_error_info, to_error_info_from_exception
from application.dto.tool_envelope import ToolEnvelope, WarningInfo
from application.dto.trade_retro import (
    TradeRetroExportReceiptDTO,
    TradeRetroHistoryDTO,
    TradeRetroHistoryInput,
    TradeRetroPlanSnapshotDTO,
    TradeRetroReviewInput,
    TradeRetroReviewRevisionDTO,
    TradeRetroRunDTO,
)
from application.ports.account_transaction_repository import AccountTransactionRepository
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.secret_redactor import SecretRedactor
from application.ports.trade_retro_exporter import TradeRetroExporter
from application.ports.trade_retro_narrative_provider import (
    TradeRetroNarrativeProvider,
    TradeRetroNarrativeRequest,
)
from application.ports.trade_retro_repository import TradeRetroRepository
from application.services._research_support import UowFactory
from domain.common.enums import DecisionType, Freshness, ResearchSubjectStatus
from domain.common.errors import (
    IdempotencyConflict,
    TradeRetroReviewVersionConflict,
    TradeRetroRunNotFound,
    TradingPartnerError,
)
from domain.common.ids import EntityIdPrefix
from domain.portfolio.enums import (
    AccountActivityCoverageStatus,
    AccountTransactionKind,
    AccountTransactionSide,
)
from domain.portfolio.models import AccountTransaction
from domain.retro.enums import (
    TradeRetroFindingReviewStatus,
    TradeRetroReviewStatus,
    TradeRetroSeverity,
    TradeRetroStatus,
)
from domain.retro.models import (
    TRADE_RETRO_ALGORITHM_VERSION,
    TRADE_RETRO_LEGACY_MARKDOWN_IMPORT_VERSION,
    TradeRetroExportReceipt,
    TradeRetroFinding,
    TradeRetroFindingReview,
    TradeRetroPlanEntry,
    TradeRetroPlanSnapshot,
    TradeRetroReviewRevision,
    TradeRetroRun,
    trade_retro_finding_key,
)
from domain.trade_plan.enums import TradePlanStatus

_BUY_DECISIONS = {DecisionType.INITIATE_INTENT.value, DecisionType.ADD_INTENT.value}
_SELL_DECISIONS = {DecisionType.REDUCE_INTENT.value, DecisionType.EXIT_INTENT.value}


def _llm_failure_warning_code(exc: BaseException) -> str:
    if isinstance(exc, TradingPartnerError):
        return f"TRADE_RETRO_LLM_{exc.code}"
    return "TRADE_RETRO_LLM_UNEXPECTED_ERROR"


class TradeRetroService:
    def __init__(
        self,
        repository: TradeRetroRepository,
        transactions: AccountTransactionRepository,
        research_uow_factory: UowFactory,
        exporter: TradeRetroExporter,
        clock: Clock,
        id_generator: IdGenerator,
        secret_redactor: SecretRedactor,
        narrative_provider: TradeRetroNarrativeProvider | None = None,
    ) -> None:
        self._repository = repository
        self._transactions = transactions
        self._research_uow_factory = research_uow_factory
        self._exporter = exporter
        self._clock = clock
        self._ids = id_generator
        self._redactor = secret_redactor
        self._narrative = narrative_provider

    def prepare(
        self, *, start: datetime, end: datetime, idempotency_key: str
    ) -> ToolEnvelope[TradeRetroPlanSnapshotDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            existing = self._repository.get_plan_snapshot_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.period_start != start or existing.period_end != end:
                    raise IdempotencyConflict(
                        "Trade Retro plan snapshot idempotency key was reused"
                    )
                return self._success(
                    request_id,
                    existing.captured_at,
                    TradeRetroPlanSnapshotDTO.from_domain(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            entries: list[TradeRetroPlanEntry] = []
            captured_at = self._clock.now()
            with self._research_uow_factory() as uow:
                offset = 0
                while True:
                    subjects = uow.subjects.list(
                        status=ResearchSubjectStatus.ACTIVE,
                        include_archived=False,
                        limit=200,
                        offset=offset,
                    )
                    for subject in subjects:
                        plan = uow.trade_plans.get_current_by_subject(subject.subject_id)
                        if plan is None:
                            continue
                        decisions = uow.decisions.list_by_subject(
                            subject.subject_id,
                            as_of=captured_at,
                        )
                        entries.append(
                            TradeRetroPlanEntry(
                                subject_id=subject.subject_id,
                                subject_title=subject.title,
                                plan_id=plan.plan_id,
                                plan_version=plan.version,
                                thesis_id=plan.thesis_id,
                                instrument_id=plan.instrument_id,
                                status=plan.status.value,
                                stop_price=(
                                    str(plan.stop_price) if plan.stop_price is not None else None
                                ),
                                max_position_percent=str(plan.max_position_percent),
                                condition_codes=tuple(
                                    item.condition_code for item in plan.conditions
                                ),
                                decision_records=tuple(
                                    (
                                        item.decision_id,
                                        item.decision_type.value,
                                        item.decided_at.isoformat(),
                                        item.primary_instrument_id,
                                    )
                                    for item in decisions
                                ),
                            )
                        )
                    if len(subjects) < 200:
                        break
                    offset += len(subjects)
            value = TradeRetroPlanSnapshot(
                snapshot_id=self._ids.new(EntityIdPrefix.RETRO_PLAN),
                period_start=start,
                period_end=end,
                captured_at=captured_at,
                entries=tuple(entries),
                idempotency_key=idempotency_key,
            )
            self._repository.append_plan_snapshot(value)
            warnings = ("PLAN_SNAPSHOT_CAPTURED_AFTER_PERIOD_START",) if captured_at > start else ()
            return self._success(
                request_id,
                captured_at,
                TradeRetroPlanSnapshotDTO.from_domain(value),
                warnings,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, start, exc)

    async def run(
        self,
        *,
        start: datetime,
        end: datetime,
        idempotency_key: str,
        use_llm: bool,
    ) -> ToolEnvelope[TradeRetroRunDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            existing = self._repository.get_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                if not self._same_run_request(existing, start, end, use_llm):
                    raise IdempotencyConflict("Trade Retro run idempotency key was reused")
                return self._success(
                    request_id,
                    existing.generated_at,
                    TradeRetroRunDTO.from_domain(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            transactions = self._transactions.list(
                providers=(),
                start=start,
                end=end,
                limit=None,
            )
            trades = tuple(
                item for item in transactions if item.kind is AccountTransactionKind.TRADE
            )
            coverage, coverage_codes = self._coverage(trades, start=start, end=end)
            snapshot = self._repository.latest_plan_snapshot_for_period(
                period_start=start,
                period_end=end,
            )
            findings = self._findings(trades, snapshot, coverage_complete=coverage)
            warning_codes = list(coverage_codes)
            if snapshot is None:
                warning_codes.append("NO_PRETRADE_PLAN_SNAPSHOT")
            generated_at = self._clock.now()
            summary = self._deterministic_summary(
                start=start,
                end=end,
                trades=trades,
                findings=findings,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
            )
            llm_provider: str | None = None
            llm_model: str | None = None
            if use_llm and self._narrative is not None:
                try:
                    response = await self._narrative.narrate(
                        TradeRetroNarrativeRequest(
                            session_id=f"trade-retro:{idempotency_key}",
                            deterministic_facts_json=json.dumps(
                                {
                                    "period_start": start.isoformat(),
                                    "period_end": end.isoformat(),
                                    "transactions": [
                                        {
                                            "id": item.provider_transaction_id,
                                            "instrument_id": item.instrument_id,
                                            "side": item.side.value if item.side else None,
                                            "quantity": (
                                                str(item.quantity)
                                                if item.quantity is not None
                                                else None
                                            ),
                                            "price": (
                                                str(item.price) if item.price is not None else None
                                            ),
                                            "fees": (
                                                str(item.fees) if item.fees is not None else None
                                            ),
                                            "currency": item.currency,
                                            "occurred_at": item.occurred_at.isoformat(),
                                        }
                                        for item in trades
                                    ],
                                    "findings": [
                                        {
                                            **asdict(item),
                                            "severity": item.severity.value,
                                        }
                                        for item in findings
                                    ],
                                    "warning_codes": warning_codes,
                                },
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        )
                    )
                    summary = response.summary_markdown
                    llm_provider = response.provider_name
                    llm_model = response.model
                except Exception as exc:  # noqa: BLE001 - deterministic result remains valid
                    warning_codes.extend(
                        (
                            "TRADE_RETRO_LLM_UNAVAILABLE",
                            _llm_failure_warning_code(exc),
                        )
                    )
            elif use_llm:
                warning_codes.append("TRADE_RETRO_LLM_NOT_CONFIGURED")
            value = TradeRetroRun(
                run_id=self._ids.new(EntityIdPrefix.RETRO),
                period_start=start,
                period_end=end,
                generated_at=generated_at,
                status=(TradeRetroStatus.COMPLETE if coverage else TradeRetroStatus.INCOMPLETE),
                plan_snapshot_id=snapshot.snapshot_id if snapshot is not None else None,
                transaction_ids=tuple(item.provider_transaction_id for item in trades),
                findings=findings,
                warning_codes=tuple(dict.fromkeys(warning_codes)),
                summary_markdown=summary,
                llm_provider=llm_provider,
                llm_model=llm_model,
                idempotency_key=idempotency_key,
            )
            self._repository.append_run(value)
            return self._success(
                request_id,
                generated_at,
                TradeRetroRunDTO.from_domain(value),
                value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, end, exc)

    def import_legacy_markdown(
        self,
        *,
        start: datetime,
        end: datetime,
        generated_at: datetime,
        summary_markdown: str,
        idempotency_key: str,
    ) -> ToolEnvelope[TradeRetroRunDTO]:
        """Import one historical Markdown retro without inventing structured facts."""

        request_id = self._ids.new(EntityIdPrefix.REQ)
        try:
            existing = self._repository.get_run_by_idempotency_key(idempotency_key)
            if existing is not None:
                if not self._same_legacy_import_request(
                    existing,
                    start=start,
                    end=end,
                    generated_at=generated_at,
                    summary_markdown=summary_markdown,
                ):
                    raise IdempotencyConflict("Trade Retro import idempotency key was reused")
                return self._success(
                    request_id,
                    existing.generated_at,
                    self._run_dto(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            value = TradeRetroRun(
                run_id=self._ids.new(EntityIdPrefix.RETRO),
                period_start=start,
                period_end=end,
                generated_at=generated_at,
                status=TradeRetroStatus.INCOMPLETE,
                plan_snapshot_id=None,
                transaction_ids=(),
                findings=(),
                warning_codes=(
                    "IMPORTED_LEGACY_MARKDOWN_RETRO",
                    "LEGACY_RETRO_FINDINGS_NOT_STRUCTURED",
                    "TRANSACTION_COVERAGE_NOT_REVALIDATED",
                ),
                summary_markdown=summary_markdown,
                llm_provider=None,
                llm_model=None,
                idempotency_key=idempotency_key,
                algorithm_version=TRADE_RETRO_LEGACY_MARKDOWN_IMPORT_VERSION,
            )
            self._repository.append_run(value)
            return self._success(
                request_id,
                generated_at,
                TradeRetroRunDTO.from_domain(value),
                value.warning_codes,
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, generated_at, exc)

    def history(self, request: TradeRetroHistoryInput) -> ToolEnvelope[TradeRetroHistoryDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            if request.run_id is None:
                runs = self._repository.list_runs(request.limit)
            else:
                run = self._repository.get_run(request.run_id)
                runs = () if run is None else (run,)
            snapshots = self._repository.get_plan_snapshots(
                tuple(
                    dict.fromkeys(
                        item.plan_snapshot_id for item in runs if item.plan_snapshot_id is not None
                    )
                )
            )
            reviews = self._repository.list_reviews_for_runs(tuple(item.run_id for item in runs))
            return self._success(
                request_id,
                as_of,
                TradeRetroHistoryDTO(
                    runs=tuple(
                        self._run_dto(
                            item,
                            snapshot=(
                                snapshots.get(item.plan_snapshot_id)
                                if item.plan_snapshot_id is not None
                                else None
                            ),
                            snapshot_loaded=True,
                            reviews=reviews.get(item.run_id, ()),
                        )
                        for item in runs
                    )
                ),
                (),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def review(self, request: TradeRetroReviewInput) -> ToolEnvelope[TradeRetroReviewRevisionDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            existing = self._repository.get_review_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                if not self._same_review_request(existing, request):
                    raise IdempotencyConflict("Trade Retro review idempotency key was reused")
                return self._success(
                    request_id,
                    existing.created_at,
                    TradeRetroReviewRevisionDTO.from_domain(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            run = self._repository.get_run(request.run_id)
            if run is None:
                raise TradeRetroRunNotFound(
                    "Trade Retro run was not found",
                    details={"run_id": request.run_id},
                )
            latest = self._repository.latest_review(request.run_id)
            current_version = latest.version if latest is not None else 0
            if request.expected_version != current_version:
                raise TradeRetroReviewVersionConflict(
                    "expected_version does not match the latest Trade Retro review",
                    details={
                        "run_id": request.run_id,
                        "expected_version": request.expected_version,
                        "current_version": current_version,
                    },
                )
            available_keys = {trade_retro_finding_key(item) for item in run.findings}
            unknown_keys = sorted(
                item.finding_key
                for item in request.finding_reviews
                if item.finding_key not in available_keys
            )
            if unknown_keys:
                raise TradeRetroRunNotFound(
                    "Trade Retro review references a finding outside this run",
                    details={"run_id": request.run_id, "finding_keys": unknown_keys},
                    code="TRADE_RETRO_FINDING_NOT_FOUND",
                )
            value = TradeRetroReviewRevision(
                review_id=self._ids.new(EntityIdPrefix.RETRO_REVIEW),
                run_id=request.run_id,
                version=current_version + 1,
                status=TradeRetroReviewStatus(request.status),
                note_markdown=request.note_markdown,
                action_items=request.action_items,
                finding_reviews=tuple(
                    TradeRetroFindingReview(
                        finding_key=item.finding_key,
                        status=TradeRetroFindingReviewStatus(item.status),
                        note=item.note,
                    )
                    for item in request.finding_reviews
                ),
                reviewed_by=request.confirmed_by,
                authorization_note=request.authorization_note,
                created_at=as_of,
                idempotency_key=request.idempotency_key,
            )
            self._repository.append_review(value)
            return self._success(
                request_id,
                value.created_at,
                TradeRetroReviewRevisionDTO.from_domain(value),
                (),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def export(
        self, *, run_id: str, idempotency_key: str
    ) -> ToolEnvelope[TradeRetroExportReceiptDTO]:
        request_id = self._ids.new(EntityIdPrefix.REQ)
        as_of = self._clock.now()
        try:
            existing = self._repository.get_export_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.run_id != run_id:
                    raise IdempotencyConflict("Trade Retro export idempotency key was reused")
                return self._success(
                    request_id,
                    existing.exported_at,
                    TradeRetroExportReceiptDTO.from_domain(existing),
                    ("DUPLICATE_IDEMPOTENCY_KEY",),
                )
            run = self._repository.get_run(run_id)
            if run is None:
                raise TradeRetroRunNotFound(
                    "Trade Retro run was not found",
                    details={"run_id": run_id},
                )
            review = self._repository.latest_review(run_id)
            path, digest = self._exporter.export(run, review)
            receipt = TradeRetroExportReceipt(
                receipt_id=self._ids.new(EntityIdPrefix.RETRO_EXPORT),
                run_id=run.run_id,
                target_path=str(path),
                content_sha256=digest,
                exported_at=as_of,
                idempotency_key=idempotency_key,
                review_version=review.version if review is not None else None,
            )
            self._repository.append_export(receipt)
            return self._success(
                request_id,
                as_of,
                TradeRetroExportReceiptDTO.from_domain(receipt),
                (),
            )
        except Exception as exc:  # noqa: BLE001
            return self._failure(request_id, as_of, exc)

    def _coverage(
        self,
        trades: tuple[AccountTransaction, ...],
        *,
        start: datetime,
        end: datetime,
    ) -> tuple[bool, tuple[str, ...]]:
        receipts = self._transactions.list_coverage(
            providers=tuple(dict.fromkeys(item.provider for item in trades)),
            account_refs=tuple(dict.fromkeys(item.account_ref for item in trades)),
            limit=500,
        )
        required = {(item.provider, item.account_ref) for item in trades}
        complete = {
            (item.provider, item.account_ref)
            for item in receipts
            if item.status is AccountActivityCoverageStatus.COMPLETE
            and item.effective_start <= start
            and item.effective_end >= end
        }
        if not required:
            return False, ("TRADE_ACTIVITY_COVERAGE_UNPROVEN",)
        missing = required - complete
        if missing:
            return False, ("TRADE_ACTIVITY_COVERAGE_INCOMPLETE",)
        return True, ()

    @staticmethod
    def _same_run_request(
        existing: TradeRetroRun,
        start: datetime,
        end: datetime,
        use_llm: bool,
    ) -> bool:
        requested_llm = existing.llm_provider is not None or any(
            code in {"TRADE_RETRO_LLM_UNAVAILABLE", "TRADE_RETRO_LLM_NOT_CONFIGURED"}
            for code in existing.warning_codes
        )
        return (
            existing.algorithm_version == TRADE_RETRO_ALGORITHM_VERSION
            and existing.period_start == start
            and existing.period_end == end
            and requested_llm is use_llm
        )

    @staticmethod
    def _same_legacy_import_request(
        existing: TradeRetroRun,
        *,
        start: datetime,
        end: datetime,
        generated_at: datetime,
        summary_markdown: str,
    ) -> bool:
        return (
            existing.algorithm_version == TRADE_RETRO_LEGACY_MARKDOWN_IMPORT_VERSION
            and existing.period_start == start
            and existing.period_end == end
            and existing.generated_at == generated_at
            and existing.summary_markdown == summary_markdown
        )

    def _findings(
        self,
        trades: tuple[AccountTransaction, ...],
        snapshot: TradeRetroPlanSnapshot | None,
        *,
        coverage_complete: bool,
    ) -> tuple[TradeRetroFinding, ...]:
        findings: list[TradeRetroFinding] = []
        by_instrument: dict[str, list[AccountTransaction]] = defaultdict(list)
        for item in trades:
            assert item.instrument_id is not None
            by_instrument[item.instrument_id].append(item)
        plans: dict[str, list[TradeRetroPlanEntry]] = defaultdict(list)
        if snapshot is not None:
            for plan_entry in snapshot.entries:
                plans[plan_entry.instrument_id].append(plan_entry)
        if not coverage_complete:
            findings.append(
                TradeRetroFinding(
                    code="COVERAGE_INCOMPLETE",
                    severity=TradeRetroSeverity.HIGH,
                    title="成交覆盖尚未证明完整",
                    detail="本轮只能审计已持久化成交，不能把未发现的交易解释为没有交易。",
                    instrument_id=None,
                    transaction_ids=tuple(item.provider_transaction_id for item in trades),
                )
            )
        for instrument_id, items in sorted(by_instrument.items()):
            ordered = sorted(
                items,
                key=lambda item: (item.occurred_at, item.provider_transaction_id),
            )
            transaction_ids = tuple(item.provider_transaction_id for item in ordered)
            matched_plans = plans.get(instrument_id, [])
            plan = matched_plans[0] if len(matched_plans) == 1 else None
            if not matched_plans:
                findings.append(
                    TradeRetroFinding(
                        code="NO_PRETRADE_PLAN",
                        severity=TradeRetroSeverity.HIGH,
                        title="成交前没有可证明的 Trade Plan 快照",
                        detail="事后创建或修改的计划不能用来证明这笔交易遵守了事前纪律。",
                        instrument_id=instrument_id,
                        transaction_ids=transaction_ids,
                    )
                )
            elif len(matched_plans) > 1:
                findings.append(
                    TradeRetroFinding(
                        code="AMBIGUOUS_PRETRADE_PLAN",
                        severity=TradeRetroSeverity.HIGH,
                        title="同一成交标的存在多个事前 Trade Plan",
                        detail=(
                            "系统不会任意选择其中一个计划证明交易纪律；请人工确认"
                            "成交对应的研究标的和计划。"
                        ),
                        instrument_id=instrument_id,
                        transaction_ids=transaction_ids,
                    )
                )
            else:
                assert plan is not None
                if plan.status != TradePlanStatus.ACTIVE.value:
                    findings.append(
                        TradeRetroFinding(
                            code="PLAN_NOT_ACTIVE",
                            severity=TradeRetroSeverity.HIGH,
                            title="成交时对应 Trade Plan 并非 ACTIVE",
                            detail=(
                                f"事前快照中的计划状态为 {plan.status}；该状态不能证明"
                                "本周期成交经过有效交易计划授权。"
                            ),
                            instrument_id=instrument_id,
                            transaction_ids=transaction_ids,
                            plan_id=plan.plan_id,
                        )
                    )
                if plan.stop_price is None and not any(
                    "INVALID" in code.upper() or "STOP" in code.upper()
                    for code in plan.condition_codes
                ):
                    findings.append(
                        TradeRetroFinding(
                            code="MISSING_INVALIDATION",
                            severity=TradeRetroSeverity.MEDIUM,
                            title="事前计划缺少失效条件",
                            detail="Trade Plan 没有 stop_price，也没有可识别的失效/止损条件。",
                            instrument_id=instrument_id,
                            transaction_ids=transaction_ids,
                            plan_id=plan.plan_id,
                        )
                    )
                mismatches = tuple(
                    item.provider_transaction_id
                    for item in ordered
                    if not self._has_compatible_decision(item, plan)
                )
                if mismatches:
                    findings.append(
                        TradeRetroFinding(
                            code="ACTION_RECORD_MISMATCH",
                            severity=TradeRetroSeverity.HIGH,
                            title="成交缺少匹配的事前 Decision Record",
                            detail="对应方向的严格交易意图未在成交时间前被确认记录。",
                            instrument_id=instrument_id,
                            transaction_ids=mismatches,
                            plan_id=plan.plan_id,
                        )
                    )
            sides = {item.side for item in ordered}
            if {
                AccountTransactionSide.BUY,
                AccountTransactionSide.SELL,
            }.issubset(sides):
                findings.append(
                    TradeRetroFinding(
                        code="ROUND_TRIP",
                        severity=TradeRetroSeverity.INFO,
                        title="周期内发生双向交易",
                        detail="同一标的在复盘周期内同时出现买入和卖出，需要核对是否为计划内调仓。",
                        instrument_id=instrument_id,
                        transaction_ids=transaction_ids,
                        plan_id=plan.plan_id if plan else None,
                    )
                )
            reentries = tuple(
                current.provider_transaction_id
                for index, current in enumerate(ordered)
                if current.side is AccountTransactionSide.BUY
                and any(
                    previous.side is AccountTransactionSide.SELL
                    and previous.occurred_at.date() == current.occurred_at.date()
                    for previous in ordered[:index]
                )
            )
            if reentries:
                findings.append(
                    TradeRetroFinding(
                        code="SAME_DAY_REENTRY",
                        severity=TradeRetroSeverity.MEDIUM,
                        title="卖出后同日重新买入",
                        detail="同日反向再入场可能是计划执行，也可能是情绪性反复，需要人工复核。",
                        instrument_id=instrument_id,
                        transaction_ids=reentries,
                        plan_id=plan.plan_id if plan else None,
                    )
                )
        return tuple(findings)

    def _run_dto(
        self,
        run: TradeRetroRun,
        *,
        snapshot: TradeRetroPlanSnapshot | None = None,
        snapshot_loaded: bool = False,
        reviews: tuple[TradeRetroReviewRevision, ...] | None = None,
    ) -> TradeRetroRunDTO:
        if not snapshot_loaded and run.plan_snapshot_id is not None:
            snapshot = self._repository.get_plan_snapshot(run.plan_snapshot_id)
        subject_ids = (
            tuple(dict.fromkeys(item.subject_id for item in snapshot.entries))
            if snapshot is not None
            else ()
        )
        return TradeRetroRunDTO.from_domain(
            run,
            reviews=(reviews if reviews is not None else self._repository.list_reviews(run.run_id)),
            subject_ids=subject_ids,
        )

    @staticmethod
    def _same_review_request(
        existing: TradeRetroReviewRevision,
        request: TradeRetroReviewInput,
    ) -> bool:
        return (
            existing.run_id == request.run_id
            and existing.version == request.expected_version + 1
            and existing.status.value == request.status
            and existing.note_markdown == request.note_markdown.strip()
            and existing.action_items == request.action_items
            and tuple(
                (item.finding_key, item.status.value, item.note)
                for item in existing.finding_reviews
            )
            == tuple(
                (
                    item.finding_key,
                    item.status,
                    item.note.strip() if item.note and item.note.strip() else None,
                )
                for item in request.finding_reviews
            )
            and existing.reviewed_by == request.confirmed_by
            and existing.authorization_note == request.authorization_note.strip()
        )

    @staticmethod
    def _has_compatible_decision(
        transaction: AccountTransaction,
        plan: TradeRetroPlanEntry,
    ) -> bool:
        expected = (
            _BUY_DECISIONS if transaction.side is AccountTransactionSide.BUY else _SELL_DECISIONS
        )
        return any(
            decision_type in expected
            and datetime.fromisoformat(decided_at) <= transaction.occurred_at
            and (
                primary_instrument_id is None or primary_instrument_id == transaction.instrument_id
            )
            for _decision_id, decision_type, decided_at, primary_instrument_id in (
                plan.decision_records
            )
        )

    @staticmethod
    def _deterministic_summary(
        *,
        start: datetime,
        end: datetime,
        trades: tuple[AccountTransaction, ...],
        findings: tuple[TradeRetroFinding, ...],
        warning_codes: tuple[str, ...],
    ) -> str:
        instruments = sorted({item.instrument_id for item in trades if item.instrument_id})
        lines = [
            f"### 交易复盘 · {start.date()}—{end.date()}",
            "",
            f"- 已持久化成交：**{len(trades)}** 笔；标的：**{len(instruments)}** 个。",
            f"- 纪律发现：**{len(findings)}** 条；执行影响：**无**。",
        ]
        if warning_codes:
            lines.append(f"- 数据边界：`{'`、`'.join(warning_codes)}`。")
        if findings:
            lines.extend(["", "#### 纪律发现"])
            lines.extend(
                f"- **[{item.severity.value}] {item.code}** · {item.title} — {item.detail}"
                for item in findings
            )
        else:
            lines.extend(["", "本周期没有发现可编程识别的纪律偏差。"])
        lines.extend(
            [
                "",
                "> 本报告只审计已持久化事实与事前快照；不推断成交、不修改"
                "标的/Thesis/Trade Plan，也不执行订单。",
            ]
        )
        return "\n".join(lines)

    def _success[T](
        self,
        request_id: str,
        as_of: datetime,
        data: T,
        warning_codes: tuple[str, ...],
    ) -> ToolEnvelope[T]:
        return ToolEnvelope.success(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            data=data,
            degraded=bool(warning_codes),
            warnings=tuple(
                WarningInfo(code=code, message="Trade Retro data boundary.", details={})
                for code in warning_codes
            ),
        )

    def _failure[T](self, request_id: str, as_of: datetime, exc: BaseException) -> ToolEnvelope[T]:
        error = (
            to_error_info(exc, self._redactor)
            if isinstance(exc, TradingPartnerError)
            else to_error_info_from_exception(exc, self._redactor)
        )
        return ToolEnvelope.failure(
            request_id=request_id,
            market=None,
            as_of=as_of,
            fetched_at=self._clock.now(),
            freshness=Freshness.UNKNOWN,
            sources=(),
            errors=(error,),
            degraded=True,
            data=None,
        )
