from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine

from application.dto.workflow import (
    WorkflowRunDTO,
    WorkflowSynthesisContractDTO,
)
from domain.common.enums import VendorId
from domain.common.errors import DataContractError
from domain.portfolio.enums import AccountTransactionKind, AccountTransactionSide
from domain.portfolio.models import AccountTransaction
from domain.workflow.enums import WorkflowRunStatus, WorkflowType
from domain.workflow.models import WorkflowRun, WorkflowStepReceipt
from infrastructure.persistence.account_transaction_repository import (
    SqlAlchemyAccountTransactionRepository,
)
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.workflow_run_repository import (
    SqlAlchemyWorkflowRunRepository,
)

NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _step(*, ok: bool = True, degraded: bool = False) -> WorkflowStepReceipt:
    return WorkflowStepReceipt(
        ordinal=1,
        step_name="market_context",
        tool_name="market_get_context",
        required=True,
        ok=ok,
        degraded=degraded,
        request_id="req_1",
        as_of=NOW,
        source_names=("yfinance",) if ok else (),
        warning_codes=("DELAYED",) if degraded else (),
        error_codes=() if ok else ("PROVIDER_UNAVAILABLE",),
    )


def _run(status: WorkflowRunStatus = WorkflowRunStatus.COMPLETE) -> WorkflowRun:
    return WorkflowRun(
        run_id="run_1",
        workflow_type=WorkflowType.US_MARKET_REVIEW,
        case_id=None,
        instrument_id=None,
        requested_as_of=NOW,
        started_at=NOW,
        completed_at=NOW,
        status=status,
        steps=(_step(),),
    )


def test_workflow_status_is_derived_and_dto_keeps_synthesis_boundary() -> None:
    with pytest.raises(DataContractError, match="status"):
        _run(WorkflowRunStatus.PARTIAL)

    dto = WorkflowRunDTO.from_domain(
        _run(),
        fact_data=({"proxy_count": 3},),
        synthesis_contract=WorkflowSynthesisContractDTO(
            required_sections=("bull_case", "bear_case", "risk_critique"),
            candidate_update_tools=("thesis_revision_propose",),
            prohibited_outputs=("orders", "position_sizing"),
        ),
    )
    assert dto.facts[0].data == {"proxy_count": 3}
    assert dto.execution_effect is False


def test_workflow_repository_round_trips_receipts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyWorkflowRunRepository(engine)

    repository.append(_run())
    restored = repository.get("run_1")

    assert restored == _run()
    engine.dispose()


def test_account_transaction_repository_is_idempotent_and_filtered() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyAccountTransactionRepository(engine)
    transaction = AccountTransaction(
        provider_transaction_id="txn_hash_1",
        account_ref="account_hash_1",
        provider=VendorId.MOOMOO,
        instrument_id="equity:US:NVDA",
        kind=AccountTransactionKind.TRADE,
        side=AccountTransactionSide.BUY,
        quantity=Decimal("2"),
        price=Decimal("100.25"),
        fees=Decimal(0),
        currency="USD",
        occurred_at=NOW,
    )

    assert repository.append_many((transaction,)) == (transaction,)
    assert repository.append_many((transaction,)) == ()
    assert repository.list(providers=(VendorId.MOOMOO,), start=NOW, end=NOW, limit=10) == (
        transaction,
    )
    engine.dispose()
