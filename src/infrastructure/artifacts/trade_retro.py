"""Safe Obsidian Markdown projection for durable Trade Retro runs."""

from __future__ import annotations

import hashlib
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from domain.common.errors import ConfigurationError, DataContractError
from domain.retro.models import (
    TradeRetroReviewRevision,
    TradeRetroRun,
    trade_retro_finding_key,
)

_START = "<!-- trading-partner:retro:start"
_END = "<!-- trading-partner:retro:end -->"


class ObsidianTradeRetroExporter:
    def __init__(self, journal_root: Path | None) -> None:
        self._journal_root = journal_root

    def export(
        self,
        run: TradeRetroRun,
        review: TradeRetroReviewRevision | None = None,
    ) -> tuple[Path, str]:
        root = self._journal_root
        if root is None:
            raise ConfigurationError(
                "RETRO_OBSIDIAN_JOURNAL_DIR is not configured",
                details={"setting": "RETRO_OBSIDIAN_JOURNAL_DIR"},
            )
        root = root.expanduser()
        if root.is_symlink():
            raise DataContractError("Trade Retro journal root must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = root.resolve(strict=True)
        week = run.period_start.isocalendar().week
        target = root / f"Week{week}.md"
        if target.exists() and target.is_symlink():
            raise DataContractError("Trade Retro target must not be a symlink")
        if not target.resolve(strict=False).is_relative_to(root):
            raise DataContractError("Trade Retro target escapes configured journal root")
        current = target.read_text(encoding="utf-8") if target.exists() else f"# Week {week}\n"
        block = self._block(run, review)
        updated = self._replace_owned_block(current, block)
        digest = hashlib.sha256(updated.encode("utf-8")).hexdigest()
        self._atomic_write(target, updated)
        return target, digest

    @staticmethod
    def _block(
        run: TradeRetroRun,
        review: TradeRetroReviewRevision | None = None,
    ) -> str:
        review_block = ""
        if review is not None:
            finding_titles = {
                trade_retro_finding_key(item): f"{item.code} · {item.title}"
                for item in run.findings
            }
            lines = [
                "",
                f"### 人工复核 · v{review.version} · {review.status.value}",
            ]
            if review.note_markdown:
                lines.extend(["", review.note_markdown.strip()])
            if review.action_items:
                lines.extend(["", "#### 行动项"])
                lines.extend(f"- [ ] {item}" for item in review.action_items)
            if review.finding_reviews:
                lines.extend(["", "#### Finding 复核"])
                lines.extend(
                    (
                        f"- **{item.status.value}** · "
                        f"{finding_titles.get(item.finding_key, item.finding_key)}"
                        f"{f' — {item.note}' if item.note else ''}"
                    )
                    for item in review.finding_reviews
                )
            lines.extend(
                [
                    "",
                    f"- `review_version`: `{review.version}`",
                    f"- `reviewed_by`: `{review.reviewed_by}`",
                ]
            )
            review_block = "\n".join(lines) + "\n"
        return (
            f"{_START} run_id={run.run_id} -->\n"
            "## Trading Partner · Trade Retro\n\n"
            f"{run.summary_markdown.strip()}\n\n"
            f"{review_block}"
            f"- `run_id`: `{run.run_id}`\n"
            f"- `period`: `{run.period_start.isoformat()}` → `{run.period_end.isoformat()}`\n"
            f"- `status`: `{run.status.value}`\n"
            f"- `execution_effect`: `false`\n"
            f"{_END}"
        )

    @staticmethod
    def _replace_owned_block(current: str, block: str) -> str:
        start = current.find(_START)
        end = current.find(_END)
        if start < 0 and end < 0:
            return f"{current.rstrip()}\n\n{block}\n"
        if start < 0 or end < start:
            raise DataContractError("Existing Trade Retro marker block is malformed")
        end += len(_END)
        return f"{current[:start].rstrip()}\n\n{block}{current[end:]}".rstrip() + "\n"

    @staticmethod
    def _atomic_write(target: Path, content: str) -> None:
        descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, target)
        except Exception:
            with suppress(OSError):
                os.close(descriptor)
            Path(name).unlink(missing_ok=True)
            raise
