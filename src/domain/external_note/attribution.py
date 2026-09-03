"""Deterministic speaker attribution shared by observation-source adapters."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal

from domain.external_note.enums import NoteSpeakerKind
from domain.external_note.models import AttributedNoteBlock

_EXPLICIT_SPEAKER = re.compile(r"^(?P<label>[^：:\n]{1,20})[：:]\s*(?P<body>.+)$")
_SPEAKER_MARKER = re.compile(r"^(?P<explicit>@)?(?P<label>[^：:\n]{1,20})(?:[：:]\s*)?$")
_INLINE_SECTION = re.compile(
    r"(^|[\s。！？!?；;])(?P<label>[^：:\n\s。！？!?；;]{1,20})[：:]"
)
_DATE_VALUE = re.compile(
    r"^(?:(?P<year>20[0-9]{2})[-/.])?"
    r"(?P<month>0?[1-9]|1[0-2])[-/.]"
    r"(?P<day>0?[1-9]|[12][0-9]|3[01])(?:\s|[:：]|$)"
)
_USER_LABELS = frozenset({"我", "本人", "我的观点", "自己", "USER"})
_NAMED_SPEAKERS = {
    "boss墨": "boss墨",
    "宝总": "宝总",
    "姜汁汽水": "姜汁汽水",
}

type NoteSectionOrder = Literal[
    "NEWEST_TO_OLDEST",
    "OLDEST_TO_NEWEST",
    "MIXED",
    "UNKNOWN",
]


def detect_section_order(body: str) -> NoteSectionOrder:
    """Classify dated sections without changing their source order."""

    dated_sections: list[tuple[int | None, int, int]] = []
    for line in (item.strip() for item in body.splitlines() if item.strip()):
        match = _DATE_VALUE.match(line)
        if match is None:
            continue
        year = int(match.group("year")) if match.group("year") else None
        month = int(match.group("month"))
        day = int(match.group("day"))
        try:
            date(year or 2000, month, day)
        except ValueError:
            continue
        dated_sections.append((year, month, day))

    directions: set[int] = set()
    for current, following in zip(dated_sections, dated_sections[1:], strict=False):
        direction = _date_direction(current, following)
        if direction != 0:
            directions.add(direction)
    if not directions:
        return "UNKNOWN"
    if len(directions) > 1:
        return "MIXED"
    return "OLDEST_TO_NEWEST" if 1 in directions else "NEWEST_TO_OLDEST"


def _date_direction(
    current: tuple[int | None, int, int],
    following: tuple[int | None, int, int],
) -> int:
    current_year, current_month, current_day = current
    following_year, following_month, following_day = following
    if current_year is not None and following_year is not None:
        current_date = date(current_year, current_month, current_day)
        following_date = date(following_year, following_month, following_day)
        return (following_date > current_date) - (following_date < current_date)

    current_ordinal = date(2000, current_month, current_day).timetuple().tm_yday
    following_ordinal = date(2000, following_month, following_day).timetuple().tm_yday
    direction = (following_ordinal > current_ordinal) - (
        following_ordinal < current_ordinal
    )
    if current_month <= 2 and following_month >= 11:
        return -1
    if current_month >= 11 and following_month <= 2:
        return 1
    return direction


def attributed_blocks(body: str) -> tuple[AttributedNoteBlock, ...]:
    result: list[AttributedNoteBlock] = []
    speaker_kind = NoteSpeakerKind.USER
    speaker_label = "USER"
    section_date: str | None = None
    for line in (item.strip() for item in body.splitlines() if item.strip()):
        dated = _dated_section(line)
        if dated is not None:
            section_date, line = dated
            speaker_kind = NoteSpeakerKind.USER
            speaker_label = "USER"
            if not line:
                continue
        marker = _SPEAKER_MARKER.fullmatch(line)
        if marker is not None:
            canonical = _canonical_speaker(
                marker.group("label"),
                explicit_at=marker.group("explicit") is not None,
            )
            if canonical == "USER":
                speaker_kind = NoteSpeakerKind.USER
                speaker_label = "USER"
                continue
            if canonical is not None:
                speaker_kind = NoteSpeakerKind.NAMED_PERSON
                speaker_label = canonical
                continue
        for segment in _split_inline_sections(line):
            match = _EXPLICIT_SPEAKER.fullmatch(segment)
            if match is not None:
                raw_label = match.group("label").strip()
                explicit_at = raw_label.startswith("@")
                label = raw_label.removeprefix("@").strip()
                explicit_body = match.group("body").strip()
                canonical = _canonical_speaker(label, explicit_at=explicit_at)
                if canonical == "USER":
                    speaker_kind = NoteSpeakerKind.USER
                    speaker_label = "USER"
                    block_body = explicit_body
                elif canonical is not None:
                    speaker_kind = NoteSpeakerKind.NAMED_PERSON
                    speaker_label = canonical
                    block_body = explicit_body
                else:
                    # Unknown ``heading:`` prefixes are structural text, not
                    # evidence that a new person exists.
                    block_body = segment
            else:
                block_body = segment
            _append_attributed_block(
                result,
                speaker_kind=speaker_kind,
                speaker_label=speaker_label,
                body=block_body,
                section_date=section_date,
            )
    return tuple(result)


def _canonical_speaker(label: str, *, explicit_at: bool) -> str | None:
    normalized = label.strip().removeprefix("@").strip()
    if normalized.upper() in _USER_LABELS or normalized in _USER_LABELS:
        return "USER"
    if normalized.casefold() == "boss墨".casefold():
        return "boss墨"
    legacy = _NAMED_SPEAKERS.get(normalized)
    if legacy is not None:
        return legacy
    return normalized if explicit_at else None


def _dated_section(line: str) -> tuple[str, str] | None:
    match = _DATE_VALUE.match(line)
    if match is None:
        return None
    year = int(match.group("year")) if match.group("year") else 2000
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        date(year, month, day)
    except ValueError:
        return None
    label = line[: match.end()].rstrip(" \t:：")
    remainder = line[match.end() :].strip()
    return label, remainder


def _append_attributed_block(
    result: list[AttributedNoteBlock],
    *,
    speaker_kind: NoteSpeakerKind,
    speaker_label: str,
    body: str,
    section_date: str | None,
) -> None:
    if (
        result
        and result[-1].speaker_kind is speaker_kind
        and result[-1].speaker_label == speaker_label
        and result[-1].section_date == section_date
    ):
        previous = result[-1]
        result[-1] = AttributedNoteBlock(
            ordinal=previous.ordinal,
            speaker_kind=previous.speaker_kind,
            speaker_label=previous.speaker_label,
            body=f"{previous.body}\n\n{body}",
            section_date=previous.section_date,
        )
        return
    result.append(
        AttributedNoteBlock(
            ordinal=len(result),
            speaker_kind=speaker_kind,
            speaker_label=speaker_label,
            body=body,
            section_date=section_date,
        )
    )


def _split_inline_sections(line: str) -> tuple[str, ...]:
    matches = tuple(_INLINE_SECTION.finditer(line))
    starts = tuple(
        match.start("label")
        for match in matches
        if match.start("label") > 0
        and not line[match.start("label") :].startswith("@")
    )
    if not starts:
        return (line,)
    result: list[str] = []
    start = 0
    for section_start in starts:
        prefix = line[start:section_start].strip()
        if prefix:
            result.append(prefix)
        start = section_start
    tail = line[start:].strip()
    if tail:
        result.append(tail)
    return tuple(result)


def prefer_proven_complete_text(editor_body: str, list_text: str) -> str:
    """Preserve editor boundaries while adding a proven prefix and/or suffix."""

    compact_list = _compact(list_text)
    editor_lines = tuple(
        compact
        for line in editor_body.splitlines()
        if (compact := _compact(line))
    )
    if len(list_text) > len(editor_body) and editor_lines:
        extension = _proven_extension(list_text, compact_list, editor_lines)
        if extension is not None:
            prefix, suffix = extension
            return "\n\n".join(
                item for item in (prefix, editor_body.rstrip(), suffix) if item
            )
    return editor_body


def _proven_extension(
    list_text: str,
    compact_list: str,
    editor_lines: tuple[str, ...],
) -> tuple[str | None, str | None] | None:
    positions = tuple(index for index, char in enumerate(list_text) if not char.isspace())
    cursor = 0
    first_start: int | None = None
    for index, line in enumerate(editor_lines):
        start = compact_list.find(line, cursor)
        if start < 0:
            return None
        if index > 0 and any(
            not _separator(char) for char in compact_list[cursor:start]
        ):
            return None
        if first_start is None:
            first_start = start
        cursor = start + len(line)
    if first_start is None or cursor == 0:
        return None
    prefix = _clean_extension(list_text[: positions[first_start]])
    suffix = _clean_extension(list_text[positions[cursor - 1] + 1 :])
    if prefix is None and suffix is None:
        return None
    return prefix, suffix


def _clean_extension(value: str) -> str | None:
    cleaned = value.strip(" \t\r\n。；;，,、:：!?！？")
    if not cleaned:
        return None
    return _restore_date_boundaries(cleaned)


def _restore_date_boundaries(value: str) -> str:
    return re.sub(
        r"[\s。；;，,、!?！？]+"
        r"(?=(?:20[0-9]{2}[-/.])?(?:0[1-9]|1[0-2])[-/.]"
        r"(?:0[1-9]|[12][0-9]|3[01])(?:\s|[:：]))",
        "\n",
        value,
    )


def _separator(value: str) -> bool:
    return value.isspace() or unicodedata.category(value).startswith("P")


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


__all__ = [
    "NoteSectionOrder",
    "attributed_blocks",
    "detect_section_order",
    "prefer_proven_complete_text",
]
