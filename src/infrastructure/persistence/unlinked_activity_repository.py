"""Compatibility exports for activity annotation persistence."""

from infrastructure.persistence.activity_annotation_repository import (
    SqlAlchemyActivityAnnotationRepository,
)

SqlAlchemyUnlinkedActivityRepository = SqlAlchemyActivityAnnotationRepository

__all__ = [
    "SqlAlchemyActivityAnnotationRepository",
    "SqlAlchemyUnlinkedActivityRepository",
]
