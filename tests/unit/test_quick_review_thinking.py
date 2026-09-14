import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from application.services.quick_review_thinking import LatestThinkingReader
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind
from domain.external_note.models import AttributedNoteBlock

NOW = datetime(2026, 9, 15, tzinfo=UTC)
INSTRUMENT = "equity:US:SYNTHETIC"


def revision(version=1, **updates):
    return NS(
        **(
            dict(
                note_id="n1",
                note_revision_id=f"r{version}",
                version=version,
                title="Synthetic note",
                source_timestamp=NOW,
                observed_at=NOW,
                coverage=NoteCoverage.FULL,
                blocks=(
                    AttributedNoteBlock(
                        0, NoteSpeakerKind.USER, "USER", "Synthetic latest view", "2026-09-14"
                    ),
                ),
            )
            | updates
        )
    )


def view(kind="USER", label="USER", refs=None, summary="Synthetic summary"):
    return dict(
        speaker_kind=kind,
        speaker_label=label,
        summary=summary,
        source_block_ordinals=[0] if refs is None else refs,
    )


def interpretation(rev="r1", views=None, **updates):
    return NS(
        **(
            dict(
                note_revision_id=rev,
                status="SUCCEEDED",
                created_at=NOW,
                payload_json=json.dumps({"viewpoints": [view()] if views is None else views}),
            )
            | updates
        )
    )


def reader(revisions=None, analyses=None, identities=None):
    calls = []

    def lookup(identity):
        calls.append(identity)
        return (analyses or {}).get(identity)

    notes = NS(
        list_revisions_for_instrument=lambda *a, **k: (
            (revision(),) if revisions is None else revisions
        ),
        get=lambda identity: (identities or {}).get(
            identity, NS(source="MOOMOO_NOTE", primary_instrument_id=INSTRUMENT)
        ),
        interpretation_for_revision=lookup,
    )
    return LatestThinkingReader(notes, NS(now=lambda: NOW)), calls


def test_new_pending_revision_never_falls_back_to_old_success():
    r, calls = reader((revision(), revision(2)), {"r1": interpretation()})
    result = r.get(INSTRUMENT)[0]
    assert result["note_revision_id"] == "r2"
    assert result["status"] == "PENDING"
    assert result["user_summary"] == ""
    assert calls == ["r2"]


def test_only_latest_dated_user_blocks_bind_user_summary_other_speakers_separate():
    blocks = (
        AttributedNoteBlock(
            0, NoteSpeakerKind.USER, "USER", "Synthetic older thought", "2026-09-01"
        ),
        AttributedNoteBlock(
            1, NoteSpeakerKind.USER, "USER", "Synthetic newest thought", "2026-09-14"
        ),
        AttributedNoteBlock(
            2, NoteSpeakerKind.NAMED_PERSON, "Analyst", "Synthetic quoted opinion", "2026-09-14"
        ),
        AttributedNoteBlock(
            3, NoteSpeakerKind.USER, "USER", "Synthetic future thought", "2027-01-01"
        ),
    )
    views = [
        view(refs=[0], summary="Older summary"),
        view(refs=[1], summary="Newest summary"),
        view(refs=[2], summary="Misattributed quote"),
        view(refs=[3], summary="Future summary"),
        view("NAMED_PERSON", "Analyst", [2], "Quoted summary"),
    ]
    r, _ = reader((revision(blocks=blocks),), {"r1": interpretation(views=views)})
    result = r.get(INSTRUMENT)[0]
    assert result["status"] == "EXTRACTED"
    assert result["user_summary"] == "Newest summary"
    assert result["user_excerpt"] == "[2026-09-14 · block 1] Synthetic newest thought"
    assert result["other_viewpoints"] == [{"speaker": "Analyst", "summary": "Quoted summary"}]
    assert "FUTURE_USER_SECTION_EXCLUDED" in result["warnings"]


def test_summary_only_exposes_metadata_no_author_content_or_analysis_read():
    r, calls = reader((revision(coverage=NoteCoverage.SUMMARY_ONLY),), {"r1": interpretation()})
    result = r.get(INSTRUMENT)[0]
    assert result["status"] == "SUMMARY_ONLY"
    assert result["user_summary"] == result["user_excerpt"] == ""
    assert result["other_viewpoints"] == []
    assert calls == []


def test_exact_instrument_and_source_required():
    for identity in [
        NS(source="OTHER", primary_instrument_id=INSTRUMENT),
        NS(source="MOOMOO_NOTE", primary_instrument_id="equity:US:OTHER"),
    ]:
        r, calls = reader(identities={"n1": identity})
        assert r.get(INSTRUMENT) == []
        assert calls == []
    assert r.get(None) == []


def test_malformed_viewpoints_and_speaker_ref_mismatch_fail_closed():
    for views in [[None], [view(refs=[True])], [view(refs=[99])], [view(refs=[])], [view(kind=[])]]:
        r, _ = reader(analyses={"r1": interpretation(views=views)})
        result = r.get(INSTRUMENT)[0]
        assert result["status"] == "UNAVAILABLE"
        assert result["user_summary"] == ""
    for payload in ["{", "[]", '{"viewpoints":null}']:
        r, _ = reader(analyses={"r1": interpretation(payload_json=payload)})
        assert r.get(INSTRUMENT)[0]["status"] == "UNAVAILABLE"


def test_no_user_author_or_section_date_is_explicit():
    r, _ = reader(
        (
            revision(
                blocks=(
                    AttributedNoteBlock(
                        0, NoteSpeakerKind.NAMED_PERSON, "Other", "Synthetic quote"
                    ),
                )
            ),
        )
    )
    result = r.get(INSTRUMENT)[0]
    assert result["user_excerpt"] == ""
    assert "USER_AUTHOR_BLOCK_MISSING" in result["warnings"]
    r, _ = reader(
        (
            revision(
                blocks=(AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Synthetic undated"),)
            ),
        )
    )
    assert "USER_SECTION_DATE_MISSING" in r.get(INSTRUMENT)[0]["warnings"]


def test_failed_newest_and_future_interpretation_never_adopt_summary():
    for analysis in [
        interpretation(status="FAILED"),
        interpretation(created_at=NOW + timedelta(days=1)),
        interpretation(rev="wrong"),
    ]:
        r, _ = reader(analyses={"r1": analysis})
        assert r.get(INSTRUMENT)[0]["user_summary"] == ""


def test_note_count_and_excerpt_truncation_disclosed_source_sort_preserved():
    rows = tuple(
        revision(
            note_id=f"n{i}",
            note_revision_id=f"r{i}",
            source_timestamp=NOW - timedelta(days=i),
            blocks=(AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "x" * 4000),),
        )
        for i in range(7)
    )
    r, _ = reader(rows)
    results = r.get(INSTRUMENT)
    assert [item["note_id"] for item in results] == [f"n{i}" for i in range(5)]
    assert all("LATEST_NOTES_TRUNCATED_TO_FIVE" in item["warnings"] for item in results)
    assert len(results[0]["user_excerpt"]) == 3000
    assert "USER_EXCERPT_TRUNCATED" in results[0]["warnings"]


def test_yearless_dates_have_explicit_inference_and_future_sections_excluded():
    rows = (
        revision(
            blocks=(
                AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Synthetic recent", "09/14"),
                AttributedNoteBlock(1, NoteSpeakerKind.USER, "USER", "Synthetic future", "10/01"),
            )
        ),
    )
    r, _ = reader(rows)
    result = r.get(INSTRUMENT)[0]
    assert "recent" in result["user_excerpt"]
    assert "future" not in result["user_excerpt"]
    assert "SECTION_YEAR_INFERRED_FROM_READ_DATE" in result["warnings"]


def test_local_note_day_is_not_excluded_before_utc_midnight():
    from zoneinfo import ZoneInfo

    rows = (
        revision(
            observed_at=datetime(2026, 9, 14, 18, tzinfo=UTC),
            source_timestamp=datetime(2026, 9, 14, 18, tzinfo=UTC),
            blocks=(
                AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Local latest", "2026-09-15"),
            ),
        ),
    )
    r, _ = reader(rows)
    r._clock = NS(now=lambda: datetime(2026, 9, 14, 18, tzinfo=UTC))
    r._zone = ZoneInfo("Asia/Shanghai")
    assert "Local latest" in r.get(INSTRUMENT)[0]["user_excerpt"]


def test_thinking_date_and_change_compare_exact_previous_user_section():
    old = revision(
        blocks=(
            AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Demand stable", "2026-09-01"),
            AttributedNoteBlock(
                1, NoteSpeakerKind.NAMED_PERSON, "Analyst", "Buy now", "2026-09-14"
            ),
        )
    )
    latest = revision(
        2,
        blocks=(
            AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Demand weakening", "2026-09-14"),
        ),
        source_timestamp=NOW - timedelta(days=7),
    )
    r, _ = reader((latest,))
    r._notes.previous_revision = lambda *args: old
    result = r.get(INSTRUMENT)[0]
    assert result["thinking_date"] == "2026-09-14"
    assert result["thinking_date_basis"] == "EXPLICIT_SECTION"
    assert result["previous_revision_id"] == "r1"
    assert result["added_lines"] == ["Demand weakening"]
    assert result["removed_lines"] == ["Demand stable"]
    assert "Buy now" not in str(result["added_lines"]) + str(result["removed_lines"])
    assert result["user_summary"] == ""  # no model summary is invented


def test_undated_thinking_not_labelled_with_sync_date():
    r, _ = reader(
        (revision(blocks=(AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Undated"),)),)
    )
    result = r.get(INSTRUMENT)[0]
    assert result["thinking_date"] is None
    assert result["thinking_date_basis"] == "UNKNOWN"


def test_recent_thought_date_outweighs_old_note_edit_timestamp():
    rows = (
        revision(
            note_id="dated-new",
            source_timestamp=NOW - timedelta(days=10),
            blocks=(
                AttributedNoteBlock(
                    0, NoteSpeakerKind.USER, "USER", "Recent thinking", "2026-09-15"
                ),
            ),
        ),
        revision(
            note_id="edited-new",
            source_timestamp=NOW,
            blocks=(
                AttributedNoteBlock(0, NoteSpeakerKind.USER, "USER", "Old thinking", "2026-09-01"),
            ),
        ),
    )
    r, _ = reader(rows)
    results = r.get(INSTRUMENT)
    assert results[0]["note_id"] == "dated-new"
    assert results[0]["thinking_date"] == "2026-09-15"
