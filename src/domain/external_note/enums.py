from enum import StrEnum


class NoteCoverage(StrEnum):
    FULL = "FULL"
    SUMMARY_ONLY = "SUMMARY_ONLY"


class NoteSpeakerKind(StrEnum):
    USER = "USER"
    NAMED_PERSON = "NAMED_PERSON"


class NoteSyncStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ExternalNoteReviewStatus(StrEnum):
    """Closed lifecycle for one exact FULL Observation revision."""

    PENDING = "PENDING"
    DEFERRED = "DEFERRED"
    ADOPTED = "ADOPTED"
    NO_ACTION = "NO_ACTION"
