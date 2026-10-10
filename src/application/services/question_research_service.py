"""Prepare and revalidate a question-specific read-only Research context."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict
from datetime import datetime, timedelta
from threading import RLock
from typing import Any

from pydantic import ValidationError

from application.dto.question_research import QuestionResearchContext
from application.dto.research_links import ResearchSourcePayload
from application.ports.clock import Clock
from application.services._research_support import UowFactory
from domain.common.errors import DataContractError


class QuestionResearchService:
    def __init__(self, uow_factory: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow_factory, clock
        self._prepared: dict[str, tuple[QuestionResearchContext, str, datetime]] = {}
        self._lock = RLock()

    @staticmethod
    def _question(uow: Any, subject_id: str, question_id: str, version: int) -> Any:
        subject = uow.subjects.get(subject_id)
        question = uow.questions.get(question_id)
        if (
            question is None
            or question.subject_id != subject_id
            or str(subject.status).lower() == "archived"
            or str(question.status).upper() != "OPEN"
            or question.version != version
        ):
            raise DataContractError("Question scope or version changed. Prepare Research again.")
        return question

    @staticmethod
    def _fingerprint(question: Any) -> str:
        payload = {
            "text": question.text,
            "requirements": question.evidence_requirements,
            "version": question.version,
            "subject_id": question.subject_id,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _target(uow: Any, context: QuestionResearchContext) -> None:
        if context.thesis_revision_id is None:
            return
        revision = uow.revisions.get(context.thesis_revision_id)
        thesis = uow.theses.get(revision.thesis_id)
        assumptions = uow.assumptions.list_by_revision(thesis.thesis_id, revision.revision_no)
        cutoff = datetime.fromisoformat(context.evidence_cutoff)
        if (
            revision.subject_id != context.subject_id
            or thesis.subject_id != context.subject_id
            or thesis.latest_revision_id != revision.revision_id
            or revision.confirmed_at > cutoff
            or not any(
                a.assumption_id == context.assumption_id
                and a.subject_id == context.subject_id
                and a.thesis_id == thesis.thesis_id
                and str(a.status).lower() == "accepted"
                and a.confirmed_at <= cutoff
                for a in assumptions
            )
        ):
            raise DataContractError("The selected assumption changed. Prepare Research again.")

    @staticmethod
    def _prompt(question: Any, context: QuestionResearchContext) -> str:
        # Visible in the composer and persisted verbatim in the user message.
        return "\n".join(
            [
                "Research this saved question using sourced facts, "
                "contrary evidence and explicit gaps.",
                f"Research Subject: {context.subject_id}",
                f"Question: {context.question_id} · version {context.question_version}",
                f"Evidence cutoff: {context.evidence_cutoff}",
                "Selected assumption: "
                + (
                    f"{context.thesis_revision_id} / {context.assumption_id}"
                    if context.thesis_revision_id
                    else "No specific assumption"
                ),
                "Prepared durable source references: "
                + json.dumps(
                    [ref.model_dump(mode="json") for ref in context.source_refs], sort_keys=True
                ),
                "Use read tools to inspect these records; "
                "these references are not facts or authorizations.",
                "Question text:",
                question.text,
                "Evidence requirements:",
                *(f"- {item}" for item in question.evidence_requirements),
                "Do not answer or close the saved question automatically. "
                "Return a reviewable answer or a specific next research step.",
            ]
        )

    def _cache(self, context: QuestionResearchContext, prompt: str) -> dict[str, Any]:
        if len(prompt.encode()) > 32_000:
            raise DataContractError("Question context is too large. Narrow its requirements first.")
        now = self._clock.now()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prepared = {k: v for k, v in self._prepared.items() if v[2] > now}
            if len(self._prepared) >= 64:
                del self._prepared[next(iter(self._prepared))]
            self._prepared[token] = (context, prompt, now + timedelta(minutes=20))
        return {
            "question_context_token": token,
            "context": context.model_dump(mode="json"),
            "prompt": prompt,
            "expires_at": (now + timedelta(minutes=20)).isoformat(),
        }

    def prepare(
        self,
        subject_id: str,
        question_id: str,
        version: int,
        thesis_revision_id: str | None = None,
        assumption_id: str | None = None,
    ) -> dict[str, Any]:
        now = self._clock.now()
        if (thesis_revision_id is None) != (assumption_id is None):
            raise DataContractError("Select both a revision and assumption")
        with self._uow() as uow:
            question = self._question(uow, subject_id, question_id, version)
            if question.asked_at > now:
                raise DataContractError("Question is not yet visible")
            try:
                sources = uow.research_sources.list_for_subject(subject_id, as_of=now, limit=8)
                coverage = "PARTIAL" if len(sources) > 8 else "COMPLETE"
            except Exception:  # noqa: BLE001 - failed reads do not manufacture evidence
                sources, coverage = (), "UNAVAILABLE"
            source_refs = []
            for source in sources[:8]:
                try:
                    source_refs.append(ResearchSourcePayload.model_validate(source.source))
                except ValidationError:
                    coverage = "PARTIAL"
            context = QuestionResearchContext(
                subject_id=subject_id,
                question_id=question_id,
                question_version=version,
                question_fingerprint=self._fingerprint(question),
                evidence_cutoff=now.isoformat(),
                thesis_revision_id=thesis_revision_id,
                assumption_id=assumption_id,
                source_refs=tuple(source_refs),
                source_coverage=coverage,
            )
            self._target(uow, context)
            prompt = self._prompt(question, context)
        return self._cache(context, prompt)

    def restore(self, context: QuestionResearchContext, prompt: str) -> dict[str, Any]:
        """Explicit retry restores the original cutoff, never a new question version."""
        with self._uow() as uow:
            question = self._question(
                uow, context.subject_id, context.question_id, context.question_version
            )
            self._target(uow, context)
            if (
                self._fingerprint(question) != context.question_fingerprint
                or self._prompt(question, context) != prompt
            ):
                raise DataContractError(
                    "The original question context changed. Prepare Research again."
                )
        return self._cache(context, prompt)

    def resolve(self, token: str, prompt: str) -> QuestionResearchContext:
        with self._lock:
            prepared = self._prepared.get(token)
        if prepared is None or prepared[2] <= self._clock.now():
            raise DataContractError("Question Research context expired. Prepare it again.")
        context, expected_prompt, _ = prepared
        if prompt != expected_prompt:
            raise DataContractError("The prepared question prompt changed. Prepare it again.")
        with self._uow() as uow:
            question = self._question(
                uow, context.subject_id, context.question_id, context.question_version
            )
            self._target(uow, context)
            if self._fingerprint(question) != context.question_fingerprint:
                raise DataContractError("Question content changed. Prepare Research again.")
        return context

    def sources_for_result(self, context: QuestionResearchContext) -> dict[str, Any]:
        sources, status = [], "CURRENT"
        with self._uow() as uow:
            try:
                question = self._question(
                    uow, context.subject_id, context.question_id, context.question_version
                )
                self._target(uow, context)
                if self._fingerprint(question) != context.question_fingerprint:
                    status = "CONTEXT_CHANGED"
            except DataContractError:
                status = "CONTEXT_CHANGED"
            for ref in context.source_refs:
                try:
                    source = uow.research_sources.get(
                        context.subject_id,
                        ref.to_domain(),
                        as_of=datetime.fromisoformat(context.evidence_cutoff),
                    )
                except Exception:  # noqa: BLE001 - failed sources remain unavailable
                    source = None
                if source is not None:
                    sources.append(asdict(source))
            return {
                "context": context.model_dump(mode="json"),
                "context_status": status,
                "sources": sources,
                "sources_complete": len(sources) == len(context.source_refs),
            }
