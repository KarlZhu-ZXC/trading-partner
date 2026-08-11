"""Focused generic notification validation, idempotency, and HTML coverage."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine, select

from application.dto.notifications import NotificationEnqueueReceipt, NotificationSendReceipt
from application.services.notification_service import NotificationService
from domain.common.errors import DataContractError, IdempotencyConflict
from domain.notifications.enums import (
    NotificationChannel,
    NotificationSourceType,
    NotificationStatus,
)
from domain.notifications.models import NotificationMessage, NotificationOutboxEntry
from domain.notifications.rendering import render_plain_text_html
from infrastructure.persistence.metadata import Base
from infrastructure.persistence.monitor_repository import SqlAlchemyMonitorRepository
from infrastructure.persistence.orm import NotificationOutboxRow
from infrastructure.providers.notifications.telegram import TelegramNotificationAdapter
from interfaces.cli import notifications as notifications_cli


@pytest.mark.asyncio
async def test_manual_enqueue_is_idempotent_and_persists_generic_source(fixed_clock) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    sender = AsyncMock()
    sender.send.return_value = NotificationSendReceipt(delivered=True, retryable=False)
    service = NotificationService(
        repository,
        sender,
        fixed_clock,
        enabled=True,
        configured=True,
    )

    first = await service.enqueue_text(
        "Ops <notice>",
        "body & details",
        idempotency_key="manual-1",
        confirmed_by="user",
        authorization_note="Explicitly authorized",
    )
    replay = await service.enqueue_text(
        "Ops <notice>",
        "body & details",
        idempotency_key="manual-1",
        confirmed_by="user",
        authorization_note="Explicitly authorized",
    )

    assert replay.notification_id == first.notification_id
    with engine.connect() as connection:
        rows = connection.execute(select(NotificationOutboxRow)).all()
    assert len(rows) == 1
    assert first.source_type.value == "MANUAL"
    assert first.source_id == "manual-1"

    delivered = await service.flush_pending()
    assert delivered.delivered == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_equal_manual_messages_remain_independent_outbox_deliveries(fixed_clock) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyMonitorRepository(engine)
    sender = AsyncMock()
    sender.send.return_value = NotificationSendReceipt(delivered=True, retryable=False)
    service = NotificationService(
        repository,
        sender,
        fixed_clock,
        enabled=True,
        configured=True,
    )

    for key in ("manual-equal-1", "manual-equal-2"):
        await service.enqueue_text(
            "Same title",
            "Same body",
            idempotency_key=key,
            confirmed_by="user",
            authorization_note=f"Explicit authorization for {key}",
        )

    receipt = await service.flush_pending()

    assert receipt.pending_selected == 2
    assert receipt.delivered == 2
    assert sender.send.await_count == 2
    engine.dispose()


@pytest.mark.asyncio
async def test_manual_enqueue_rejects_reused_key_with_different_payload(fixed_clock) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    service = NotificationService(
        SqlAlchemyMonitorRepository(engine),
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    await service.enqueue_text(
        "Title",
        "body",
        idempotency_key="manual-2",
        confirmed_by="external_agent",
        authorization_note="Approved by operator",
    )
    with pytest.raises(IdempotencyConflict):
        await service.enqueue_text(
            "Different title",
            "body",
            idempotency_key="manual-2",
            confirmed_by="external_agent",
            authorization_note="Approved by operator",
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_system_enqueue_is_idempotent_and_carries_no_user_authorization(fixed_clock) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    service = NotificationService(
        SqlAlchemyMonitorRepository(engine),
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    expires_at = fixed_clock.now() + timedelta(hours=6)

    first = await service.enqueue_system_text(
        source_id="catalyst-agenda:daily:2026-08-09:w7",
        title="未来 7 天催化事项",
        body="TSLA · 财报发布日期待核验",
        idempotency_key="catalyst-agenda:daily:2026-08-09:w7",
        expires_at=expires_at,
    )
    replay = await service.enqueue_system_text(
        source_id="catalyst-agenda:daily:2026-08-09:w7",
        title="未来 7 天催化事项",
        body="TSLA · 财报发布日期待核验",
        idempotency_key="catalyst-agenda:daily:2026-08-09:w7",
        expires_at=expires_at,
    )

    assert replay.notification_id == first.notification_id
    assert first.source_type is NotificationSourceType.SYSTEM
    assert first.confirmed_by is None
    assert first.authorization_note is None
    with pytest.raises(IdempotencyConflict):
        await service.enqueue_system_text(
            source_id="catalyst-agenda:daily:2026-08-09:w7",
            title="未来 7 天催化事项",
            body="different",
            idempotency_key="catalyst-agenda:daily:2026-08-09:w7",
            expires_at=expires_at,
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_manual_enqueue_expiry_replay_is_explicit_only(fixed_clock) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    service = NotificationService(
        SqlAlchemyMonitorRepository(engine),
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    explicit_expiry = fixed_clock.now() + timedelta(hours=2)
    first = await service.enqueue_text(
        "Title",
        "body",
        idempotency_key="manual-expiry",
        confirmed_by="user",
        authorization_note="Approved by operator",
        expires_at=explicit_expiry,
    )
    replay = await service.enqueue_text(
        "Title",
        "body",
        idempotency_key="manual-expiry",
        confirmed_by="user",
        authorization_note="Approved by operator",
        expires_at=explicit_expiry,
    )
    assert replay.notification_id == first.notification_id
    with pytest.raises(IdempotencyConflict):
        await service.enqueue_text(
            "Title",
            "body",
            idempotency_key="manual-expiry",
            confirmed_by="user",
            authorization_note="Approved by operator",
            expires_at=explicit_expiry + timedelta(hours=1),
        )

    fixed_clock.advance(3600)
    default_first = await service.enqueue_text(
        "Default title",
        "body",
        idempotency_key="manual-default-expiry",
        confirmed_by="user",
        authorization_note="Approved by operator",
    )
    fixed_clock.advance(3600)
    default_replay = await service.enqueue_text(
        "Default title",
        "body",
        idempotency_key="manual-default-expiry",
        confirmed_by="user",
        authorization_note="Approved by operator",
    )
    assert default_replay.notification_id == default_first.notification_id
    assert default_replay.expires_at == default_first.expires_at
    engine.dispose()


@pytest.mark.parametrize(
    ("title", "body", "confirmed_by", "authorization_note"),
    (
        ("", "body", "user", "note"),
        ("title", "", "user", "note"),
        ("title", "body", "codex", "note"),
        ("title", "body", "user", ""),
    ),
)
async def test_manual_enqueue_validates_explicit_authorization(
    fixed_clock,
    title: str,
    body: str,
    confirmed_by: str,
    authorization_note: str,
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    service = NotificationService(
        SqlAlchemyMonitorRepository(engine),
        None,
        fixed_clock,
        enabled=False,
        configured=False,
    )
    with pytest.raises(DataContractError):
        await service.enqueue_text(
            title,
            body,
            idempotency_key="manual-validation",
            confirmed_by=confirmed_by,
            authorization_note=authorization_note,
        )
    engine.dispose()


def test_manual_html_fallback_escapes_title_and_body() -> None:
    rendered = render_plain_text_html("<title>", "<script>alert(1)</script> & text")
    assert rendered == "<b>&lt;title&gt;</b>\n\n&lt;script&gt;alert(1)&lt;/script&gt; &amp; text"


def test_system_entry_rejects_manual_authorization_fields() -> None:
    now = datetime.now(UTC)
    with pytest.raises(DataContractError):
        NotificationOutboxEntry(
            notification_id="notification_00000000-0000-7000-8000-000000000003",
            source_type=NotificationSourceType.SYSTEM,
            source_id="system-test",
            channel=NotificationChannel.TELEGRAM,
            title="System test",
            body="body",
            status=NotificationStatus.PENDING,
            attempt_count=0,
            next_attempt_at=now,
            created_at=now,
            confirmed_by="user",
            authorization_note="must be rejected",
        )


def test_manual_messages_require_expiry() -> None:
    now = datetime.now(UTC)
    common = {
        "notification_id": "notification_00000000-0000-7000-8000-000000000004",
        "source_type": NotificationSourceType.MANUAL,
        "source_id": "manual-expiry-required",
        "channel": NotificationChannel.TELEGRAM,
        "title": "Manual title",
        "body": "body",
        "idempotency_key": "manual-expiry-required",
        "confirmed_by": "user",
        "authorization_note": "Explicitly authorized",
    }
    with pytest.raises(DataContractError):
        NotificationMessage(created_at=now, **common)
    with pytest.raises(DataContractError):
        NotificationOutboxEntry(
            status=NotificationStatus.PENDING,
            attempt_count=0,
            next_attempt_at=now,
            created_at=now,
            **common,
        )


@pytest.mark.asyncio
async def test_manual_body_markers_never_enter_monitor_formatter() -> None:
    now = datetime.now(UTC)
    body = "POST_MARKET_SUMMARY\n本轮变化：0\nRULES\nCHANGES\n<raw> body"
    entry = NotificationOutboxEntry(
        notification_id="notification_00000000-0000-7000-8000-000000000002",
        source_type=NotificationSourceType.MANUAL,
        source_id="manual-marker-collision",
        channel=NotificationChannel.TELEGRAM,
        title="Manual title",
        body=body,
        status=NotificationStatus.PENDING,
        attempt_count=0,
        next_attempt_at=now,
        created_at=now,
        idempotency_key="manual-marker-collision",
        confirmed_by="user",
        authorization_note="Explicitly authorized marker test",
        expires_at=now.replace(year=now.year + 1),
    )
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramNotificationAdapter(
        bot_token="123456:TOP_SECRET_TOKEN",
        chat_id="123",
        client=client,
    )
    receipt = await adapter.send(entry)
    await client.aclose()

    assert receipt.delivered is True
    assert observed["text"] == render_plain_text_html(entry.title, body)
    assert "本轮无状态变化" not in str(observed["text"])


@pytest.mark.asyncio
async def test_enqueue_cli_receipt_does_not_echo_body_or_authorization(monkeypatch, capsys) -> None:
    now = datetime.now(UTC)
    entry = NotificationOutboxEntry(
        notification_id="notification_00000000-0000-7000-8000-000000000001",
        source_type=NotificationSourceType.MANUAL,
        source_id="cli-key",
        channel=NotificationChannel.TELEGRAM,
        title="CLI title",
        body="secret body",
        status=NotificationStatus.PENDING,
        attempt_count=0,
        next_attempt_at=now,
        created_at=now,
        idempotency_key="cli-key",
        confirmed_by="user",
        authorization_note="Explicitly authorized",
        expires_at=now.replace(year=now.year + 1),
    )
    service = SimpleNamespace(
        enqueue_text=AsyncMock(return_value=entry),
        enqueue_receipt=lambda value: NotificationEnqueueReceipt(
            notification_id=value.notification_id,
            source_type=value.source_type.value,
            source_id=value.source_id,
            channel=value.channel.value,
            title=value.title,
            status=value.status.value,
            expires_at=value.expires_at,
        ),
    )
    container = SimpleNamespace(
        operations=SimpleNamespace(notifications=service),
        resources=SimpleNamespace(monitor_run_lock=None),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(notifications_cli, "build_default_application", lambda: container)
    monkeypatch.setattr(notifications_cli.sys, "stdin", StringIO("secret body"))

    assert (
        await notifications_cli._run(
            "enqueue",
            title="CLI title",
            idempotency_key="cli-key",
            confirmed_by="user",
            authorization_note="secret authorization",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "secret body" not in output
    assert "secret authorization" not in output
