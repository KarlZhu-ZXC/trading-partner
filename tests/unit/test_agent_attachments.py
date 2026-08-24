from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from application.dto.agent import AgentImageInput
from application.services.agent_attachment_validation import decode_image_data_url
from application.services.agent_context_service import AgentContextService
from domain.agent.attachments import AgentImageAttachment
from domain.agent.enums import AgentChannel, AgentMessageRole
from domain.agent.models import AgentConversation, AgentMessage
from domain.common.errors import DataContractError
from infrastructure.attachments.agent import FileAgentAttachmentStore

_PNG_1X1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_file_agent_attachment_store_round_trips_private_png(tmp_path: Path) -> None:
    store = FileAgentAttachmentStore(tmp_path / "attachments")

    value = store.save(
        attachment_id="agent_attachment_test",
        content=_PNG_1X1,
        media_type="image/png",
        original_name="../chart.png",
    )

    assert value.width == 1
    assert value.height == 1
    assert value.original_name == "chart.png"
    assert store.read(value) == _PNG_1X1
    assert (tmp_path / "attachments").stat().st_mode & 0o777 == 0o700
    assert next((tmp_path / "attachments").glob("*.img")).stat().st_mode & 0o777 == 0o600


def test_file_agent_attachment_store_rejects_invalid_image_bytes(tmp_path: Path) -> None:
    store = FileAgentAttachmentStore(tmp_path / "attachments")

    with pytest.raises(DataContractError):
        store.save(
            attachment_id="agent_attachment_bad",
            content=b"not-an-image",
            media_type="image/png",
            original_name=None,
        )


def test_context_service_replays_image_as_openai_content_part(tmp_path: Path) -> None:
    store = FileAgentAttachmentStore(tmp_path / "attachments")
    attachment = store.save(
        attachment_id="agent_attachment_context",
        content=_PNG_1X1,
        media_type="image/png",
        original_name="chart.png",
    )
    now = datetime(2026, 8, 22, tzinfo=UTC)
    conversation = AgentConversation(
        conversation_id="agent_conversation_test",
        owner_principal="local-console",
        title="Images",
        created_at=now,
        updated_at=now,
    )
    message = AgentMessage(
        message_id="agent_message_test",
        conversation_id=conversation.conversation_id,
        role=AgentMessageRole.USER,
        content="What is in this chart?",
        created_at=now,
        channel=AgentChannel.CONSOLE,
        attachments=(attachment,),
    )

    class Repository:
        def list_messages(self, *_args: object, **_kwargs: object) -> tuple[AgentMessage, ...]:
            return (message,)

    context = AgentContextService(
        repository=Repository(),  # type: ignore[arg-type]
        clock=None,  # type: ignore[arg-type]
        id_generator=None,  # type: ignore[arg-type]
        attachment_store=store,
    )

    values = context.model_messages(conversation=conversation, system_prompt="system")

    assert values[-1].content[0]["type"] == "text"  # type: ignore[index]
    assert values[-1].content[1]["type"] == "image_url"  # type: ignore[index]
    assert (
        values[-1]
        .content[1]["image_url"]["url"]
        .startswith(  # type: ignore[index]
            "data:image/png;base64,"
        )
    )


def test_agent_image_input_rejects_oversized_content() -> None:
    with pytest.raises(ValueError):
        AgentImageInput(content=b"x" * 2_000_001, media_type="image/png")


def test_agent_image_data_url_decoder_accepts_bounded_png() -> None:
    import base64

    encoded = base64.b64encode(_PNG_1X1).decode("ascii")

    assert decode_image_data_url(f"data:image/png;base64,{encoded}") == (
        "image/png",
        _PNG_1X1,
    )


@pytest.mark.parametrize(
    "value",
    (
        b"not-text",
        "https://example.com/image.png",
        "data:image/gif;base64,AAAA",
        "data:image/png;base64,=invalid",
        "data:image/png;base64,",
    ),
)
def test_agent_image_data_url_decoder_rejects_unsafe_inputs(value: object) -> None:
    with pytest.raises(DataContractError):
        decode_image_data_url(value)  # type: ignore[arg-type]


def test_attachment_metadata_round_trips() -> None:
    value = AgentImageAttachment(
        attachment_id="agent_attachment_metadata",
        media_type="image/jpeg",
        byte_size=10,
        sha256="a" * 64,
        width=10,
        height=20,
    )
    assert AgentImageAttachment.from_dict(value.as_dict()) == value
