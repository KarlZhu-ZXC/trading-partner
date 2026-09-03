from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine

from application.ports.agent_model_provider import ModelResponse, ModelToolCall
from application.ports.external_note_provider import (
    ExternalNoteScanResult,
    ObservationSourceCapability,
)
from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from application.services.external_note_sync_service import (
    ExternalNoteSyncService,
    ExternalObservationCaptureRequest,
)
from domain.common.errors import DataContractError
from domain.external_note.attribution import (
    attributed_blocks,
    detect_section_order,
    prefer_proven_complete_text,
)
from domain.external_note.enums import NoteCoverage, NoteSpeakerKind, NoteSyncStatus
from domain.external_note.models import (
    AttributedNoteBlock,
    ExternalNoteInterpretation,
    ExternalNoteRevision,
)
from infrastructure.persistence.external_note_repository import (
    SqlAlchemyExternalNoteRepository,
)
from infrastructure.persistence.observation_capture_store import (
    OwnerOnlyObservationCaptureStore,
)
from infrastructure.providers.local_observations import LocalObservationInboxProvider
from infrastructure.providers.moomoo_notes import (
    MoomooNotesCacheProvider,
    MoomooNotesRemoteClient,
    owner_only_cookie_file_configured,
    write_owner_only_cookie_file,
)

NOW = datetime(2026, 8, 27, 12, tzinfo=UTC)


def _stock_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE t_stock(stock_id INTEGER PRIMARY KEY, code TEXT, "
            "exchange TEXT, instrument_type INTEGER)"
        )
        connection.execute("INSERT INTO t_stock VALUES(79894981859506, 'AFRM', 'US', 3)")
        connection.commit()


def _cache(
    cache: Path,
    *,
    include_editor: bool = True,
    user_text: str = "目前在70-80区间震荡",
    include_date: bool = True,
) -> None:
    listed = {
        "code": 0,
        "message": "成功",
        "data": {
            "feed": [
                {
                    "feedId": "117156244357125",
                    "feedTitle": "AFRM",
                    "summaryDesc": f"{user_text}。宝总：突破82还是看100",
                    "timestamp": 1787662506,
                    "viewPermission": {"permissionType": 2, "userIds": []},
                    "allRelatedStockInfos": [{"stockId": "79894981859506"}],
                }
            ]
        },
    }
    (cache / "note-list_0").write_bytes(
        b"https://www.moomoo.com/community/v2/api/note/list?redacted=1"
        + json.dumps(listed, ensure_ascii=False).encode()
    )
    if not include_editor:
        return
    state = {
        "editor": {
            "params": {
                "fid": "117156244357125",
                "feedData": {
                    "feed_title": "AFRM",
                    "all_related_stock_infos": [{"stock_id": "79894981859506"}],
                    "module_items": [
                        {
                            "rich_text": {
                                "content": (
                                    f"<p>08/25</p><p>{user_text}</p>"
                                    if include_date
                                    else f"<p>{user_text}</p>"
                                )
                            }
                        },
                        {"rich_text": {"content": "<p>宝总：突破82还是看100</p>"}},
                    ],
                },
            }
        }
    }
    (cache / "editor_0").write_text(
        "https://www.moomoo.com/hans/community/sns-editor\n"
        "window.__INITIAL_STATE__ = " + json.dumps(state, ensure_ascii=False),
        encoding="utf-8",
    )


def _remote_editor_html(body: str = "远程完整正文") -> bytes:
    state = {
        "editor": {
            "params": {
                "fid": "117156244357125",
                "feedData": {
                    "feed_title": "AFRM",
                    "all_related_stock_infos": [{"stock_id": "79894981859506"}],
                    "module_items": [
                        {"rich_text": {"content": f"<p>{body}</p>"}},
                        {"rich_text": {"content": "<p>宝总：等待确认</p>"}},
                    ],
                },
            }
        }
    }
    return (
        "<!doctype html><script>window.__INITIAL_STATE__ = "
        + json.dumps(state, ensure_ascii=False)
        + "</script>"
    ).encode()


def _remote_list_payload(summary: str = "远程摘要") -> dict[str, object]:
    return {
        "code": 0,
        "message": "success",
        "data": {
            "feed": [
                {
                    "feedId": "117156244357125",
                    "feedTitle": "AFRM",
                    "summaryDesc": summary,
                    "timestamp": 1787662506,
                    "viewPermission": {"permissionType": 2},
                    "allRelatedStockInfos": [{"stockId": "79894981859506"}],
                }
            ]
        },
    }


def test_moomoo_notes_cache_recovers_full_note_and_deterministic_speakers(
    tmp_path: Path, fixed_clock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    provider = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
    )
    fixed_clock.set(NOW)

    result = provider.scan()

    assert result.cache_files_scanned == 2
    assert len(result.snapshots) == 1
    note = result.snapshots[0]
    assert note.coverage is NoteCoverage.FULL
    assert note.primary_instrument_id == "equity:US:AFRM"
    assert note.related_provider_codes == ("US.AFRM",)
    assert [(item.speaker_kind, item.speaker_label) for item in note.blocks] == [
        (NoteSpeakerKind.USER, "USER"),
        (NoteSpeakerKind.NAMED_PERSON, "宝总"),
    ]
    assert [item.section_date for item in note.blocks] == ["08/25", "08/25"]


def test_moomoo_notes_cache_keeps_summary_only_explicit(tmp_path: Path, fixed_clock) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_editor=False)
    provider = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
    )

    note = provider.scan().snapshots[0]

    assert note.coverage is NoteCoverage.SUMMARY_ONLY
    assert note.full_body is None


def test_named_viewpoint_inherits_following_lines_until_next_date_section() -> None:
    body = """08/25
财报前走势下跌严重，等待财报后再行动。
宝总：财报看跌到190~196附近但需注意几个位置：
①190~210区间，回落区间底部插针可等信号接多
②190为流动性位置，个人认为比较容易到
③181~185为FVG+月支撑+月上涨段OTE
整体观点：目前是震荡+下跌趋势，按区间处理
08/27
财报拉涨，但没有突破区间高位。"""

    blocks = attributed_blocks(body)

    assert [(item.speaker_label, item.body) for item in blocks] == [
        ("USER", "财报前走势下跌严重，等待财报后再行动。"),
        (
            "宝总",
            "财报看跌到190~196附近但需注意几个位置：\n\n"
            "①190~210区间，回落区间底部插针可等信号接多\n\n"
            "②190为流动性位置，个人认为比较容易到\n\n"
            "③181~185为FVG+月支撑+月上涨段OTE\n\n"
            "整体观点：目前是震荡+下跌趋势，按区间处理",
        ),
        ("USER", "财报拉涨，但没有突破区间高位。"),
    ]
    assert [item.section_date for item in blocks] == ["08/25", "08/25", "08/27"]


def test_explicit_user_label_resets_named_viewpoint_inside_date_section() -> None:
    blocks = attributed_blocks("08/25\n宝总：外部观点\n后续补充\n我：这是我的更正\n继续观察")

    assert [item.speaker_label for item in blocks] == ["宝总", "USER"]
    assert [item.body for item in blocks] == [
        "外部观点\n\n后续补充",
        "这是我的更正\n\n继续观察",
    ]
    assert all(item.section_date == "08/25" for item in blocks)


def test_legacy_speaker_vocabulary_and_at_markers_reject_heading_false_positives() -> None:
    blocks = attributed_blocks(
        "08/28\n"
        "财报看法：利润超预期\n"
        "正常用户补充\n"
        "@宝总：等待突破\n"
        "风险：跌破支撑\n"
        "@姜汁汽水\n"
        "观察成交量\n"
        "@boss墨: 保持纪律\n"
        "句子中提到@宝总但不切换\n"
        "@USER\n"
        "我的结论"
    )

    assert [item.speaker_label for item in blocks] == [
        "USER",
        "宝总",
        "姜汁汽水",
        "boss墨",
        "USER",
    ]
    assert blocks[0].body == "财报看法：利润超预期\n\n正常用户补充"
    assert blocks[1].body == "等待突破\n\n风险：跌破支撑"
    assert blocks[2].body == "观察成交量"
    assert blocks[3].body == "保持纪律\n\n句子中提到@宝总但不切换"
    assert blocks[4].body == "我的结论"


def test_line_leading_at_marker_allows_new_speaker_but_midline_mention_does_not() -> None:
    blocks = attributed_blocks(
        "08/28\n"
        "新朋友：没有@时只是标题\n"
        "@新朋友：这是新speaker\n"
        "后续继承\n"
        "句中提到 @另一个人：但不切换\n"
        "@另一个人\n"
        "现在才切换"
    )

    assert [item.speaker_label for item in blocks] == [
        "USER",
        "新朋友",
        "另一个人",
    ]
    assert blocks[0].body == "新朋友：没有@时只是标题"
    assert blocks[1].body == (
        "这是新speaker\n\n后续继承\n\n句中提到 @另一个人：但不切换"
    )
    assert blocks[2].body == "现在才切换"


@pytest.mark.parametrize("label", ["boss墨", "宝总", "姜汁汽水"])
def test_legacy_allowed_speaker_prefixes_remain_readable(label: str) -> None:
    blocks = attributed_blocks(f"08/28\n{label}：旧格式观点\n后续段落")

    assert len(blocks) == 1
    assert blocks[0].speaker_label == label
    assert blocks[0].body == "旧格式观点\n\n后续段落"


@pytest.mark.parametrize("label", ["boss墨", "宝总", "姜汁汽水"])
def test_legacy_allowed_speaker_marker_only_line_remains_readable(label: str) -> None:
    blocks = attributed_blocks(f"08/28\n用户观点\n{label}：\n财报看法：外部观点\n后续段落")

    assert [item.speaker_label for item in blocks] == ["USER", label]
    assert blocks[1].body == "财报看法：外部观点\n\n后续段落"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("08/31\n新观点\n08/25\n旧观点", "NEWEST_TO_OLDEST"),
        ("08/25\n旧观点\n08/31\n新观点", "OLDEST_TO_NEWEST"),
        ("08/31\n08/25\n08/27", "MIXED"),
        ("01/02\n12/31\n12/20", "NEWEST_TO_OLDEST"),
        ("12/20\n12/31\n01/02", "OLDEST_TO_NEWEST"),
        ("2025-12-31\n2026-01-02", "OLDEST_TO_NEWEST"),
        ("只有一段没有足够日期", "UNKNOWN"),
    ],
)
def test_detect_section_order_per_note(body: str, expected: str) -> None:
    assert detect_section_order(body) == expected


def test_proven_newest_first_prefix_preserves_prior_editor_boundaries() -> None:
    editor = "08/28\n旧用户观点\n宝总：旧外部观点\n后续仍属宝总"
    listed = "08/31 新增用户观点；08/28 旧用户观点；宝总：旧外部观点；后续仍属宝总"

    complete = prefer_proven_complete_text(editor, listed)
    blocks = attributed_blocks(complete)

    assert complete == f"08/31 新增用户观点\n\n{editor}"
    assert [(item.speaker_label, item.body) for item in blocks] == [
        ("USER", "新增用户观点"),
        ("USER", "旧用户观点"),
        ("宝总", "旧外部观点\n\n后续仍属宝总"),
    ]
    assert [item.section_date for item in blocks] == ["08/31", "08/28", "08/28"]


def test_unproven_middle_insertion_fails_closed_to_prior_editor_body() -> None:
    editor = "08/28\n旧观点一\n旧观点二"
    listed = "08/28 旧观点一；中间改写；旧观点二"

    assert prefer_proven_complete_text(editor, listed) == editor


def test_moomoo_notes_remote_enriches_full_body_with_randomized_bounded_delays(
    tmp_path: Path, fixed_clock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_editor=False)
    cookie_file = tmp_path / "cookie.txt"
    write_owner_only_cookie_file(cookie_file, "session=private-test-value")
    requests: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["cookie"] == "session=private-test-value"
        if request.url.path.endswith("/note/list"):
            return httpx.Response(200, json=_remote_list_payload())
        return httpx.Response(200, content=_remote_editor_html())

    remote = MoomooNotesRemoteClient(
        cookie_file=cookie_file,
        delay_min_seconds=0.5,
        delay_max_seconds=1.5,
        timeout_seconds=3,
        max_stock_ids=1,
        max_notes=1,
        transport=httpx.MockTransport(handler),
        sleeper=delays.append,
        random_uniform=lambda minimum, maximum: (minimum + maximum) / 2,
    )
    provider = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
        remote_client=remote,
    )

    result = provider.scan()

    assert result.warning_codes == ()
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/community/sns-editor")
    assert requests[0].url.params["feed_id"] == "117156244357125"
    assert delays == [1.0]
    assert result.snapshots[0].coverage is NoteCoverage.FULL
    assert result.snapshots[0].full_body == "远程完整正文\n\n宝总：等待确认"


def test_moomoo_notes_remote_recovers_list_when_only_cached_stock_key_survives(
    tmp_path: Path, fixed_clock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "empty-list_0").write_bytes(
        b"https://www.moomoo.com/community/v2/api/note/list?stockId=79894981859506"
        b'{"code":0,"data":{"feed":[]}}'
    )
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    cookie_file = tmp_path / "cookie.txt"
    write_owner_only_cookie_file(cookie_file, "session=private-test-value")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/note/list"):
            return httpx.Response(200, json=_remote_list_payload())
        return httpx.Response(200, content=_remote_editor_html())

    remote = MoomooNotesRemoteClient(
        cookie_file=cookie_file,
        delay_min_seconds=0,
        delay_max_seconds=0,
        timeout_seconds=3,
        max_stock_ids=1,
        max_notes=1,
        transport=httpx.MockTransport(handler),
        sleeper=lambda _delay: None,
    )

    result = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
        remote_client=remote,
    ).scan()

    assert [request.url.path for request in requests] == [
        "/community/v2/api/note/list",
        "/hans/community/sns-editor",
    ]
    assert result.snapshots[0].coverage is NoteCoverage.FULL


def test_moomoo_notes_remote_missing_cookie_falls_back_without_a_request(
    tmp_path: Path, fixed_clock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_editor=False)
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    remote = MoomooNotesRemoteClient(
        cookie_file=tmp_path / "missing-cookie.txt",
        delay_min_seconds=0,
        delay_max_seconds=0,
        timeout_seconds=3,
        transport=httpx.MockTransport(handler),
    )
    result = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
        remote_client=remote,
    ).scan()

    assert called is False
    assert result.snapshots[0].coverage is NoteCoverage.SUMMARY_ONLY
    assert result.warning_codes == ("MOOMOO_NOTES_REMOTE_COOKIE_UNAVAILABLE",)


def test_moomoo_notes_remote_auth_failure_keeps_cached_summary(
    tmp_path: Path, fixed_clock
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_editor=False)
    cookie_file = tmp_path / "cookie.txt"
    write_owner_only_cookie_file(cookie_file, "session=expired")
    remote = MoomooNotesRemoteClient(
        cookie_file=cookie_file,
        delay_min_seconds=0,
        delay_max_seconds=0,
        timeout_seconds=3,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"location": "/login"})
        ),
    )

    result = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
        remote_client=remote,
    ).scan()

    assert result.snapshots[0].coverage is NoteCoverage.SUMMARY_ONLY
    assert result.warning_codes == ("MOOMOO_NOTES_REMOTE_AUTH_REQUIRED",)


def test_moomoo_note_cookie_file_requires_owner_only_mode(tmp_path: Path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    write_owner_only_cookie_file(cookie_file, "session=valid")
    assert cookie_file.stat().st_mode & 0o777 == 0o600
    assert owner_only_cookie_file_configured(cookie_file) is True

    cookie_file.chmod(0o644)

    assert owner_only_cookie_file_configured(cookie_file) is False


def test_local_observation_bridge_accepts_full_text_from_a_future_adapter(
    tmp_path: Path, fixed_clock
) -> None:
    inbox = tmp_path / "observations"
    inbox.mkdir()
    (inbox / "tradingview-afrm.json").write_text(
        json.dumps(
            {
                "source_code": "TRADINGVIEW_NOTE",
                "external_id": "layout-afrm",
                "title": "AFRM Layout Note",
                "full_body": "区间内继续观察\nBoss墨：突破后再确认",
                "observed_at": NOW.isoformat(),
                "primary_instrument_id": "equity:US:AFRM",
                "related_provider_codes": ["NASDAQ:AFRM"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (inbox / "invalid.json").write_text("not-json", encoding="utf-8")
    provider = LocalObservationInboxProvider(inbox, fixed_clock)

    result = provider.scan()

    assert len(result.snapshots) == 1
    assert result.cache_files_scanned == 2
    assert result.warning_codes == ("LOCAL_OBSERVATION_FILE_INVALID",)
    note = result.snapshots[0]
    assert note.source == "TRADINGVIEW_NOTE"
    assert note.coverage is NoteCoverage.FULL
    assert [(item.speaker_kind, item.speaker_label) for item in note.blocks] == [
        (NoteSpeakerKind.USER, "USER"),
        (NoteSpeakerKind.NAMED_PERSON, "boss墨"),
    ]


@pytest.mark.asyncio
async def test_capture_api_spool_enters_the_same_provider_neutral_revision_chain(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    inbox = tmp_path / "observations"
    service = ExternalNoteSyncService(
        LocalObservationInboxProvider(inbox, fixed_clock),
        repository,
        fixed_clock,
        id_generator,
        capture_store=OwnerOnlyObservationCaptureStore(inbox),
    )
    fixed_clock.set(NOW)

    request = ExternalObservationCaptureRequest(
        source_code="TRADINGVIEW_NOTE",
        external_id="layout-afrm",
        title="AFRM Layout Note",
        full_body="区间内观察",
        observed_at=NOW,
        primary_instrument_id="equity:US:AFRM",
        related_provider_codes=("NASDAQ:AFRM",),
    )
    receipt = await service.capture(request)
    repeated = await service.capture(request)
    corrected = await service.capture(
        replace(
            request,
            full_body="更正：跌破69后原判断失效\nBoss墨：等待重新站稳",
            observed_at=NOW.replace(minute=1),
        )
    )
    reverted = await service.capture(
        replace(request, observed_at=NOW.replace(minute=2))
    )
    replayed = await service.sync(source_code="LOCAL_OBSERVATION_BRIDGE")
    stale = await service.capture(
        replace(
            request,
            full_body="过时观察",
            observed_at=NOW.replace(minute=0, second=0, microsecond=0),
            source_timestamp=NOW.replace(minute=0, second=0, microsecond=0),
        )
    )

    assert receipt.revisions_created == 1
    assert repeated.revisions_created == 0
    assert corrected.revisions_created == 1
    assert reverted.revisions_created == 1
    assert replayed.revisions_created == 0
    assert stale.revisions_created == 0
    assert "OBSERVATION_OUT_OF_ORDER_IGNORED" in stale.warning_codes
    assert len(tuple(inbox.glob("*.json"))) == 4
    captured = next(inbox.glob("*.json"))
    assert captured.stat().st_mode & 0o777 == 0o600
    item = service.inbox()[0]
    history = service.history(item.identity.note_id)
    assert item.identity.source == "TRADINGVIEW_NOTE"
    assert item.revision.coverage is NoteCoverage.FULL
    assert item.revision.version == 3
    assert item.revision.full_body == request.full_body
    assert [entry.revision.version for entry in history] == [3, 2, 1]
    corrected_revision = repository.previous_revision(item.identity.note_id, 3)
    assert corrected_revision is not None
    assert [(block.speaker_kind, block.speaker_label) for block in corrected_revision.blocks] == [
        (NoteSpeakerKind.USER, "USER"),
        (NoteSpeakerKind.NAMED_PERSON, "boss墨"),
    ]
    engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_capture_of_one_source_revision_is_idempotent(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    inbox = tmp_path / "observations"
    service = ExternalNoteSyncService(
        LocalObservationInboxProvider(inbox, fixed_clock),
        repository,
        fixed_clock,
        id_generator,
        capture_store=OwnerOnlyObservationCaptureStore(inbox),
    )
    request = ExternalObservationCaptureRequest(
        source_code="TRADINGVIEW_NOTE",
        external_id="layout-afrm-concurrent",
        title="AFRM Concurrent Note",
        full_body="Observe the range.",
        observed_at=NOW,
        primary_instrument_id="equity:US:AFRM",
    )

    receipts = await asyncio.gather(*(service.capture(request) for _ in range(8)))

    assert sum(item.revisions_created for item in receipts) == 1
    assert len(tuple(inbox.glob("*.json"))) == 1
    assert len(service.inbox()) == 1
    engine.dispose()


@pytest.mark.asyncio
async def test_cross_process_sync_contention_fails_with_typed_retryable_error(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    inbox = tmp_path / "observations"

    class BusyLock:
        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            raise AssertionError("unacquired lock must not be released")

    service = ExternalNoteSyncService(
        LocalObservationInboxProvider(inbox, fixed_clock),
        repository,
        fixed_clock,
        id_generator,
        process_lock=BusyLock(),
        process_lock_wait_seconds=0,
    )

    with pytest.raises(DataContractError) as captured:
        await service.sync()

    assert captured.value.code == "OBSERVATION_SYNC_BUSY"
    assert captured.value.retryable is True
    engine.dispose()


@pytest.mark.asyncio
async def test_note_interpretation_uses_opencode_max_and_preserves_attribution(
    fixed_clock, id_generator
) -> None:
    captured = None

    class Provider:
        model = "deepseek-v4-flash-vision-exp"

        async def complete(self, request):  # type: ignore[no-untyped-def]
            nonlocal captured
            captured = request
            scenarios = [
                {
                    "scenario": scenario,
                    "action": "REVIEW" if scenario != "SIDEWAYS" else "NO_ACTION",
                    "condition": "等待条件",
                    "confirmation": "需要结构确认",
                    "loss_boundary": "缺少损失边界",
                }
                for scenario in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
            ]
            value = {
                "change_relation": "NEW_THREAD",
                "material_change_summary": "首次解析。",
                "viewpoints": [
                    {
                        "speaker_kind": "USER",
                        "speaker_label": "USER",
                        "source_block_ordinals": [0],
                        "summary": "用户观察横盘。",
                        "holding_horizon": "UNKNOWN",
                        "direction": "SIDEWAYS",
                        "structure": "70至80区间。",
                    },
                    {
                        "speaker_kind": "NAMED_PERSON",
                        "speaker_label": "宝总",
                        "source_block_ordinals": [1],
                        "summary": "宝总观察突破。",
                        "holding_horizon": "UNKNOWN",
                        "direction": "UP",
                        "structure": "突破82后观察100。",
                    },
                ],
                "user_scenarios": scenarios,
                "catalysts": [],
                "key_levels": ["70", "80", "82", "100"],
                "missing_evidence": ["holding_horizon", "loss_boundary"],
                "contradictions": [],
                "suggested_next_step": "REVIEW",
            }
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="call_note",
                        name="submit_note_interpretation",
                        arguments=json.dumps(value, ensure_ascii=False),
                    ),
                )
            )

        async def aclose(self) -> None:
            return None

    revision = ExternalNoteRevision(
        note_revision_id="external_note_revision_test",
        note_id="external_note_test",
        version=1,
        content_sha256="a" * 64,
        source_revision_key="source:test-a",
        title="AFRM",
        summary="用户和宝总观点",
        full_body="用户观察\n宝总：外部观点",
        coverage=NoteCoverage.FULL,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="SELF",
        related_provider_stock_ids=("79894981859506",),
        related_provider_codes=("US.AFRM",),
        blocks=(
            AttributedNoteBlock(
                ordinal=0,
                speaker_kind=NoteSpeakerKind.USER,
                speaker_label="USER",
                body="用户观察",
            ),
            AttributedNoteBlock(
                ordinal=1,
                speaker_kind=NoteSpeakerKind.NAMED_PERSON,
                speaker_label="宝总",
                body="外部观点",
            ),
        ),
    )
    service = ExternalNoteInterpretationService(
        Provider(),  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="deepseek-v4-flash-vision-exp",
        clock=fixed_clock,
        id_generator=id_generator,
    )
    fixed_clock.set(NOW)

    result = await service.analyze(revision, None)

    assert result.status == "SUCCEEDED"
    assert result.reasoning_effort == "max"
    assert captured is not None
    assert captured.reasoning_effort == "max"
    assert captured.native_web_search is False


@pytest.mark.asyncio
async def test_note_interpretation_accepts_the_models_closed_interpretation_wrapper(
    fixed_clock, id_generator
) -> None:
    requests = []

    class Provider:
        async def complete(self, request):  # type: ignore[no-untyped-def]
            requests.append(request)
            if len(requests) == 1:
                return ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            id="call_invalid",
                            name="submit_note_interpretation",
                            arguments=json.dumps({"interpretation": {}}),
                        ),
                    )
                )
            scenarios = [
                {
                    "scenario": scenario,
                    "action": "NO_ACTION",
                    "condition": "等待",
                    "confirmation": "等待确认",
                    "loss_boundary": "尚未定义",
                }
                for scenario in ("UPSIDE", "SIDEWAYS", "PULLBACK", "INVALIDATION")
            ]
            payload = {
                "change_relation": "NEW_THREAD",
                "material_change_summary": "首次解析。",
                "viewpoints": [
                    {
                        "speaker_kind": "USER",
                        "speaker_label": "USER",
                        "source_block_ordinals": [0],
                        "summary": "用户观察横盘。",
                        "holding_horizon": "UNKNOWN",
                        "direction": "SIDEWAYS",
                        "structure": "区间。",
                    }
                ],
                "user_scenarios": scenarios,
                "catalysts": [],
                "key_levels": [],
                "missing_evidence": [],
                "contradictions": [],
                "suggested_next_step": "NO_ACTION",
            }
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="call_wrapped",
                        name="submit_note_interpretation",
                        arguments=json.dumps({"interpretation": payload}, ensure_ascii=False),
                    ),
                )
            )

    revision = ExternalNoteRevision(
        note_revision_id="external_note_revision_wrapped",
        note_id="external_note_wrapped",
        version=1,
        content_sha256="b" * 64,
        source_revision_key="source:test-b",
        title="AFRM",
        summary="横盘",
        full_body="横盘",
        coverage=NoteCoverage.FULL,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="SELF",
        related_provider_stock_ids=(),
        related_provider_codes=("US.AFRM",),
        blocks=(
            AttributedNoteBlock(
                ordinal=0,
                speaker_kind=NoteSpeakerKind.USER,
                speaker_label="USER",
                body="横盘",
            ),
        ),
    )
    service = ExternalNoteInterpretationService(
        Provider(),  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="deepseek-v4-flash-vision-exp",
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.analyze(revision, None)

    assert result.status == "SUCCEEDED"
    assert len(requests) == 2
    assert [message.role for message in requests[1].messages if hasattr(message, "role")][-2:] == [
        "assistant",
        "tool",
    ]


@pytest.mark.asyncio
async def test_note_interpretation_reports_missing_tool_call_without_raw_output(
    fixed_clock, id_generator
) -> None:
    calls = 0

    class Provider:
        async def complete(self, _request):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            return ModelResponse(text="ordinary text must not be persisted")

    revision = ExternalNoteRevision(
        note_revision_id="external_note_revision_missing_tool",
        note_id="external_note_missing_tool",
        version=1,
        content_sha256="e" * 64,
        source_revision_key="source:missing-tool",
        title="NVDA",
        summary="观察",
        full_body="观察",
        coverage=NoteCoverage.FULL,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="SELF",
        related_provider_stock_ids=(),
        related_provider_codes=("US.NVDA",),
        blocks=(
            AttributedNoteBlock(
                ordinal=0,
                speaker_kind=NoteSpeakerKind.USER,
                speaker_label="USER",
                body="观察",
            ),
        ),
    )
    service = ExternalNoteInterpretationService(
        Provider(),  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="deepseek-v4-flash-vision-exp",
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.analyze(revision, None)

    assert calls == 2
    assert result.status == "FAILED"
    assert result.error_code == "NOTE_INTERPRETATION_TOOL_CALL_MISSING"
    assert result.payload_json == "{}"


@pytest.mark.asyncio
async def test_note_interpretation_schema_error_names_only_closed_field_path(
    fixed_clock, id_generator
) -> None:
    class Provider:
        async def complete(self, _request):  # type: ignore[no-untyped-def]
            return ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        id="call_invalid_schema",
                        name="submit_note_interpretation",
                        arguments="{}",
                    ),
                )
            )

    revision = ExternalNoteRevision(
        note_revision_id="external_note_revision_schema_invalid",
        note_id="external_note_schema_invalid",
        version=1,
        content_sha256="f" * 64,
        source_revision_key="source:schema-invalid",
        title="WTIOIL",
        summary="观察",
        full_body="观察",
        coverage=NoteCoverage.FULL,
        source_timestamp=NOW,
        observed_at=NOW,
        visibility="SELF",
        related_provider_stock_ids=(),
        related_provider_codes=(),
        blocks=(
            AttributedNoteBlock(
                ordinal=0,
                speaker_kind=NoteSpeakerKind.USER,
                speaker_label="USER",
                body="观察",
            ),
        ),
    )
    service = ExternalNoteInterpretationService(
        Provider(),  # type: ignore[arg-type]
        provider_name="opencode_go",
        model="deepseek-v4-flash-vision-exp",
        clock=fixed_clock,
        id_generator=id_generator,
    )

    result = await service.analyze(revision, None)

    assert result.status == "FAILED"
    assert result.error_code == "NOTE_INTERPRETATION_SCHEMA_MISSING_CHANGE_RELATION"


@pytest.mark.asyncio
async def test_note_sync_is_idempotent(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    provider = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
    )
    service = ExternalNoteSyncService(provider, repository, fixed_clock, id_generator)
    fixed_clock.set(NOW)

    first = await service.sync(analyze=False)
    second = await service.sync(analyze=False)

    assert first.identities_created == 1
    assert first.revisions_created == 1
    assert second.identities_created == 0
    assert second.revisions_created == 0
    assert second.unchanged_count == 1
    inbox = service.inbox()
    assert len(inbox) == 1
    assert inbox[0].identity.primary_instrument_id == "equity:US:AFRM"
    engine.dispose()


@pytest.mark.asyncio
async def test_observation_sync_aggregates_provider_neutral_sources(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    moomoo = MoomooNotesCacheProvider(
        cache_data_dir=cache,
        stock_database_path=stock_db,
        clock=fixed_clock,
    )

    class TradingViewFixture:
        capability = ObservationSourceCapability(
            source_code="TRADINGVIEW_NOTE",
            display_name="TradingView Notes",
            supports_full_text=True,
            supports_incremental_sync=True,
            requires_interactive_session=False,
            content_modes=("ADAPTER_FULL_TEXT",),
        )

        def scan(self) -> ExternalNoteScanResult:
            value = moomoo.scan()
            snapshot = replace(
                value.snapshots[0],
                source="TRADINGVIEW_NOTE",
                external_id="tradingview-layout-afrm",
                title="AFRM TradingView Layout Note",
            )
            return ExternalNoteScanResult(
                snapshots=(snapshot,),
                cache_files_scanned=1,
            )

    class BrokenFixture:
        capability = ObservationSourceCapability(
            source_code="BROKEN_NOTE_SOURCE",
            display_name="Broken Notes",
            supports_full_text=True,
            supports_incremental_sync=True,
            requires_interactive_session=False,
            content_modes=("TEST",),
        )

        def scan(self) -> ExternalNoteScanResult:
            raise OSError("fixture unavailable")

    service = ExternalNoteSyncService(
        (moomoo, TradingViewFixture(), BrokenFixture()),  # type: ignore[arg-type]
        repository,
        fixed_clock,
        id_generator,
    )
    fixed_clock.set(NOW)

    receipt = await service.sync(analyze=False)

    assert receipt.identities_created == 2
    assert receipt.status is NoteSyncStatus.PARTIAL
    assert receipt.error_codes == ("OBSERVATION_SOURCE_UNAVAILABLE_BROKEN_NOTE_SOURCE",)
    assert {item.source_code for item in service.source_capabilities()} == {
        "MOOMOO_NOTE",
        "TRADINGVIEW_NOTE",
        "BROKEN_NOTE_SOURCE",
    }
    assert {item.identity.source for item in service.inbox()} == {
        "MOOMOO_NOTE",
        "TRADINGVIEW_NOTE",
    }
    engine.dispose()


@pytest.mark.asyncio
async def test_note_sync_does_not_create_a_revision_when_full_cache_is_evicted(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
    )
    fixed_clock.set(NOW)
    first = await service.sync(analyze=False)
    (cache / "editor_0").unlink()

    second = await service.sync(analyze=False)

    assert first.revisions_created == 1
    assert second.revisions_created == 0
    assert second.unchanged_count == 1
    assert service.inbox()[0].revision.coverage is NoteCoverage.FULL
    engine.dispose()


@pytest.mark.asyncio
async def test_inbox_recovers_prior_full_revision_from_a_legacy_false_downgrade(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
    )
    fixed_clock.set(NOW)
    await service.sync(analyze=False)
    full = service.inbox()[0].revision
    repository.append_revision(
        replace(
            full,
            note_revision_id="external_note_revision_legacy_downgrade",
            version=2,
            content_sha256="c" * 64,
            source_revision_key="source:legacy-downgrade",
            full_body=None,
            coverage=NoteCoverage.SUMMARY_ONLY,
        )
    )

    effective = service.inbox()[0].revision

    assert effective.note_revision_id == full.note_revision_id
    assert effective.coverage is NoteCoverage.FULL
    engine.dispose()


@pytest.mark.asyncio
async def test_inbox_promotes_list_text_only_when_prior_editor_proves_it_is_a_superset(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_date=False)
    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
    )
    fixed_clock.set(NOW)
    await service.sync(analyze=False)
    full = service.inbox()[0].revision
    legacy_editor_body = "目前在70-80区间震荡\n宝总：突破82还是看100"
    proven_summary = full.summary + "；08/27 补充观点"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE external_note_revisions SET full_body=?, summary=?, blocks_json=? "
            "WHERE note_revision_id=?",
            (
                legacy_editor_body,
                proven_summary,
                json.dumps(
                    [
                        {
                            "ordinal": 0,
                            "speaker_kind": "USER",
                            "speaker_label": "USER",
                            "body": "目前在70-80区间震荡",
                        },
                        {
                            "ordinal": 1,
                            "speaker_kind": "NAMED_PERSON",
                            "speaker_label": "宝总",
                            "body": "突破82还是看100",
                        },
                    ],
                    ensure_ascii=False,
                ),
                full.note_revision_id,
            ),
        )
    repository.append_revision(
        replace(
            full,
            note_revision_id="external_note_revision_proven_superset",
            version=2,
            content_sha256="d" * 64,
            source_revision_key="source:proven-superset",
            summary=proven_summary,
            full_body=None,
            coverage=NoteCoverage.SUMMARY_ONLY,
        )
    )

    effective = service.inbox()[0].revision

    assert effective.note_revision_id == "external_note_revision_proven_superset"
    assert effective.coverage is NoteCoverage.FULL
    assert effective.full_body == f"{legacy_editor_body}\n\n08/27 补充观点"
    assert [(item.speaker_label, item.body) for item in effective.blocks] == [
        ("USER", "目前在70-80区间震荡"),
        ("宝总", "突破82还是看100"),
        ("USER", "补充观点"),
    ]
    assert effective.blocks[-1].section_date == "08/27"
    engine.dispose()


@pytest.mark.asyncio
async def test_summary_only_note_is_never_sent_for_model_interpretation(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache, include_editor=False)
    calls = 0

    class Interpretation:
        async def analyze(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            raise AssertionError("summary-only note must not reach the model")

    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
        Interpretation(),  # type: ignore[arg-type]
    )

    receipt = await service.sync(analyze=True)
    pending = await service.analyze_pending(retry_failed=True)
    summary_revision = service.inbox()[0].revision
    repository.append_interpretation(
        ExternalNoteInterpretation(
            interpretation_id="external_note_interpretation_legacy_summary",
            note_revision_id=summary_revision.note_revision_id,
            status="SUCCEEDED",
            provider="legacy",
            model="legacy",
            reasoning_effort="max",
            schema_version="legacy",
            payload_json="{}",
            error_code=None,
            created_at=NOW,
        )
    )

    assert calls == 0
    assert pending == ()
    assert "MOOMOO_NOTES_FULL_TEXT_UNAVAILABLE" in receipt.warning_codes
    assert service.inbox()[0].interpretation is None
    engine.dispose()


@pytest.mark.asyncio
async def test_background_interpretation_compares_latest_revision_to_prior_success(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    captured: list[str | None] = []

    class Interpretation:
        async def analyze(
            self, revision: ExternalNoteRevision, previous_payload_json: str | None
        ) -> ExternalNoteInterpretation:
            captured.append(previous_payload_json)
            return ExternalNoteInterpretation(
                interpretation_id=f"interpretation-{revision.version}",
                note_revision_id=revision.note_revision_id,
                status="SUCCEEDED",
                provider="test",
                model="test",
                reasoning_effort="max",
                schema_version="test-v1",
                payload_json=json.dumps({"revision": revision.version}),
                error_code=None,
                created_at=NOW,
            )

    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
        Interpretation(),  # type: ignore[arg-type]
    )
    fixed_clock.set(NOW)
    await service.sync(analyze=False)
    await service.analyze_pending()
    _cache(cache, user_text="跌破69时原有上涨逻辑失效")
    await service.sync(analyze=False)

    await service.analyze_pending()

    assert captured == [None, '{"revision": 1}']
    revisions = repository.list_latest()
    assert revisions[0][1].version == 2
    engine.dispose()


@pytest.mark.asyncio
async def test_explicit_reanalysis_replaces_latest_successful_interpretation(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    calls = 0

    class Interpretation:
        async def analyze(
            self, revision: ExternalNoteRevision, _previous: str | None
        ) -> ExternalNoteInterpretation:
            nonlocal calls
            calls += 1
            return ExternalNoteInterpretation(
                interpretation_id=f"interpretation-{calls}",
                note_revision_id=revision.note_revision_id,
                status="SUCCEEDED",
                provider="test",
                model="test",
                reasoning_effort="max",
                schema_version="test-v1",
                payload_json=json.dumps({"attempt": calls}),
                error_code=None,
                created_at=NOW.replace(second=calls),
            )

    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
        Interpretation(),  # type: ignore[arg-type]
    )
    await service.sync(analyze=False)
    first = await service.analyze_pending()
    skipped = await service.analyze_pending()
    repeated = await service.analyze_pending(
        retry_failed=True,
        reanalyze_succeeded=True,
    )

    assert len(first) == 1
    assert skipped == ()
    assert len(repeated) == 1
    assert calls == 2
    latest = service.inbox()[0].interpretation
    assert latest is not None
    assert json.loads(latest.payload_json) == {"attempt": 2}
    engine.dispose()


@pytest.mark.asyncio
async def test_failed_reanalysis_preserves_prior_successful_interpretation(
    migrated_sqlite_url, fixed_clock, id_generator, tmp_path: Path
) -> None:
    engine = create_engine(migrated_sqlite_url)
    repository = SqlAlchemyExternalNoteRepository(engine)
    cache = tmp_path / "note-cache"
    cache.mkdir()
    stock_db = tmp_path / "stock.db"
    _stock_database(stock_db)
    _cache(cache)
    calls = 0

    class Interpretation:
        async def analyze(
            self, revision: ExternalNoteRevision, _previous: str | None
        ) -> ExternalNoteInterpretation:
            nonlocal calls
            calls += 1
            succeeded = calls == 1
            return ExternalNoteInterpretation(
                interpretation_id=f"interpretation-{calls}",
                note_revision_id=revision.note_revision_id,
                status="SUCCEEDED" if succeeded else "FAILED",
                provider="test",
                model="test",
                reasoning_effort="max",
                schema_version="test-v1",
                payload_json=json.dumps({"attempt": calls}) if succeeded else "{}",
                error_code=None if succeeded else "NOTE_INTERPRETATION_SCHEMA_INVALID",
                created_at=NOW.replace(second=calls),
            )

    service = ExternalNoteSyncService(
        MoomooNotesCacheProvider(
            cache_data_dir=cache,
            stock_database_path=stock_db,
            clock=fixed_clock,
        ),
        repository,
        fixed_clock,
        id_generator,
        Interpretation(),  # type: ignore[arg-type]
    )
    await service.sync(analyze=False)
    await service.analyze_pending()
    repeated = await service.analyze_pending(reanalyze_succeeded=True)

    assert repeated[0].status == "FAILED"
    latest = service.inbox()[0].interpretation
    assert latest is not None
    assert latest.status == "SUCCEEDED"
    assert json.loads(latest.payload_json) == {"attempt": 1}
    engine.dispose()
