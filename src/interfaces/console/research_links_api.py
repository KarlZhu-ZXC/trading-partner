"""Read-only Research links projection; all writes use existing Candidate review."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from bootstrap import ApplicationContainer
from domain.common.errors import TradingPartnerError

router = APIRouter()


@router.get("/api/research/{subject_id}/questions/{question_id}/research-context")
async def question_research_context(
    request: Request,
    subject_id: str,
    question_id: str,
    version: int = Query(ge=1),
    thesis_revision_id: str | None = Query(default=None, min_length=1, max_length=128),
    assumption_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(
            container.services.question_research.prepare,
            subject_id,
            question_id,
            version,
            thesis_revision_id,
            assumption_id,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": error.code,
                "message": "Question context changed or is unavailable. Refresh the question.",
            },
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})


@router.get("/api/research/{subject_id}/evidence-links")
async def evidence_links(request: Request, subject_id: str) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(container.services.research_links.get, subject_id)
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": "Research links are unavailable for this scope"},
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})


@router.get("/api/research/{subject_id}/decisions/{decision_id}/replay")
async def decision_replay(request: Request, subject_id: str, decision_id: str) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(
            container.services.decision_replay.get, subject_id, decision_id
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=422,
            detail={"code": error.code, "message": "Decision replay is unavailable for this scope"},
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})


@router.get("/api/research/{subject_id}/plans/{plan_id}/workflow")
async def plan_workflow(
    request: Request,
    subject_id: str,
    plan_id: str,
    version: int = Query(ge=1),
) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(
            container.services.plan_workflow.get, subject_id, plan_id, version
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": error.code,
                "message": "Plan context changed or is unavailable. Reload Research.",
            },
        ) from None
    return JSONResponse(
        {"data": jsonable_encoder(data, custom_encoder={Decimal: str})},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/retro/{run_id}/workflow")
async def retro_workflow(request: Request, run_id: str) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(container.services.trade_retro.workflow, run_id)
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": error.code,
                "message": "Retro context is unavailable. Reload the review.",
            },
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})


@router.get("/api/retro/{run_id}/review-receipt")
async def retro_review_receipt(
    request: Request,
    run_id: str,
    idempotency_key: str = Query(min_length=1, max_length=200),
) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(
            container.services.trade_retro.review_receipt,
            run_id,
            idempotency_key,
        )
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": error.code, "message": "The review receipt does not match this run."},
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})


@router.get("/api/retro/{run_id}/detail")
async def retro_detail(request: Request, run_id: str) -> JSONResponse:
    container = cast(ApplicationContainer, request.app.state.container)
    try:
        data = await asyncio.to_thread(container.services.trade_retro.detail, run_id)
    except TradingPartnerError as error:
        raise HTTPException(
            status_code=404,
            detail={
                "code": error.code,
                "message": "The selected historical review is unavailable.",
            },
        ) from None
    return JSONResponse({"data": jsonable_encoder(data)}, headers={"Cache-Control": "no-store"})
