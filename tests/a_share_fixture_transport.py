"""Deterministic fixture HTTP transport for A-share contract tests (no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from application.ports.http_transport import HttpRequest, HttpResponse

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "infrastructure"
    / "providers"
    / "a_share"
    / "fixtures"
)

_DEFAULT_JSON_HEADERS = {"content-type": "application/json; charset=utf-8"}
_DEFAULT_TEXT_HEADERS = {"content-type": "text/plain; charset=gbk"}


@dataclass
class FixtureHttpTransport:
    """Maps requests to fixture bodies by vendor/operation case name."""

    vendor: str
    operation: str
    case: str
    requests: list[HttpRequest] = field(default_factory=list)
    status_code_override: int | None = None
    body_override: bytes | None = None
    headers: dict[str, str] | None = None

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        op_dir = FIXTURES_ROOT / self.vendor / self.operation
        meta_path = op_dir / f"{self.case}.meta.json"
        if meta_path.is_file():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            status = int(meta.get("status_code", 200))
            body = str(meta.get("body", "")).encode("utf-8")
            headers = dict(meta.get("headers") or _DEFAULT_TEXT_HEADERS)
            if self.body_override is not None:
                body = self.body_override
            if self.status_code_override is not None:
                status = self.status_code_override
            if self.headers is not None:
                headers = dict(self.headers)
            return HttpResponse(status_code=status, headers=headers, body=body)

        json_path = op_dir / f"{self.case}.json"
        txt_path = op_dir / f"{self.case}.txt"
        if json_path.is_file():
            body = json_path.read_bytes()
            headers = dict(self.headers or _DEFAULT_JSON_HEADERS)
        elif txt_path.is_file():
            body = txt_path.read_bytes()
            headers = dict(self.headers or _DEFAULT_TEXT_HEADERS)
        else:
            raise FileNotFoundError(
                f"missing fixture for {self.vendor}/{self.operation}/{self.case}"
            )
        status = 200 if self.status_code_override is None else self.status_code_override
        if self.body_override is not None:
            body = self.body_override
        return HttpResponse(status_code=status, headers=headers, body=body)


@dataclass
class PathMappedFixtureTransport:
    """Route by URL path to fixture files (multi-endpoint market board)."""

    path_to_fixture: dict[str, Path]
    requests: list[HttpRequest] = field(default_factory=list)
    default_headers: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_JSON_HEADERS))
    status_by_path: dict[str, int] = field(default_factory=dict)

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        path = urlsplit(request.url).path
        # Case-insensitive path match for allowlisted hosts.
        key = path
        fixture: Path | None = self.path_to_fixture.get(key)
        if fixture is None:
            # Try casefold match
            lower_map = {k.casefold(): v for k, v in self.path_to_fixture.items()}
            fixture = lower_map.get(path.casefold())
        if fixture is None:
            raise FileNotFoundError(f"no fixture mapped for path {path!r}")
        body = fixture.read_bytes()
        status = self.status_by_path.get(path, 200)
        return HttpResponse(status_code=status, headers=dict(self.default_headers), body=body)


@dataclass
class ScriptedHttpTransport:
    """Return scripted responses in order; records every request."""

    responses: list[HttpResponse]
    requests: list[HttpRequest] = field(default_factory=list)

    async def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.responses:
            raise RuntimeError("no scripted responses remaining")
        return self.responses.pop(0)


def path_only(url: str) -> str:
    return urlsplit(url).path


def market_board_success_transport() -> PathMappedFixtureTransport:
    root = FIXTURES_ROOT / "eastmoney" / "market_board"
    return PathMappedFixtureTransport(
        path_to_fixture={
            "/api/qt/clist/get": root / "success_equity.json",
            "/getTopicZTPool": root / "success_zt.json",
            "/getTopicDTPool": root / "success_dt.json",
            "/getTopicZBPool": root / "success_zb.json",
        }
        # Industry is a second clist call — Scripted variant needed.
    )


def market_board_success_scripted() -> ScriptedHttpTransport:
    """Ordered responses: equity clist, zt, dt, zb, industry clist."""
    root = FIXTURES_ROOT / "eastmoney" / "market_board"
    headers = dict(_DEFAULT_JSON_HEADERS)

    def _r(name: str) -> HttpResponse:
        return HttpResponse(
            status_code=200,
            headers=headers,
            body=(root / name).read_bytes(),
        )

    return ScriptedHttpTransport(
        responses=[
            _r("success_equity.json"),
            _r("success_zt.json"),
            _r("success_dt.json"),
            _r("success_zb.json"),
            _r("success_industry.json"),
        ]
    )


def market_board_no_data_scripted() -> ScriptedHttpTransport:
    root = FIXTURES_ROOT / "eastmoney" / "market_board"
    headers = dict(_DEFAULT_JSON_HEADERS)
    empty_pool = (root / "empty_pool.json").read_bytes()
    return ScriptedHttpTransport(
        responses=[
            HttpResponse(
                status_code=200,
                headers=headers,
                body=(root / "no_data_equity.json").read_bytes(),
            ),
            HttpResponse(status_code=200, headers=headers, body=empty_pool),
            HttpResponse(status_code=200, headers=headers, body=empty_pool),
            HttpResponse(status_code=200, headers=headers, body=empty_pool),
            HttpResponse(
                status_code=200,
                headers=headers,
                body=(root / "success_industry.json").read_bytes(),
            ),
        ]
    )


def market_board_contract_drift_scripted() -> ScriptedHttpTransport:
    root = FIXTURES_ROOT / "eastmoney" / "market_board"
    headers = dict(_DEFAULT_JSON_HEADERS)
    body = (root / "contract_drift.json").read_bytes()
    return ScriptedHttpTransport(
        responses=[
            HttpResponse(status_code=200, headers=headers, body=body),
        ]
    )
