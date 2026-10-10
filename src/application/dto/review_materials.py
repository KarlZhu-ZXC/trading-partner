"""Server-prepared reference manifest, never evidence of reading or adoption."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ReviewMaterialRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal[
        "THESIS", "PLAN", "CHANGE", "OBSERVATION_REVISION", "ACCOUNT_SNAPSHOT", "OBSERVATION_REVIEW"
    ]
    source_id: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, max_length=128)
    as_of: AwareDatetime | None = None


class ReviewMaterials(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    subject_id: str = Field(min_length=1, max_length=128)
    baseline_decision_id: str | None = Field(default=None, max_length=128)
    prepared_at: AwareDatetime
    references: tuple[ReviewMaterialRef, ...] = Field(max_length=1500)
    reference_count: int = Field(ge=0)
    references_complete: bool
    coverage: dict[str, str]
    warning_codes: tuple[str, ...]
    pending_observation_reviews: int | None = Field(default=None, ge=0)


def review_materials_key(decision_key: str) -> str:
    """Deterministic companion Journal identity, inside the Decision UoW."""
    import hashlib

    return "review-materials:" + hashlib.sha256(decision_key.encode()).hexdigest()
