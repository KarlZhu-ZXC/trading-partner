"""Session-bound read model for Monitor lifecycle dependencies."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from domain.monitoring.enums import MonitorStatus
from infrastructure.persistence.orm.monitoring import MonitorVersionRow


class SqlAlchemyMonitorLifecycleReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_live_ids(
        self,
        *,
        subject_id: str | None = None,
        trade_plan_id: str | None = None,
    ) -> tuple[str, ...]:
        if subject_id is None and trade_plan_id is None:
            return ()
        latest = (
            select(
                MonitorVersionRow.monitor_id,
                func.max(MonitorVersionRow.version).label("latest_version"),
            )
            .group_by(MonitorVersionRow.monitor_id)
            .subquery()
        )
        statement = (
            select(MonitorVersionRow.monitor_id)
            .join(
                latest,
                (MonitorVersionRow.monitor_id == latest.c.monitor_id)
                & (MonitorVersionRow.version == latest.c.latest_version),
            )
            .where(
                MonitorVersionRow.status.in_(
                    (MonitorStatus.ACTIVE.value, MonitorStatus.PAUSED.value)
                )
            )
        )
        if subject_id is not None:
            statement = statement.where(MonitorVersionRow.subject_id == subject_id)
        if trade_plan_id is not None:
            statement = statement.where(MonitorVersionRow.trade_plan_id == trade_plan_id)
        statement = statement.order_by(MonitorVersionRow.monitor_id)
        return tuple(self._session.scalars(statement))
