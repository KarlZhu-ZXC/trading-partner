from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

from application.dto.watchlist_hub import (
    WatchlistAddInput,
    WatchlistGetGroupsInput,
    WatchlistGetItemsInput,
    WatchlistRemoveInput,
)
from application.dto.watchlist_source import (
    WatchlistSourceGroup,
    WatchlistSourceMembership,
)
from application.services.watchlist_hub_service import WatchlistHubService
from domain.common.errors import PersistenceError, ProviderUnavailableError
from domain.watchlist.enums import (
    WatchlistGroupType,
    WatchlistMutationStatus,
    WatchlistSource,
)
from infrastructure.persistence.database import create_engine_from_url
from infrastructure.persistence.research_unit_of_work import SqlAlchemyResearchUnitOfWork
from infrastructure.persistence.watchlist_hub_unit_of_work import (
    SqlAlchemyWatchlistHubUnitOfWork,
)
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator
from infrastructure.system.redactor import DefaultSecretRedactor


class _FakeWatchlistProvider:
    source = WatchlistSource.MOOMOO

    def __init__(self) -> None:
        self.fail_reads = False
        self.group_reads = 0
        self.membership_reads = 0
        self.add_calls = 0
        self.remove_calls = 0
        self.groups = (
            WatchlistSourceGroup(
                source=self.source,
                name="Favorites",
                group_type=WatchlistGroupType.SYSTEM,
                writable=True,
            ),
        )
        self.memberships: dict[str, list[WatchlistSourceMembership]] = {
            "Favorites": [
                WatchlistSourceMembership(
                    source=self.source,
                    group_name="Favorites",
                    provider_code="US.NVDA",
                    display_name="NVIDIA",
                    instrument_id="equity:US:NVDA",
                    provider_asset_type="STOCK",
                    research_supported=True,
                    group_writable=True,
                )
            ]
        }

    async def list_groups(self) -> tuple[WatchlistSourceGroup, ...]:
        self.group_reads += 1
        if self.fail_reads:
            raise ProviderUnavailableError("watchlist source unavailable")
        return self.groups

    async def list_memberships(
        self, group_name: str | None = None
    ) -> tuple[WatchlistSourceMembership, ...]:
        self.membership_reads += 1
        if self.fail_reads:
            raise ProviderUnavailableError("watchlist source unavailable")
        if group_name is not None:
            return tuple(self.memberships.get(group_name, ()))
        return tuple(item for values in self.memberships.values() for item in values)

    async def add_membership(
        self,
        *,
        group_name: str,
        provider_code: str,
        display_name: str,
    ) -> WatchlistSourceMembership:
        self.add_calls += 1
        item = WatchlistSourceMembership(
            source=self.source,
            group_name=group_name,
            provider_code=provider_code,
            display_name=display_name,
            instrument_id=f"equity:US:{provider_code.removeprefix('US.')}",
            provider_asset_type="STOCK",
            research_supported=True,
            group_writable=True,
        )
        self.memberships.setdefault(group_name, []).append(item)
        return item

    async def remove_membership(
        self, *, group_name: str, provider_code: str
    ) -> WatchlistSourceMembership:
        self.remove_calls += 1
        values = self.memberships[group_name]
        item = next(value for value in values if value.provider_code == provider_code)
        self.memberships[group_name] = [
            value for value in values if value.provider_code != provider_code
        ]
        return item


def _service(
    database_url: str,
    provider: _FakeWatchlistProvider,
) -> tuple[WatchlistHubService, Engine]:
    engine = create_engine_from_url(database_url)
    clock = SystemClock()
    ids = Uuid7IdGenerator()
    redactor = DefaultSecretRedactor()

    def watchlist_uow() -> SqlAlchemyWatchlistHubUnitOfWork:
        return SqlAlchemyWatchlistHubUnitOfWork(engine, clock, ids, redactor)

    def research_uow() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    return (
        WatchlistHubService(
            provider=provider,
            uow_factory=watchlist_uow,
            research_uow_factory=research_uow,
            default_group="Favorites",
            clock=clock,
            id_generator=ids,
            secret_redactor=redactor,
        ),
        engine,
    )


@pytest.mark.asyncio
async def test_watchlist_hub_refresh_restart_mutate_and_stale_fallback(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'watchlist-hub.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    provider = _FakeWatchlistProvider()
    service, engine = _service(database_url, provider)

    groups = await service.get_groups(WatchlistGetGroupsInput(refresh=True))
    assert groups.ok is True
    assert groups.data is not None
    assert [group.name for group in groups.data.groups] == ["Favorites"]

    initial = await service.get_items(WatchlistGetItemsInput(refresh=True))
    assert initial.ok is True
    assert initial.data is not None
    assert [item.provider_code for item in initial.data.items] == ["US.NVDA"]
    nvda_membership_id = initial.data.items[0].membership_id
    refreshed_again = await service.get_items(WatchlistGetItemsInput(refresh=True))
    assert refreshed_again.data is not None
    assert refreshed_again.data.items[0].membership_id == nvda_membership_id

    # Matching research metadata is deliberately a separate aggregate/table.
    now = "2026-07-18T12:00:00+00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO investment_cases "
                "(case_id,case_type,title,summary,status,primary_instrument_id,"
                "topic_tags_json,created_at,updated_at,created_by,archived_at,"
                "archived_reason,linked_case_ids_json,evidence_ids_json,"
                "report_ids_json,event_ids_json,decision_ids_json,schema_version) "
                "VALUES (:case_id,'company','MSFT case','separate research','draft',"
                "'equity:US:MSFT','[]',:now,:now,'user',NULL,NULL,'[]','[]','[]',"
                "'[]','[]',1)"
            ),
            {"case_id": "case_phase2_separation", "now": now},
        )
        connection.execute(
            text(
                "INSERT INTO watchlist_items "
                "(item_id,market,symbol,display_name,thesis_hint,triggers_json,"
                "case_id,status,created_at,updated_at,expires_at,promoted_to_case_id,"
                "triggered_at,triggered_reason) "
                "VALUES ('research_watch_msft','US','MSFT','Microsoft','research hint',"
                "'[]',:case_id,'watching',:now,:now,NULL,NULL,NULL,NULL)"
            ),
            {"case_id": "case_phase2_separation", "now": now},
        )

    added = await service.add(
        WatchlistAddInput(
            instrument_id="equity:US:MSFT",
            display_name="Microsoft",
            confirmed_by="user",
            idempotency_key="add-msft-once",
        )
    )
    assert added.ok is True
    assert added.data is not None
    membership_id = added.data.membership.membership_id
    assert added.data.membership.provider_code == "US.MSFT"
    assert added.data.membership.research_watchlist_item_ids == ("research_watch_msft",)
    assert added.data.membership.research_subject_ids == ("case_phase2_separation",)

    duplicate = await service.add(
        WatchlistAddInput(
            instrument_id="equity:US:MSFT",
            display_name="Microsoft",
            confirmed_by="user",
            idempotency_key="add-msft-once",
        )
    )
    assert duplicate.ok is True
    assert duplicate.degraded is True
    assert provider.add_calls == 1

    removed = await service.remove(
        WatchlistRemoveInput(
            membership_id=membership_id,
            confirmed_by="user",
            idempotency_key="remove-msft-once",
        )
    )
    assert removed.ok is True
    assert removed.data is not None
    assert removed.data.membership.active is False
    assert provider.remove_calls == 1
    history = await service.get_items(WatchlistGetItemsInput(refresh=False, include_inactive=True))
    assert history.data is not None
    removed_history = next(item for item in history.data.items if item.provider_code == "US.MSFT")
    assert removed_history.membership_id == membership_id
    assert removed_history.active is False
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM investment_cases WHERE case_id=:case_id"),
                {"case_id": "case_phase2_separation"},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM watchlist_items WHERE item_id='research_watch_msft'")
            ).scalar_one()
            == 1
        )

    # A new service over the same database proves durable restart recovery.
    restarted, restarted_engine = _service(database_url, provider)
    provider.fail_reads = True
    stale = await restarted.get_items(WatchlistGetItemsInput(refresh=True))
    assert stale.ok is True
    assert stale.degraded is True
    assert stale.warnings[0].code == "WATCHLIST_SOURCE_UNAVAILABLE_USING_DURABLE_STATE"
    assert stale.data is not None
    assert [item.provider_code for item in stale.data.items] == ["US.NVDA"]

    provider.fail_reads = False
    provider.memberships["Favorites"] = []
    empty = await restarted.get_items(WatchlistGetItemsInput(refresh=True))
    assert empty.ok is True
    assert empty.data is not None and empty.data.items == ()
    provider.fail_reads = True
    stale_empty = await restarted.get_items(WatchlistGetItemsInput(refresh=True))
    assert stale_empty.ok is True
    assert stale_empty.degraded is True
    assert stale_empty.data is not None and stale_empty.data.items == ()

    restarted_engine.dispose()
    engine.dispose()


@pytest.mark.asyncio
async def test_watchlist_reads_are_durable_first_by_default(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'watchlist-durable-first.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    provider = _FakeWatchlistProvider()
    service, engine = _service(database_url, provider)

    groups = await service.get_groups(WatchlistGetGroupsInput())
    items = await service.get_items(WatchlistGetItemsInput())

    assert groups.ok is True and groups.data is not None
    assert groups.data.groups == ()
    assert items.ok is False
    assert items.errors[0].code == "WATCHLIST_GROUP_NOT_FOUND"
    assert provider.group_reads == 0
    assert provider.membership_reads == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_watchlist_hub_full_sync_is_read_only_and_preserves_removed_history(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'watchlist-full-sync.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    provider = _FakeWatchlistProvider()
    provider.groups = (
        *provider.groups,
        WatchlistSourceGroup(
            source=provider.source,
            name="All",
            group_type=WatchlistGroupType.SYSTEM,
            writable=True,
        ),
    )
    provider.memberships["All"] = [
        WatchlistSourceMembership(
            source=provider.source,
            group_name="All",
            provider_code="US.NVDA",
            display_name="NVIDIA",
            instrument_id="equity:US:NVDA",
            provider_asset_type="STOCK",
            research_supported=True,
            group_writable=True,
        ),
        WatchlistSourceMembership(
            source=provider.source,
            group_name="All",
            provider_code="FX.XAUUSD",
            display_name="Gold",
            instrument_id=None,
            provider_asset_type="FUTURE",
            research_supported=False,
            group_writable=True,
        ),
    ]
    service, engine = _service(database_url, provider)

    first = await service.sync_all()
    assert first.ok is True
    assert first.data is not None
    assert first.data.groups_synced == 2
    assert first.data.membership_relations_synced == 3
    assert first.data.unique_provider_codes == 2
    assert first.data.research_supported_unique == 1
    assert first.data.unsupported_unique == 1
    assert provider.add_calls == 0
    assert provider.remove_calls == 0

    default_items = await service.get_items(WatchlistGetItemsInput(limit=1))
    assert default_items.ok is True and default_items.data is not None
    assert default_items.data.group.name == "All"
    assert default_items.data.group_was_defaulted is True
    assert default_items.data.total_returned == 1
    assert default_items.data.total_count == 2
    assert default_items.data.has_more is True

    provider.groups = provider.groups[:1]
    del provider.memberships["All"]
    second = await service.sync_all()
    assert second.ok is True
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM watchlist_groups WHERE active=0")
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT COUNT(*) FROM watchlist_memberships WHERE active=0")
            ).scalar_one()
            == 2
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM watchlist_mutations")).scalar_one() == 0
        )
    engine.dispose()


class _FailingCommitUow:
    def __init__(self, delegate: SqlAlchemyWatchlistHubUnitOfWork, *, fail: bool) -> None:
        self._delegate = delegate
        self._fail = fail

    def __enter__(self) -> _FailingCommitUow:
        self._delegate.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._delegate.__exit__(exc_type, exc, tb)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)

    def commit(self) -> None:
        if self._fail:
            self._delegate.rollback()
            raise PersistenceError("injected commit failure")
        self._delegate.commit()


@pytest.mark.asyncio
async def test_watchlist_hub_records_partial_when_source_write_outlives_db_commit(
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'watchlist-partial.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    provider = _FakeWatchlistProvider()
    normal_service, engine = _service(database_url, provider)
    seeded = await normal_service.get_groups(WatchlistGetGroupsInput())
    assert seeded.ok is True

    clock = SystemClock()
    ids = Uuid7IdGenerator()
    redactor = DefaultSecretRedactor()
    call_count = 0

    def flaky_uow() -> _FailingCommitUow:
        nonlocal call_count
        call_count += 1
        delegate = SqlAlchemyWatchlistHubUnitOfWork(engine, clock, ids, redactor)
        # add(): idempotency read, group refresh, group read, pending commit,
        # then the fifth UoW is the post-source success commit.
        return _FailingCommitUow(delegate, fail=call_count == 5)

    def research_uow() -> SqlAlchemyResearchUnitOfWork:
        return SqlAlchemyResearchUnitOfWork(engine, clock, ids, redactor)

    service = WatchlistHubService(
        provider=provider,
        uow_factory=flaky_uow,  # type: ignore[arg-type]
        research_uow_factory=research_uow,
        default_group="Favorites",
        clock=clock,
        id_generator=ids,
        secret_redactor=redactor,
    )
    result = await service.add(
        WatchlistAddInput(
            instrument_id="equity:US:MSFT",
            confirmed_by="user",
            idempotency_key="partial-msft",
        )
    )
    assert result.ok is False
    assert result.errors[0].code == "PARTIAL_DATA_ERROR"
    assert any(item.provider_code == "US.MSFT" for item in provider.memberships["Favorites"])

    with SqlAlchemyWatchlistHubUnitOfWork(engine, clock, ids, redactor) as uow:
        mutation = uow.mutations.get_by_idempotency_key("partial-msft")
        assert mutation is not None
        assert mutation.status is WatchlistMutationStatus.PARTIAL
        assert mutation.error_code == "PERSISTENCE_ERROR"
    engine.dispose()
