"""FTS text normalization and safe MATCH query builders (Phase 1C C3).

Index and query share the same pure functions so CJK unigram projections and
caller MATCH expressions stay coherent. Caller text is never spliced raw into
MATCH syntax.
"""

from __future__ import annotations

import re
import unicodedata

from domain.common.errors import InputValidationError

# CJK Unified / Extension A / Compatibility Ideographs + common supplementary
# plane unified extensions and Compatibility Ideographs Supplement.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2B73F),  # Extension C
    (0x2B740, 0x2B81F),  # Extension D
    (0x2B820, 0x2CEAF),  # Extension E
    (0x2CEB0, 0x2EBEF),  # Extension F
    (0x2F800, 0x2FA1F),  # Compatibility Ideographs Supplement
    (0x30000, 0x3134F),  # Extension G
)

_WHITESPACE_RE = re.compile(r"\s+", flags=re.UNICODE)


def _is_cjk_codepoint(cp: int) -> bool:
    return any(start <= cp <= end for start, end in _CJK_RANGES)


def normalize_fts_text(value: str) -> str:
    """NFKC-normalize and insert ASCII spaces between adjacent CJK codepoints.

    Does not lowercase, strip punctuation, or otherwise rewrite non-CJK text.
    Case folding for Latin letters is left to FTS5 ``unicode61``.
    """
    if not isinstance(value, str):
        msg = "normalize_fts_text requires a str"
        raise TypeError(msg)
    normalized = unicodedata.normalize("NFKC", value)
    if not normalized:
        return normalized
    chars = list(normalized)
    out: list[str] = [chars[0]]
    for index in range(1, len(chars)):
        prev_cp = ord(chars[index - 1])
        cur_cp = ord(chars[index])
        if _is_cjk_codepoint(prev_cp) and _is_cjk_codepoint(cur_cp):
            out.append(" ")
        out.append(chars[index])
    return "".join(out)


def build_fts_match_query(value: str) -> str:
    """Build a parameterized FTS5 MATCH expression from caller text.

    1. Apply :func:`normalize_fts_text`.
    2. Split on Unicode whitespace.
    3. Quote each token (internal ``"`` → ``""``).
    4. Join with `` AND ``.

    Operators such as ``OR`` / ``NOT`` / ``NEAR`` / ``*`` / parentheses are
    treated as quoted literal tokens and never alter MATCH syntax.
    """
    if not isinstance(value, str):
        msg = "build_fts_match_query requires a str"
        raise TypeError(msg)
    normalized = normalize_fts_text(value)
    tokens = [token for token in _WHITESPACE_RE.split(normalized) if token]
    if not tokens:
        raise InputValidationError(
            "FTS query produced no tokens after normalization",
            details={"component": "research_search"},
        )
    quoted: list[str] = []
    for token in tokens:
        escaped = token.replace('"', '""')
        quoted.append(f'"{escaped}"')
    return " AND ".join(quoted)
