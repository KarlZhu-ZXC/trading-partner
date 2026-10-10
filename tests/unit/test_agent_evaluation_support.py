"""Tests for the interface-independent deterministic evaluation support."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from domain.common.ids import EntityIdPrefix
from interfaces.cli.agent_evaluation_support import (
    EVALUATION_PROJECT_ROOT,
    EvaluationClock,
    EvaluationIds,
    evaluation_fingerprint_manifest,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_support_preserves_deterministic_ids_clock_and_manifest() -> None:
    ids = EvaluationIds()
    assert ids.new(EntityIdPrefix.AGENT_CONVERSATION) == "agent_conversation_eval_0001"
    assert ids.new(EntityIdPrefix.AGENT_MESSAGE) == "agent_message_eval_0002"
    assert EvaluationClock().now().isoformat() == "2026-08-13T12:00:00+00:00"
    assert EVALUATION_PROJECT_ROOT == PROJECT_ROOT
    assert set(evaluation_fingerprint_manifest()) >= {
        "evals/agent-behavior.v1.json",
        "src/interfaces/agent/prompts.py",
        "src/application/services/agent_runtime_service.py",
    }


def test_live_evaluation_import_isolated_from_console_and_behavior_runner() -> None:
    source_root = PROJECT_ROOT / "src"
    script = f"""
import sys
sys.path.insert(0, {str(source_root)!r})
import interfaces.cli.copilot_research_live_evaluation
assert "interfaces.cli.agent_behavior_evaluation" not in sys.modules
assert "interfaces.console.agent_api" not in sys.modules
assert "fastapi" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
