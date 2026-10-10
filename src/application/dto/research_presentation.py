"""Host-owned research presentation; never part of the model answer schema."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchTextSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["text"] = "text"
    text: str = Field(min_length=1, max_length=200_000)


class ResearchMetadataSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["metadata"] = "metadata"
    text: str = Field(min_length=1, max_length=4_000)


class ResearchFieldSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["field"] = "field"
    ref: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]*$")
    path: str = Field(min_length=1, max_length=160, pattern=r"^result(?:/[A-Za-z0-9_.:-]+)+$")
    scalar: str = Field(min_length=1, max_length=160)
    is_null: bool
    # Ordered pairs preserve canonical rendering across JSON sort_keys and JS.
    context: tuple[
        tuple[
            Annotated[str, Field(min_length=1, max_length=80)],
            Annotated[str, Field(min_length=1, max_length=4_000)],
        ],
        ...,
    ] = Field(max_length=48)
    inline: bool = False

    @model_validator(mode="after")
    def _consistent_field(self) -> ResearchFieldSegment:
        if not self.ref.endswith("/" + self.path):
            raise ValueError("field reference must match its path")
        if self.is_null and self.scalar != "null":
            raise ValueError("missing field must retain its null representation")
        if len({key for key, _ in self.context}) != len(self.context):
            raise ValueError("field context keys must be unique")
        if len(self.raw_text()) > 4_000:
            raise ValueError("field text exceeds answer block bounds")
        return self

    def raw_text(self) -> str:
        text = f"{self.path}: {self.scalar}"
        if self.context:
            text += " · " + " · ".join(f"{key}={value}" for key, value in self.context)
        return text

    def rendered_text(self) -> str:
        raw = self.raw_text()
        return f"〔{raw}〕" if self.inline else raw


ResearchSegment = Annotated[
    ResearchTextSegment | ResearchMetadataSegment | ResearchFieldSegment,
    Field(discriminator="kind"),
]


class ResearchAnswerPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    segments: tuple[ResearchSegment, ...] = Field(min_length=1, max_length=512)

    def rendered_text(self) -> str:
        return "".join(
            segment.rendered_text() if isinstance(segment, ResearchFieldSegment) else segment.text
            for segment in self.segments
        )
