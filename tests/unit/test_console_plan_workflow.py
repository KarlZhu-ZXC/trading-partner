import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from domain.common.errors import DataContractError
from interfaces.console.research_links_api import plan_workflow


@pytest.mark.asyncio
async def test_plan_workflow_read_preserves_decimal_and_rejects_changed_scope():
    service = Mock()
    service.get.return_value = {"field_changes": [{"after": Decimal("123.123456789123456789")}]}
    container = SimpleNamespace(services=SimpleNamespace(plan_workflow=service))
    request = Request(
        {"type": "http", "app": SimpleNamespace(state=SimpleNamespace(container=container))}
    )
    response = await plan_workflow(request, "case_synthetic", "trade_plan_synthetic", 2)
    assert response.headers["cache-control"] == "no-store"
    assert (
        json.loads(response.body)["data"]["field_changes"][0]["after"] == "123.123456789123456789"
    )
    service.get.assert_called_once_with("case_synthetic", "trade_plan_synthetic", 2)
    service.get.side_effect = DataContractError("internal detail must not leak")
    with pytest.raises(HTTPException) as raised:
        await plan_workflow(request, "case_synthetic", "trade_plan_synthetic", 1)
    assert raised.value.status_code == 409
    assert "internal detail" not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_retro_receipt_is_read_only_and_run_scoped():
    from interfaces.console.research_links_api import retro_review_receipt

    service = Mock()
    service.review_receipt.return_value = None
    container = SimpleNamespace(services=SimpleNamespace(trade_retro=service))
    request = Request(
        {"type": "http", "app": SimpleNamespace(state=SimpleNamespace(container=container))}
    )
    response = await retro_review_receipt(request, "retro_synthetic", "request-key")
    assert json.loads(response.body) == {"data": None}
    assert response.headers["cache-control"] == "no-store"
    service.review_receipt.assert_called_once_with("retro_synthetic", "request-key")
    service.review.assert_not_called()
    service.review_receipt.side_effect = DataContractError("private detail")
    with pytest.raises(HTTPException) as rejected:
        await retro_review_receipt(request, "retro_other", "request-key")
    assert rejected.value.status_code == 409
    assert "private detail" not in str(rejected.value.detail)
