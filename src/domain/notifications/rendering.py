"""Shared deterministic Telegram plain-text rendering helpers."""

from __future__ import annotations

import html

TELEGRAM_MAX_TEXT_LENGTH = 4096


def render_plain_text_html(title: str, body: str) -> str:
    """Render a generic notification without interpreting body control lines."""

    return f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"


def rendered_plain_text_length(title: str, body: str) -> int:
    return len(render_plain_text_html(title, body))
