"""Compact US-company research operation adapters."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import ValidationError

from application.dto.us_research import (
    EventsSearchInput,
    FundamentalGetSnapshotInput,
    FundamentalGetStatementsInput,
    ResearchGetCompanyUpdatesInput,
    USGetFilingsInput,
    USGetInsiderActivityInput,
)
from bootstrap import ApplicationContainer
from interfaces.mcp.validation import unexpected_failure as _unexpected_failure


def build_us_research_adapters(container: ApplicationContainer) -> SimpleNamespace:
    """Build compact US-company research adapters."""

    # -------------------------------------------------------- Phase 1G US research
    async def us_get_fundamentals(
        instrument_id: str,
        as_of: datetime | None = None,
        operation: Literal["snapshot", "statements"] = "snapshot",
        frequency: str = "quarterly",
        limit: int = 8,
        view: Literal["latest", "vintages"] = "latest",
    ) -> dict[str, Any]:
        """Return a US fundamental snapshot or normalized statements."""
        if operation == "statements":
            return await fundamental_get_statements(instrument_id, frequency, as_of, limit, view)
        if operation != "snapshot":
            raise ValueError("operation must be snapshot or statements")
        try:
            inp = FundamentalGetSnapshotInput.model_validate(
                {"instrument_id": instrument_id, "as_of": as_of}
            )
            envelope = await container.services.us_research.get_fundamental_snapshot(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def fundamental_get_statements(
        instrument_id: str,
        frequency: str = "quarterly",
        as_of: datetime | None = None,
        limit: int = 8,
        view: str = "latest",
    ) -> dict[str, Any]:
        """Return normalized US income, balance-sheet, and cash-flow periods."""
        try:
            inp = FundamentalGetStatementsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "frequency": frequency,
                    "as_of": as_of,
                    "limit": limit,
                    "view": view,
                }
            )
            envelope = await container.services.us_research.get_fundamental_statements(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_company_research(
        operation: Literal["filings", "insider_activity", "company_updates", "events"] = "filings",
        instrument_id: str | None = None,
        forms: tuple[str, ...] = (),
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        include_sections: bool = False,
        limit: int = 20,
        since: datetime | None = None,
        event_types: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Read filings, insider activity, company updates, or external events."""
        if operation == "insider_activity":
            if instrument_id is None:
                raise ValueError("instrument_id is required for insider_activity")
            return await us_get_insider_activity(instrument_id, start, end, as_of, limit)
        if operation == "company_updates":
            if instrument_id is None:
                raise ValueError("instrument_id is required for company_updates")
            return await research_get_company_updates(instrument_id, since, as_of, limit)
        if operation == "events":
            return await events_search(instrument_id, event_types, start, end, as_of, limit)
        if operation != "filings":
            raise ValueError(
                "operation must be filings, insider_activity, company_updates, or events"
            )
        if instrument_id is None:
            raise ValueError("instrument_id is required for filings")
        try:
            inp = USGetFilingsInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "forms": forms,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "include_sections": include_sections,
                    "limit": limit,
                }
            )
            envelope = await container.services.us_research.get_filings(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def us_get_insider_activity(
        instrument_id: str,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Return visible-at-as_of SEC/Alpha insider transactions."""
        try:
            inp = USGetInsiderActivityInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.services.us_research.get_insider_activity(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def research_get_company_updates(
        instrument_id: str,
        since: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Merge recent filings, insider activity, and corporate actions."""
        try:
            inp = ResearchGetCompanyUpdatesInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "since": since,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.services.us_research.get_company_updates(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    async def events_search(
        instrument_id: str | None = None,
        event_types: tuple[str, ...] = (),
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search the Phase 1G external-event view."""
        try:
            inp = EventsSearchInput.model_validate(
                {
                    "instrument_id": instrument_id,
                    "event_types": event_types,
                    "start": start,
                    "end": end,
                    "as_of": as_of,
                    "limit": limit,
                }
            )
            envelope = await container.services.us_research.search_events(inp)
            return envelope.model_dump(mode="json")
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            return _unexpected_failure(container, exc)

    return SimpleNamespace(
        us_get_fundamentals=us_get_fundamentals,
        us_get_company_research=us_get_company_research,
    )
