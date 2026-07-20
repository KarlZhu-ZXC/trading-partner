"""Non-secret settings view for the application layer."""

from __future__ import annotations

from typing import Protocol

from domain.common.enums import AppEnvironment


class AppSettingsView(Protocol):
    """Minimal settings surface — avoids importing infrastructure Settings."""

    app_name: str
    app_env: AppEnvironment
    mcp_server_name: str
