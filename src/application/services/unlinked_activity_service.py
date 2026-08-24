"""Compatibility name for the Phase 4B activity annotation service."""

from application.services.activity_annotation_service import (
    ActivityAnnotationService,
    unlinked_activity_source_key,
)

UnlinkedActivityService = ActivityAnnotationService

__all__ = ["ActivityAnnotationService", "UnlinkedActivityService", "unlinked_activity_source_key"]
