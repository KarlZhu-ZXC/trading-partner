"""Cross-Research-Subject link invariants shared by write services."""

from __future__ import annotations

from collections.abc import Iterable

from application.ports.research_unit_of_work import ResearchUnitOfWork
from domain.common.errors import DataContractError, InputValidationError, ResearchSubjectNotFound


def validate_linked_subject_ids(
    uow: ResearchUnitOfWork,
    *,
    owner_subject_id: str,
    linked_subject_ids: Iterable[str],
    confirmation: bool = False,
) -> tuple[str, ...]:
    """Normalize and validate durable Research Subject graph edges.

    Links live in JSON for compatibility, so application writes must provide the
    existence, uniqueness, and no-self-edge guarantees that a relational join
    table would otherwise enforce.
    """

    normalized = tuple(value.strip() for value in linked_subject_ids if value.strip())
    error_type = DataContractError if confirmation else InputValidationError
    if len(normalized) != len(set(normalized)):
        raise error_type(
            "linked_subject_ids must not contain duplicates",
            details={"subject_id": owner_subject_id},
        )
    if owner_subject_id in normalized:
        raise error_type(
            "Research Subject cannot link to itself",
            details={"subject_id": owner_subject_id},
        )
    for linked_subject_id in normalized:
        try:
            uow.subjects.get(linked_subject_id)
        except ResearchSubjectNotFound as exc:
            raise error_type(
                "linked_subject_id does not identify an existing Research Subject",
                details={
                    "subject_id": owner_subject_id,
                    "linked_subject_id": linked_subject_id,
                },
            ) from exc
    return normalized


__all__ = ["validate_linked_subject_ids"]
