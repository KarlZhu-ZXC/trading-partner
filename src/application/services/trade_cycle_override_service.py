"""Application boundary for append-only Trade Cycle manual overrides."""

from __future__ import annotations

from collections.abc import Iterable

from application.dto.trade_cycle_overrides import (
    TradeCycleOverrideAppendInput,
    TradeCycleOverrideImpactDTO,
    TradeCycleOverrideProjectionDTO,
    TradeCycleOverrideRevisionDTO,
)
from application.ports.clock import Clock
from application.ports.id_generator import IdGenerator
from application.ports.trade_cycle_override_repository import TradeCycleOverrideRepository
from domain.common.ids import EntityIdPrefix
from domain.portfolio.models import TradeCycleProjection
from domain.portfolio.trade_cycle_overrides import (
    TradeCycleOverrideProjection,
    TradeCycleOverrideRevision,
    apply_trade_cycle_overrides,
)


class TradeCycleOverrideService:
    """Append revisions and produce a deterministic effective Cycle preview."""

    def __init__(
        self,
        repository: TradeCycleOverrideRepository,
        clock: Clock,
        id_generator: IdGenerator,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._ids = id_generator

    def append_revision(
        self,
        request: TradeCycleOverrideAppendInput | None = None,
        *,
        projection: TradeCycleProjection | TradeCycleOverrideProjection | None = None,
        **values: object,
    ) -> TradeCycleOverrideRevisionDTO:
        if request is None:
            request = TradeCycleOverrideAppendInput.model_validate(values)
        elif values:
            raise ValueError("request and keyword override values cannot be mixed")
        root = request.root_cycle_id
        assert root is not None
        latest = self._repository.get_latest(root)
        current_version = latest.version if latest is not None else 0
        revision = TradeCycleOverrideRevision(
            override_id=self._ids.new(EntityIdPrefix.TRADE_CYCLE_OVERRIDE),
            root_cycle_id=root,
            version=current_version + 1,
            operation=request.operation,
            cycle_ids=request.cycle_ids or (root,),
            activity_ids=request.activity_ids,
            split_groups=request.split_groups,
            target_cycle_id=request.target_cycle_id,
            algorithm_version=request.algorithm_version,
            note=request.note,
            actor=request.actor,
            authorization_note=request.authorization_note,
            idempotency_key=request.idempotency_key,
            created_at=self._clock.now(),
            expected_version=request.expected_version,
        )
        if projection is not None:
            # Fail closed before persistence if this revision cannot be applied
            # to the currently displayed algorithm projection.
            from domain.portfolio.models import TradeCycleProjection

            if isinstance(projection, TradeCycleProjection):
                base_projection = projection
                prior_revisions = self._repository.list(root_cycle_id=None, limit=None)
            else:
                base_projection = projection.algorithm_projection
                prior_revisions = projection.applied_revisions
            apply_trade_cycle_overrides(
                base_projection,
                (*prior_revisions, revision),
            )
        stored = self._repository.append(revision, expected_version=request.expected_version)
        return TradeCycleOverrideRevisionDTO.from_domain(stored)

    append = append_revision

    def preview_revision(
        self,
        request: TradeCycleOverrideAppendInput,
        *,
        projection: TradeCycleProjection,
    ) -> TradeCycleOverrideProjectionDTO:
        """Preview one proposed revision with all durable prior revisions, without writing."""

        root = request.root_cycle_id
        assert root is not None
        latest = self._repository.get_latest(root)
        revision = TradeCycleOverrideRevision(
            override_id="trade_cycle_override_preview",
            root_cycle_id=root,
            version=(latest.version if latest is not None else 0) + 1,
            operation=request.operation,
            cycle_ids=request.cycle_ids or (root,),
            activity_ids=request.activity_ids,
            split_groups=request.split_groups,
            target_cycle_id=request.target_cycle_id,
            algorithm_version=request.algorithm_version,
            note=request.note,
            actor=request.actor,
            authorization_note=request.authorization_note,
            idempotency_key=request.idempotency_key,
            created_at=self._clock.now(),
            expected_version=request.expected_version,
        )
        prior = self._repository.list(root_cycle_id=None, limit=None)
        return TradeCycleOverrideProjectionDTO.from_domain(
            apply_trade_cycle_overrides(projection, (*prior, revision))
        )

    def list_revisions(
        self, *, root_cycle_id: str | None = None, limit: int | None = None
    ) -> tuple[TradeCycleOverrideRevisionDTO, ...]:
        values = self._repository.list(root_cycle_id=root_cycle_id, limit=limit)
        return tuple(TradeCycleOverrideRevisionDTO.from_domain(item) for item in values)

    def preview(
        self,
        projection: TradeCycleOverrideProjection | object,
        *,
        root_cycle_id: str | None = None,
        revisions: Iterable[TradeCycleOverrideRevision] | None = None,
    ) -> TradeCycleOverrideProjectionDTO:
        """Apply durable revisions to an algorithm projection without writing."""

        # Accept a bare TradeCycleProjection as a convenience for callers that
        # have only calculator output; original and effective remain separate.
        from domain.portfolio.models import TradeCycleProjection

        if isinstance(projection, TradeCycleProjection):
            base = projection
            prior: tuple[TradeCycleOverrideRevision, ...] = ()
        elif isinstance(projection, TradeCycleOverrideProjection):
            base = projection.algorithm_projection
            prior = projection.applied_revisions
        else:
            raise TypeError("preview requires TradeCycleProjection or TradeCycleOverrideProjection")
        selected = tuple(revisions) if revisions is not None else tuple(
            self._repository.list(root_cycle_id=root_cycle_id, limit=None)
        )
        result = apply_trade_cycle_overrides(base, (*prior, *selected))
        return TradeCycleOverrideProjectionDTO.from_domain(result)

    apply = preview

    def preview_impact(
        self,
        projection: TradeCycleProjection | TradeCycleOverrideProjection,
        *,
        root_cycle_id: str | None = None,
    ) -> tuple[TradeCycleOverrideImpactDTO, ...]:
        value = self.preview(projection, root_cycle_id=root_cycle_id)
        return value.impacts


__all__ = ["TradeCycleOverrideService"]
