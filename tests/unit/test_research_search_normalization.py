"""Unit tests for Phase 1C C3 FTS normalization / MATCH builders."""

from __future__ import annotations

import unicodedata

import pytest

from domain.common.errors import InputValidationError
from infrastructure.persistence.repositories._research_search_normalization import (
    build_fts_match_query,
    normalize_fts_text,
)


def test_normalize_inserts_spaces_between_adjacent_cjk() -> None:
    raw = "贵州茅台发布业绩预告"
    expected = "贵 州 茅 台 发 布 业 绩 预 告"
    assert normalize_fts_text(raw) == expected


def test_normalize_nfkc_compatibility_ideograph() -> None:
    # U+FA19 CJK COMPATIBILITY IDEOGRAPH-FA19 → unified under NFKC where applicable
    compat = "\ufa19"
    nfkc = unicodedata.normalize("NFKC", compat)
    assert normalize_fts_text(compat) == nfkc


def test_normalize_does_not_lowercase_or_strip_punctuation() -> None:
    raw = "NVIDIA Q2!! Growth"
    assert normalize_fts_text(raw) == raw


def test_normalize_mixed_cjk_latin_preserves_boundaries() -> None:
    raw = "茅台Moutai600519"
    assert normalize_fts_text(raw) == "茅 台Moutai600519"


def test_build_match_query_cjk_token_and() -> None:
    expr = build_fts_match_query("茅台")
    assert expr == '"茅" AND "台"'


def test_build_match_query_quotes_operators_as_literals() -> None:
    expr = build_fts_match_query('OR NOT NEAR * (evil) "x"')
    # tokens after normalize/whitespace split; internal quotes doubled
    assert '"OR"' in expr
    assert '"NOT"' in expr
    assert '"NEAR"' in expr
    assert '"*"' in expr
    assert '"(evil)"' in expr
    assert '"""x"""' in expr or '"x"' in expr
    assert " AND " in expr
    # Must not leave bare OR/NOT as operators
    assert " OR " not in expr
    assert " NOT " not in expr


def test_build_match_query_empty_after_normalize_raises() -> None:
    with pytest.raises(InputValidationError) as exc_info:
        build_fts_match_query("   \t\n  ")
    assert exc_info.value.code == "INPUT_VALIDATION_ERROR"
    assert "component" in exc_info.value.details


def test_build_match_query_english_phrase_and() -> None:
    expr = build_fts_match_query("structural demand")
    assert expr == '"structural" AND "demand"'


def test_normalize_extension_a_cjk() -> None:
    # U+3400 is CJK Extension A
    raw = "\u3400\u3401"
    assert normalize_fts_text(raw) == "\u3400 \u3401"
