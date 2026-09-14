"""Read latest synced owner thinking without interpreting or changing private notes."""

import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from application.ports.clock import Clock
from application.ports.external_note_repository import ExternalNoteRepository
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import AttributedNoteBlock, ExternalNoteRevision


class LatestThinkingReader:
    def __init__(self, notes: ExternalNoteRepository, clock: Clock, timezone: str = "UTC") -> None:
        self._notes = notes
        self._clock = clock
        self._zone = ZoneInfo(timezone)

    def get(self, instrument_id: str | None) -> list[dict[str, object]]:
        if instrument_id is None:
            return []
        now = self._clock.now()
        revisions = self._notes.list_revisions_for_instrument(instrument_id, observed_through=now)
        latest: dict[str, ExternalNoteRevision] = {}
        for revision in revisions:
            if revision.observed_at > now:
                continue
            previous = latest.get(revision.note_id)
            if previous is None or revision.version > previous.version:
                latest[revision.note_id] = revision
        eligible = []
        for revision in latest.values():
            identity = self._notes.get(revision.note_id)
            if (
                identity is not None
                and identity.source == "MOOMOO_NOTE"
                and identity.primary_instrument_id == instrument_id
            ):
                eligible.append(revision)
        eligible.sort(
            key=lambda r: (r.source_timestamp or r.observed_at, r.observed_at, r.note_id),
            reverse=True,
        )
        result = [self._extract(revision, now) for revision in eligible[:5]]
        if len(eligible) > 5:
            for item in result:
                warnings = item["warnings"]
                assert isinstance(warnings, list)
                warnings.append("LATEST_NOTES_TRUNCATED_TO_FIVE")
        return result

    def _extract(self, revision: ExternalNoteRevision, now: datetime) -> dict[str, object]:
        warnings: list[str] = []
        item: dict[str, object] = {
            "note_id": revision.note_id,
            "note_revision_id": revision.note_revision_id,
            "version": revision.version,
            "title": revision.title,
            "source_timestamp": (
                revision.source_timestamp.isoformat() if revision.source_timestamp else None
            ),
            "observed_at": revision.observed_at.isoformat(),
            "status": "PENDING",
            "user_excerpt": "",
            "user_summary": "",
            "other_viewpoints": [],
            "warnings": warnings,
        }
        if revision.source_timestamp is None:
            warnings.append("SOURCE_TIMESTAMP_MISSING")
        elif revision.source_timestamp > now:
            warnings.append("SOURCE_TIMESTAMP_IN_FUTURE")
        if revision.coverage is not NoteCoverage.FULL:
            item["status"] = "SUMMARY_ONLY"
            warnings.append("SUMMARY_ONLY_NO_AUTHOR_EXTRACTION")
            return item
        selected = _latest_user_blocks(revision.blocks, now.astimezone(self._zone).date(), warnings)
        item["user_excerpt"] = _bounded(
            "\n\n".join(
                f"[{block.section_date or 'date unknown'} · block {block.ordinal}] {block.body}"
                for block in selected
            ),
            3000,
            "USER_EXCERPT_TRUNCATED",
            warnings,
        )
        try:
            interpretation = self._notes.interpretation_for_revision(revision.note_revision_id)
        except Exception:
            item["status"] = "UNAVAILABLE"
            warnings.append("INTERPRETATION_SOURCE_UNAVAILABLE")
            return item
        if interpretation is None:
            warnings.append("EXACT_REVISION_INTERPRETATION_PENDING")
            return item
        if interpretation.note_revision_id != revision.note_revision_id:
            item["status"] = "UNAVAILABLE"
            warnings.append("INTERPRETATION_REVISION_MISMATCH")
            return item
        if interpretation.created_at > now:
            warnings.append("INTERPRETATION_NOT_YET_VISIBLE")
            return item
        if interpretation.status != "SUCCEEDED":
            item["status"] = "PENDING" if interpretation.status == "PENDING" else "UNAVAILABLE"
            warnings.append("EXACT_REVISION_INTERPRETATION_NOT_SUCCEEDED")
            return item
        try:
            payload = json.loads(interpretation.payload_json)
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, dict) or not isinstance(payload.get("viewpoints"), list):
            item["status"] = "UNAVAILABLE"
            warnings.append("INTERPRETATION_PAYLOAD_INVALID")
            return item
        blocks = {block.ordinal: block for block in revision.blocks}
        selected_ordinals = {block.ordinal for block in selected}
        summaries: list[str] = []
        others: list[dict[str, str]] = []
        for viewpoint in payload["viewpoints"][:100]:
            parsed = _viewpoint(viewpoint)
            if parsed is None:
                warnings.append("VIEWPOINT_PROVENANCE_INVALID")
                continue
            kind, speaker, summary, ordinals = parsed
            referenced = [blocks.get(ordinal) for ordinal in ordinals]
            if any(block is None for block in referenced):
                warnings.append("VIEWPOINT_PROVENANCE_INVALID")
                continue
            if kind == "USER":
                if not set(ordinals).issubset(selected_ordinals):
                    warnings.append("USER_VIEWPOINT_OUTSIDE_LATEST_SECTION")
                    continue
                summaries.append(summary)
            elif kind == "NAMED_PERSON" and all(
                block is not None
                and block.speaker_kind is NoteSpeakerKind.NAMED_PERSON
                and block.speaker_label == speaker
                and _visible_date(block.section_date, now.astimezone(self._zone).date(), warnings)
                for block in referenced
            ):
                others.append({"speaker": speaker, "summary": summary})
            else:
                warnings.append("VIEWPOINT_SPEAKER_MISMATCH")
        item["user_summary"] = _bounded(
            "\n\n".join(summaries), 2000, "USER_SUMMARY_TRUNCATED", warnings
        )
        item["other_viewpoints"] = [
            {
                "speaker": other["speaker"],
                "summary": _bounded(other["summary"], 1000, "OTHER_VIEWPOINT_TRUNCATED", warnings),
            }
            for other in others[:5]
        ]
        if len(others) > 5 or len(payload["viewpoints"]) > 100:
            warnings.append("VIEWPOINTS_TRUNCATED")
        item["status"] = "EXTRACTED" if summaries else "UNAVAILABLE"
        if not summaries:
            warnings.append("NO_VALID_LATEST_USER_SUMMARY")
        item["warnings"] = list(dict.fromkeys(warnings))
        return item


def _section_date(value: str | None, today: date, warnings: list[str]) -> date | None:
    if not value:
        return None
    try:
        if re.fullmatch(r"\d{1,2}/\d{1,2}", value):
            month, day = map(int, value.split("/"))
            warnings.append("SECTION_YEAR_INFERRED_FROM_READ_DATE")
            return date(today.year, month, day)
        return date.fromisoformat(value)
    except ValueError:
        warnings.append("SECTION_DATE_INVALID")
        return None


def _visible_date(value: str | None, today: date, warnings: list[str]) -> bool:
    parsed = _section_date(value, today, warnings)
    return not value or (parsed is not None and parsed <= today)


def _latest_user_blocks(
    blocks: tuple[AttributedNoteBlock, ...], today: date, warnings: list[str]
) -> list[AttributedNoteBlock]:
    users = [block for block in blocks if block.speaker_kind is NoteSpeakerKind.USER]
    if not users:
        warnings.append("USER_AUTHOR_BLOCK_MISSING")
        return []
    dated: list[tuple[date, AttributedNoteBlock]] = []
    undated = []
    for block in users:
        parsed = _section_date(block.section_date, today, warnings)
        if parsed is not None and parsed > today:
            warnings.append("FUTURE_USER_SECTION_EXCLUDED")
        elif parsed is not None:
            dated.append((parsed, block))
        elif not block.section_date:
            undated.append(block)
    if dated:
        newest = max(day for day, _ in dated)
        if undated:
            warnings.append("UNDATED_USER_SECTIONS_EXCLUDED")
        if any(day < newest for day, _ in dated):
            warnings.append("OLDER_USER_SECTIONS_EXCLUDED")
        return [block for day, block in dated if day == newest]
    warnings.append("USER_SECTION_DATE_MISSING")
    return undated


def _bounded(value: str, maximum: int, code: str, warnings: list[str]) -> str:
    if len(value) > maximum:
        warnings.append(code)
    return value[:maximum]


def _viewpoint(value: Any) -> tuple[str, str, str, list[int]] | None:
    if not isinstance(value, dict):
        return None
    kind, speaker, summary, refs = (
        value.get("speaker_kind"),
        value.get("speaker_label"),
        value.get("summary"),
        value.get("source_block_ordinals"),
    )
    if (
        not isinstance(kind, str)
        or kind not in {"USER", "NAMED_PERSON"}
        or not isinstance(speaker, str)
        or not speaker.strip()
        or len(speaker) > 80
        or not isinstance(summary, str)
        or not summary.strip()
        or len(summary) > 20_000
        or not isinstance(refs, list)
        or not refs
        or len(refs) > 100
        or any(type(ordinal) is not int or ordinal < 0 for ordinal in refs)
    ):
        return None
    return kind, speaker, summary, refs
