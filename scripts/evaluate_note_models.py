#!/usr/bin/env python3
"""Resumable, owner-only note extraction comparisons; no application writes.

Default is a dry run. Live calls require --live, and an external/private corpus
additionally requires --authorize-private. Reports never contain raw provider errors.
Semantic fidelity is reviewed against the source, not by keyword scores or voting.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import logging
import os
import re
import stat
import time
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, is_dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from application.services.external_note_interpretation_service import (
    ExternalNoteInterpretationService,
)
from bootstrap import load_settings
from domain.common.errors import TradingPartnerError
from domain.external_note.attribution import attributed_blocks
from domain.external_note.enums import NoteCoverage
from domain.external_note.models import ExternalNoteRevision
from infrastructure.providers.llm.note_evaluation_protocol import (
    EvaluationProtocolProvider,
    profile_for,
)
from infrastructure.providers.llm.opencode_cli import NOTE_CLI_MODELS, OpenCodeCliNoteProvider
from infrastructure.providers.llm.opencode_go import (
    OpenCodeGoModelProvider,
    OpenCodeZenModelProvider,
)
from infrastructure.system.clock import SystemClock
from infrastructure.system.id_generator import Uuid7IdGenerator

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "tests/fixtures/note-interpretation-cases.v1.json"
SAFE_ID = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")
EFFORTS = {"low", "medium", "high", "xhigh", "max", "provider_default"}
REPAIR_ERROR = re.compile(r"^上一次提交未被接受（(NOTE_INTERPRETATION_[A-Z0-9_]{1,108})）")
TRANSPORT_ERRORS = {
    "ConnectError",
    "ReadError",
    "WriteError",
    "CloseError",
    "ProxyError",
    "RemoteProtocolError",
    "LocalProtocolError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "UnsupportedProtocol",
    "TooManyRedirects",
}


def safe_failure_details(error: TradingPartnerError) -> dict:
    """Closed diagnostics only: never copy arbitrary error text or details."""
    details = error.details
    result = {"retryable": error.retryable is True}
    status = details.get("status_code")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    attempts = details.get("attempts")
    if type(attempts) is int and 1 <= attempts <= 10:
        result["transport_attempts"] = attempts
    if "error_type" in details:
        kind = details["error_type"]
        result["transport_error"] = (
            kind if isinstance(kind, str) and kind in TRANSPORT_ERRORS else "OTHER"
        )
    return result


def retryable_result(record: dict) -> bool:
    if record.get("error_code") not in {
        "PROVIDER_TIMEOUT_ERROR",
        "PROVIDER_UNAVAILABLE_ERROR",
        "PROVIDER_RATE_LIMIT_ERROR",
    }:
        return False
    failures = record.get("failure_diagnostics", [])
    return not failures or failures[-1].get("retryable") is True


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=lambda v: asdict(v) if is_dataclass(v) else str(v),
        ).encode()
    ).hexdigest()


def private_path(path: Path, *, directory: bool = False) -> None:
    mode = path.stat()
    if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise ValueError("Private files/directories must be owner-only")
    if directory != path.is_dir():
        raise ValueError("Unexpected private path type")


def load_corpus(path: Path, *, authorized: bool = False) -> dict:
    path = path.resolve()
    external = not path.is_relative_to(ROOT)
    if external and not authorized:
        raise ValueError("External corpus requires --authorize-private before reading")
    if external:
        private_path(path)
        private_path(path.parent, directory=True)
    data = json.loads(path.read_text())
    if data.get("version") != 1 or data.get("classification") not in {"synthetic", "private"}:
        raise ValueError("Unsupported corpus schema")
    if data["classification"] == "private" and (not external or not authorized):
        raise ValueError("Private corpus must be authorized and outside the repository")
    cases = data.get("cases", [])
    if not cases or len(cases) > 100:
        raise ValueError("Corpus must contain 1..100 cases")
    ids = set()
    for case in cases:
        cid = case.get("id", "")
        if not SAFE_ID.fullmatch(cid) or cid in ids:
            raise ValueError("Case IDs must be unique safe identifiers")
        ids.add(cid)
        if not isinstance(case.get("body"), str) or not case["body"].strip():
            raise ValueError("Every case needs complete source text")
        if not case.get("rubric") or not all(isinstance(x, str) for x in case["rubric"]):
            raise ValueError("Every case needs human review criteria")
    return data


def variant(value: str, *, allow_contributor: bool = False) -> tuple[str, str]:
    model, effort = value.rsplit(":", 1)
    if (
        not SAFE_ID.fullmatch(model)
        or effort not in EFFORTS
        or ("contributor" in model and not allow_contributor)
    ):
        raise ValueError("Invalid/unsupported variant; Contributor models are excluded")
    return model, effort


def mechanical_checks(payload: dict, case: dict) -> dict:
    """Only declared exact action contracts; never a semantic correctness score."""
    actual = {s["scenario"]: s["action"] for s in payload.get("user_scenarios", [])}
    expected = case.get("expected_actions", {})
    failures = [
        scenario for scenario, allowed in expected.items() if actual.get(scenario) not in allowed
    ]
    return {
        "checked": len(expected),
        "failed_scenarios": failures,
        "semantic_fidelity": "REQUIRES_SOURCE_REVIEW",
    }


def write_json(path: Path, value: object) -> None:
    # Never expose a partially written checkpoint or replace a symlink target.
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def code_fingerprint() -> str:
    files = [
        ROOT / "src/application/services/external_note_interpretation_service.py",
        ROOT / "src/domain/external_note/attribution.py",
        Path(__file__),
    ]
    files += sorted((ROOT / "src/infrastructure/providers/llm").rglob("*.py"))
    return digest(
        {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
    )


def prepare_run(path: Path, manifest: dict) -> None:
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Evaluation output must be outside the repository")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_path(path, directory=True)
    target = path / "manifest.json"
    if target.exists():
        if json.loads(target.read_text()) != manifest:
            raise ValueError("Run inputs changed; choose a new run directory")
    else:
        write_json(target, manifest)


def summarize(path: Path) -> dict:
    rows = [json.loads(f.read_text()) for f in path.glob("result-*.json")]
    manifest = json.loads((path / "manifest.json").read_text())
    review_path = path / "reviews.json"
    reviews = json.loads(review_path.read_text()).get("results", {}) if review_path.exists() else {}
    planned = len(manifest["cases"]) * manifest["repeats"]
    groups = {}
    for model, effort in manifest["variants"]:
        selected = [r for r in rows if (r["model"], r["effort"]) == (model, effort)]
        by_job = {}
        for row in sorted(selected, key=lambda r: r["attempt"]):
            by_job.setdefault(row["job_id"], []).append(row)
        completed = {
            j: next((r for r in reversed(rs) if r["status"] == "SUCCEEDED"), None)
            for j, rs in by_job.items()
        }
        success = {j: r for j, r in completed.items() if r is not None}
        failure_codes = {}
        for row in selected:
            if row["status"] != "SUCCEEDED":
                code = row.get("error_code") or "UNKNOWN"
                failure_codes[code] = failure_codes.get(code, 0) + 1
        usable = sum(
            reviews.get(j, {}).get("verdict") == "PASS" and not r["mechanical"]["failed_scenarios"]
            for j, r in success.items()
        )
        unreviewed = sum(reviews.get(j, {}).get("verdict") not in {"PASS", "FAIL"} for j in success)
        groups[model + ":" + effort] = {
            "planned_jobs": planned,
            "calls": len(selected),
            "completed_jobs": len(success),
            "incapable_jobs": len(by_job) - len(success),
            "pending_jobs": planned - len(by_job),
            "failed_attempts": sum(failure_codes.values()),
            "failure_codes": failure_codes,
            "recovered_jobs": sum(
                any(r["status"] != "SUCCEEDED" for r in by_job[j]) for j in success
            ),
            "seconds": [r["seconds"] for r in success.values()],
            "failed_exact_checks": sum(
                len(r["mechanical"]["failed_scenarios"]) for r in success.values()
            ),
            "semantic_failures": sum(reviews.get(j, {}).get("verdict") == "FAIL" for j in success),
            "review_pending": unreviewed,
            "verified_usable_jobs": usable,
            "end_to_end_pass_fraction": f"{usable}/{planned}",
            "assessment_complete": not unreviewed and len(by_job) == planned,
        }
    return {
        "groups": groups,
        "policy": "Timeout, provider failure and invalid output count as "
        "incapability. Never remove them from the planned-job denominator. Returned content "
        "needs source review; end-to-end pass fractions are provisional until review completes.",
    }


def error_ledger(path: Path) -> dict:
    """Index every saved failure and repaired error without copying model payloads."""
    events = []
    rows = [json.loads(f.read_text()) for f in sorted(path.glob("result-*.json"))]
    for row in rows:
        reference = {
            key: row.get(key)
            for key in ("job_id", "case", "model", "effort", "attempt", "started_at")
        }
        if row["status"] != "SUCCEEDED":
            events.append(
                {
                    **reference,
                    "kind": "ATTEMPT_FAILED",
                    "error_code": row.get("error_code") or "UNKNOWN",
                    "seconds": row.get("seconds"),
                }
            )
        for failure in row.get("failure_diagnostics", []):
            safe = {
                key: failure[key]
                for key in (
                    "provider_call",
                    "retryable",
                    "http_status",
                    "transport_attempts",
                    "transport_error",
                )
                if key in failure
            }
            events.append({**reference, "kind": "PROVIDER_CALL_FAILED", **safe})
        for failure in row.get("repair_errors", []):
            events.append(
                {
                    **reference,
                    "kind": "OUTPUT_REJECTED_BEFORE_REPAIR",
                    "provider_call": failure["provider_call"],
                    "error_code": failure["error_code"],
                    "attempt_recovered": row["status"] == "SUCCEEDED",
                }
            )
    reviews = path / "reviews.json"
    if reviews.exists():
        for job_id, review in json.loads(reviews.read_text()).get("results", {}).items():
            if review.get("verdict") == "FAIL":
                events.append(
                    {
                        "job_id": job_id,
                        "kind": "CONTENT_REVIEW_FAILED",
                        "findings": review.get("findings", []),
                    }
                )
            for finding in review.get("auxiliary_findings", []):
                events.append(
                    {
                        "job_id": job_id,
                        "kind": "AUXILIARY_CONTENT_DEFECT",
                        "finding": finding,
                    }
                )
    return {
        "classification": "private",
        "events": events,
        "policy": "Events may describe the same failed attempt at different layers; "
        "never add event counts to obtain failed-job counts. Raw provider responses, "
        "exception messages, prompts and secrets are excluded. Content review remains separate.",
    }


@contextmanager
def run_lock(path: Path):
    path = path.resolve()
    if path.is_relative_to(ROOT):
        raise ValueError("Evaluation output must be outside the repository")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_path(path, directory=True)
    fd = os.open(path / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


class ObservedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.calls = 0
        self.usage = []
        self.response_models = []
        self.request_hashes = []
        self.failures = []
        self.repair_errors = []
        self.request_snapshots = []
        self.response_snapshots = []

    async def complete(self, request):
        self.calls += 1
        self.request_snapshots.append(asdict(request))
        if self.calls > 1 and request.messages:
            message = request.messages[-1]
            content = message.get("content") if isinstance(message, dict) else message.content
            if isinstance(content, str) and (match := REPAIR_ERROR.match(content)):
                self.repair_errors.append(
                    {"provider_call": self.calls - 1, "error_code": match.group(1)}
                )
        self.request_hashes.append(
            digest(
                {
                    "messages": request.messages,
                    "tools": request.tools,
                    "effort": request.reasoning_effort,
                }
            )
        )
        try:
            response = await self.provider.complete(request)
        except TradingPartnerError as error:
            self.failures.append({"provider_call": self.calls, **safe_failure_details(error)})
            raise
        self.usage.append(asdict(response.usage) if response.usage else None)
        self.response_models.append(response.model)
        self.response_snapshots.append(asdict(response))
        return response


async def evaluate(args) -> None:
    corpus = load_corpus(args.corpus, authorized=args.authorize_private)
    training_authorized = getattr(args, "authorize_contributor_training", False)
    variants = list(
        dict.fromkeys(variant(x, allow_contributor=training_authorized) for x in args.variant)
    )
    provider_kind = getattr(args, "provider", "opencode_go")
    documented = getattr(args, "documented_protocols", False)
    if documented:
        if args.concurrency != 1 or args.max_retries != 0:
            raise ValueError(
                "Selected-model protocol requires serial execution and no outer retries"
            )
        for model, effort in variants:
            if profile_for(provider_kind, model)["effort"] != effort:
                raise ValueError("Requested effort must match the frozen documented profile")
        if (
            args.live
            and provider_kind == "opencode_go"
            and not getattr(args, "confirm_go_extra_usage_disabled", False)
        ):
            raise ValueError("Confirm Go extra usage is disabled before this selected-model run")
    if provider_kind == "opencode_cli":
        if any(m not in NOTE_CLI_MODELS or e != "provider_default" for m, e in variants):
            raise ValueError(
                "CLI evaluation requires allowlisted models and provider_default effort"
            )
        if getattr(args, "direct", False):
            raise ValueError("CLI evaluation uses the production proxy policy; --direct is Go-only")
    elif not documented and any(e == "provider_default" for _, e in variants):
        raise ValueError("Go evaluation requires an explicit reasoning effort")
    selected = set(args.cases.split(",")) if args.cases else {c["id"] for c in corpus["cases"]}
    cases = [c for c in corpus["cases"] if c["id"] in selected]
    if not cases or selected != {c["id"] for c in cases} or not variants:
        raise ValueError("Select known cases and at least one variant")
    if not 1 <= args.repeats <= 10 or not 1 <= args.concurrency <= 6:
        raise ValueError("Repeats must be 1..10 and concurrency 1..6")
    if not 1 <= args.timeout <= 300:
        raise ValueError("Timeout must be 1..300 seconds per attempt")
    if not 0 <= args.max_retries <= 3:
        raise ValueError("Max retries must be 0..3")
    manifest = {
        "version": 1,
        "provider": provider_kind,
        "documented_protocols": documented,
        "contributor_training_authorized": training_authorized,
        "go_extra_usage_disabled_confirmed": getattr(
            args, "confirm_go_extra_usage_disabled", False
        ),
        "max_retries": args.max_retries,
        "corpus_hash": digest(corpus),
        "code_hash": code_fingerprint(),
        "classification": corpus["classification"],
        "variants": [list(v) for v in variants],
        "cases": sorted(selected),
        "repeats": args.repeats,
        "timeout": args.timeout,
        "concurrency": args.concurrency,
        "stop_on_rate_limit": getattr(args, "stop_on_rate_limit", True),
    }
    jobs = [
        (case, model, effort, repeat)
        for repeat in range(1, args.repeats + 1)
        for case in cases
        for model, effort in variants
    ]
    if not args.live:
        print(
            json.dumps(
                {"dry_run": True, "jobs": len(jobs), "variants": variants, "case_count": len(cases)}
            )
        )
        return
    if not args.run_dir:
        raise ValueError("--run-dir is required for live evaluation")
    settings = load_settings()
    direct = getattr(args, "direct", False)
    proxy_url = None if direct else settings.provider_proxy_url
    config = settings.resolved_external_note_analysis_config
    if provider_kind == "opencode_zen":
        config = settings.resolved_agent_llm_configs.get("opencode_zen")
    if config is None and provider_kind != "opencode_cli":
        raise ValueError("Note provider is not configured")
    manifest["network"] = {
        "endpoint_hash": digest(config.base_url) if config is not None else None,
        "explicit_proxy": bool(proxy_url),
        "trust_env": not direct,
    }
    if provider_kind == "opencode_cli":
        manifest["cli_executable"] = str(
            Path(getattr(args, "cli_executable", None) or settings.external_note_cli_executable)
        )
        manifest["cli_policy"] = (
            "Production isolated CLI adapter; 32000 reasoning-model output budget"
        )
    prepare_run(args.run_dir, manifest)
    # Private snapshots make each experiment independently reviewable even after
    # the source checkout or shared corpus changes. Never print these snapshots.
    if not (args.run_dir / "corpus.json").exists():
        write_json(args.run_dir / "corpus.json", corpus)
        snapshot = args.run_dir / "source"
        snapshot.mkdir(mode=0o700)
        for source in (
            Path(__file__),
            ROOT / "src/application/services/external_note_interpretation_service.py",
            ROOT / "src/infrastructure/providers/llm/opencode_cli.py",
            ROOT / "src/infrastructure/providers/llm/note_evaluation_protocol.py",
            ROOT / "src/domain/external_note/attribution.py",
        ):
            target = snapshot / source.name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(source.read_bytes())
    if provider_kind == "opencode_cli":
        supported = {m: ("provider_default",) for m, _ in variants}
    else:
        provider_type = (
            OpenCodeZenModelProvider if provider_kind == "opencode_zen" else OpenCodeGoModelProvider
        )
        directory_provider = provider_type(config, proxy_url=proxy_url, trust_env=not direct)
        try:
            catalog = await directory_provider.list_models(force_refresh=True)
        finally:
            await directory_provider.aclose()
        supported = {item.id: item.reasoning_efforts for item in catalog.models}
    for model, effort in variants:
        if model not in supported or (not documented and effort not in supported[model]):
            raise ValueError("Selected model/effort absent from current adapter catalog")
    write_json(
        args.run_dir / "catalog.json",
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "selected": {m: supported[m] for m, _ in variants},
            "frozen_profiles": {m: profile_for(provider_kind, m) for m, _ in variants}
            if documented
            else {},
            "caveat": (
                "CLI allowlist only; availability/version need separate public metadata evidence."
                if provider_kind == "opencode_cli"
                else "Advertised by adapter; upstream internal reasoning is not observable."
            ),
        },
    )
    reviews = args.run_dir / "reviews.json"
    if not reviews.exists():
        write_json(
            reviews,
            {
                "status": "PENDING",
                "criteria": {c["id"]: c["rubric"] for c in cases},
                "results": {},
                "instructions": "Key results by job ID; record supported "
                "findings, omissions, unsupported claims and source citations. "
                "Do not score failures as semantic errors.",
            },
        )
    semaphore = asyncio.Semaphore(args.concurrency)
    rate_limited = asyncio.Event()
    stopped_models: set[str] = (
        {path.stem.removeprefix("stopped-") for path in args.run_dir.glob("stopped-*.json")}
        if documented
        else set()
    )
    shared_client = httpx.AsyncClient(
        proxy=proxy_url,
        trust_env=not direct,
        timeout=httpx.Timeout(args.timeout, connect=15, write=30, pool=15),
        follow_redirects=False,
    )

    async def run_once(case, model, effort, repeat, *, retry=False):
        job_id = digest([case["id"], model, effort, repeat])[:24]
        previous = sorted(args.run_dir.glob(f"result-{job_id}-*.json"))
        if previous and (
            not (args.retry_failed or retry)
            or any(json.loads(f.read_text())["status"] == "SUCCEEDED" for f in previous)
        ):
            return
        if len(previous) >= 1 + args.max_retries:
            return None
        if previous and not retryable_result(json.loads(previous[-1].read_text())):
            return None
        attempt = len(previous) + 1
        async with semaphore:
            if rate_limited.is_set() or model in stopped_models:
                return None
            if provider_kind == "opencode_cli":
                provider = OpenCodeCliNoteProvider(
                    model=model,
                    executable=manifest["cli_executable"],
                    timeout_seconds=args.timeout,
                    proxy_url=proxy_url,
                )
            elif documented:
                provider = EvaluationProtocolProvider(
                    provider=provider_kind,
                    model=model,
                    api_key=config.api_key,
                    client=shared_client,
                )
            else:
                provider_type = (
                    OpenCodeZenModelProvider
                    if provider_kind == "opencode_zen"
                    else OpenCodeGoModelProvider
                )
                provider = provider_type(
                    replace(
                        config, model=model, timeout_seconds=args.timeout, reasoning_effort=effort
                    ),
                    proxy_url=proxy_url,
                    trust_env=not direct,
                )
            observed = ObservedProvider(provider)
            service = ExternalNoteInterpretationService(
                observed,
                provider_name=provider_kind,
                model=model,
                clock=SystemClock(),
                id_generator=Uuid7IdGenerator(),
                timeout_seconds=args.timeout,
                reasoning_effort=effort,
            )
            body = case["body"]
            fixed = datetime.fromisoformat(case.get("observed_at", "2026-01-01T00:00:00+00:00"))
            revision = ExternalNoteRevision(
                note_revision_id="external_note_revision_eval_" + job_id,
                note_id="external_note_eval_" + case["id"],
                version=1,
                content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                source_revision_key="evaluation:" + case["id"],
                title=case.get("title", "Evaluation"),
                summary="",
                full_body=body,
                coverage=NoteCoverage.FULL,
                source_timestamp=fixed,
                observed_at=fixed,
                visibility="SELF",
                related_provider_stock_ids=(),
                related_provider_codes=(),
                blocks=attributed_blocks(body),
            )
            started = time.monotonic()
            record = {
                "job_id": job_id,
                "case": case["id"],
                "model": model,
                "effort": effort,
                "repeat": repeat,
                "attempt": attempt,
                "started_at": datetime.now(UTC).isoformat(),
            }
            try:
                context = case.get("interpretation_context")
                result = await service.analyze(
                    revision,
                    case.get("previous_payload_json"),
                    **({"interpretation_context": context} if context is not None else {}),
                )
                payload = json.loads(result.payload_json) if result.status == "SUCCEEDED" else None
                record.update(
                    status=result.status,
                    error_code=result.error_code,
                    payload=payload,
                    recorded_effort=result.reasoning_effort,
                    mechanical=mechanical_checks(payload, case) if payload else None,
                )
            except Exception as exc:  # noqa: BLE001 - do not log provider messages or private text
                record.update(status="ERROR", error_code=type(exc).__name__, payload=None)
            finally:
                await provider.aclose()
            record.update(
                finished_at=datetime.now(UTC).isoformat(),
                seconds=round(time.monotonic() - started, 2),
                provider_calls=observed.calls,
                usage=observed.usage,
                response_models=observed.response_models,
                request_hashes=observed.request_hashes,
                failure_diagnostics=observed.failures,
                repair_errors=observed.repair_errors,
                request_snapshots=observed.request_snapshots,
                response_snapshots=observed.response_snapshots,
                wire_request_snapshots=getattr(provider, "wire_request_snapshots", []),
            )
            write_json(args.run_dir / f"result-{job_id}-{attempt:03d}.json", record)
            if documented and record.get("error_code") in {
                "PROVIDER_AUTHENTICATION_ERROR",
                "PROVIDER_REQUEST_REJECTED",
            }:
                stopped_models.add(model)
                write_json(
                    args.run_dir / ("stopped-" + model + ".json"),
                    {
                        "error_code": record["error_code"],
                        "job_id": job_id,
                        "policy": "Stop model after access/configuration rejection; "
                        "keep pending cases.",
                    },
                )
            if (
                manifest["stop_on_rate_limit"]
                and record.get("error_code") == "PROVIDER_RATE_LIMIT_ERROR"
                and not rate_limited.is_set()
            ):
                rate_limited.set()
                write_json(
                    args.run_dir / "pause.json",
                    {
                        "status": "BLOCKED_PROVIDER_RATE_LIMIT",
                        "recorded_at": datetime.now(UTC).isoformat(),
                        "trigger_job_id": job_id,
                        "trigger_attempt": attempt,
                        "policy": "Stop dispatching new jobs and retries; let active calls finish. "
                        "Preserve all results and remaining attempt budgets.",
                    },
                )
            print(
                json.dumps(
                    {k: record[k] for k in ("job_id", "model", "effort", "status", "seconds")}
                ),
                flush=True,
            )

            return record

    async def run(case, model, effort, repeat):
        for retry in range(args.max_retries + 1):
            record = await run_once(case, model, effort, repeat, retry=retry > 0)
            if record is None or not retryable_result(record):
                return
            if rate_limited.is_set():
                return
            if record["attempt"] >= args.max_retries + 1:
                return
            await asyncio.sleep(2 ** (record["attempt"] - 1))

    try:
        await asyncio.gather(*(run(*job) for job in jobs))
    finally:
        await shared_client.aclose()
    write_json(args.run_dir / "summary.json", summarize(args.run_dir))
    write_json(args.run_dir / "errors.json", error_ledger(args.run_dir))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--variant", action="append", default=[], metavar="MODEL:EFFORT")
    parser.add_argument(
        "--provider", choices=("opencode_go", "opencode_zen", "opencode_cli"), default="opencode_go"
    )
    parser.add_argument("--documented-protocols", action="store_true")
    parser.add_argument("--authorize-contributor-training", action="store_true")
    parser.add_argument("--confirm-go-extra-usage-disabled", action="store_true")
    parser.add_argument("--cli-executable", default=None)
    parser.add_argument("--cases", default="")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--authorize-private", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--direct", action="store_true", help="Diagnostic run without any proxy")
    parser.add_argument(
        "--continue-on-rate-limit",
        dest="stop_on_rate_limit",
        action="store_false",
        default=True,
        help="Opt out of the shared rate-limit dispatch stop",
    )
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        with run_lock(args.run_dir) if args.live and args.run_dir else nullcontext():
            asyncio.run(evaluate(args))
    except Exception as exc:  # noqa: BLE001 - no raw errors, URLs, credentials or note text
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
