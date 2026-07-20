from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.dto.tool_envelope import ErrorInfo, ToolEnvelope
from application.services.technical_tool_coordinator import TechnicalChartArtifact
from interfaces.mcp.chart_artifacts import LocalChartArtifact, persist_chart_png
from interfaces.mcp.server import create_mcp_server


def test_persist_chart_png_returns_visible_local_reference(tmp_path: Path) -> None:
    png = b"\x89PNG\r\n\x1a\nfixture"

    artifact = persist_chart_png(png, request_id="req_example", root=tmp_path)

    assert artifact.path == (tmp_path / "req_example.png").resolve()
    assert artifact.path.read_bytes() == png
    assert artifact.markdown == f"![Technical chart](<{artifact.path}>)"
    assert artifact.path.stat().st_mode & 0o777 == 0o600


def test_persist_chart_png_rejects_non_png(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a PNG"):
        persist_chart_png(b"not-an-image", request_id="req_example", root=tmp_path)


def test_persist_chart_png_hashes_unsafe_request_id(tmp_path: Path) -> None:
    artifact = persist_chart_png(
        b"\x89PNG\r\n\x1a\nfixture",
        request_id="../../escape",
        root=tmp_path,
    )

    assert artifact.path.parent == tmp_path.resolve()
    assert artifact.path.name.endswith(".png")
    assert "escape" not in artifact.path.name


@pytest.mark.asyncio
async def test_mcp_chart_returns_envelope_local_reference_and_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = (tmp_path / "chart.png").resolve()
    monkeypatch.setattr(
        "interfaces.mcp.server.persist_chart_png",
        lambda png, request_id: LocalChartArtifact(path=path),
    )
    envelope = ToolEnvelope[object].failure(
        request_id="req_chart",
        market=None,
        as_of="2026-07-21T12:00:00+00:00",
        fetched_at="2026-07-21T12:00:00+00:00",
        errors=[ErrorInfo(code="STUB", message="stub", retryable=False)],
    )
    container = MagicMock()
    container.settings.mcp_server_name = "chart-artifact-test"
    container.technical_tool_coordinator.render_chart = AsyncMock(
        return_value=TechnicalChartArtifact(
            envelope=envelope,
            png=b"\x89PNG\r\n\x1a\nfixture",
        )
    )
    manager = create_mcp_server(container)._tool_manager

    content = await manager.call_tool(
        "technical_render_chart",
        {"instrument_id": "equity:US:TEST"},
    )

    assert len(content) == 3
    assert content[0].type == "text"
    assert content[1].type == "text"
    assert str(path) in content[1].text
    assert f"![Technical chart](<{path}>)" in content[1].text
    assert content[2].type == "image"
    assert content[2].mimeType == "image/png"
