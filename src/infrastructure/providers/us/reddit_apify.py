"""Harsh Maur Apify client for bounded Reddit search fallback."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final
from urllib.parse import urlencode

from application.ports.clock import Clock
from application.ports.http_transport import HttpRequest, HttpResponse, HttpTransport
from domain.common.errors import (
    DataContractError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from domain.instruments.models import Instrument

_ACTOR_ID: Final = "harshmaur/reddit-scraper"
_ACTOR_PATH_ID: Final = "harshmaur~reddit-scraper"
_API_ROOT: Final = "https://api.apify.com/v2"
_TERMINAL_STATUSES: Final = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
_AMBIGUOUS_SYMBOLS: Final = frozenset(
    {"A", "AI", "ALL", "ARE", "CAN", "CAR", "FOR", "IT", "ON", "OR", "SO", "T", "U"}
)


@dataclass(frozen=True, slots=True)
class ApifyRedditPost:
    post_id: str
    subreddit: str
    title: str
    body: str
    published_at: datetime
    url: str | None
    score: int | None
    comments: int | None


def _json_object(response: HttpResponse, *, operation: str) -> Mapping[str, object]:
    try:
        value = json.loads(response.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise DataContractError(f"Apify {operation} response is not valid JSON") from None
    if not isinstance(value, Mapping):
        raise DataContractError(f"Apify {operation} response must be an object")
    return value


def _data_object(response: HttpResponse, *, operation: str) -> Mapping[str, object]:
    value = _json_object(response, operation=operation).get("data")
    if not isinstance(value, Mapping):
        raise DataContractError(f"Apify {operation} response is missing data")
    return value


def _bounded_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


class ApifyRedditClient:
    """Run one bounded Actor job for one or more instruments and classify locally."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        api_token: str,
        actor_id: str,
        subreddits: tuple[str, ...],
        lookback_days: Mapping[str, int],
        max_charge_usd: Decimal,
        clock: Clock,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 1.0,
        max_posts_per_run: int = 200,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not api_token.strip():
            raise DataContractError("Apify API token is required")
        if actor_id != _ACTOR_ID:
            raise DataContractError("unsupported Apify Reddit actor")
        if not subreddits or set(subreddits) != set(lookback_days):
            raise DataContractError("Apify subreddit lookbacks must exactly match subreddits")
        if any(days < 1 or days > 365 for days in lookback_days.values()):
            raise DataContractError("Apify subreddit lookbacks must be between 1 and 365 days")
        if max_charge_usd <= 0:
            raise DataContractError("Apify max charge must be positive")
        if timeout_seconds <= 0 or poll_interval_seconds <= 0 or max_posts_per_run <= 0:
            raise DataContractError("Apify runtime bounds must be positive")
        self._transport = transport
        self._api_token = api_token.strip()
        self._subreddits = subreddits
        self._lookback_days = dict(lookback_days)
        self._max_charge_usd = max_charge_usd
        self._clock = clock
        self._timeout = float(timeout_seconds)
        self._poll_interval = float(poll_interval_seconds)
        self._max_posts = int(max_posts_per_run)
        self._sleep = sleep

    @staticmethod
    def _upstream_time_filter(days: int) -> str:
        if days <= 1:
            return "day"
        if days <= 7:
            return "week"
        if days <= 30:
            return "month"
        if days <= 365:
            return "year"
        return "all"

    @staticmethod
    def _query(instruments: tuple[Instrument, ...]) -> str:
        aliases: list[str] = []
        seen: set[str] = set()
        for instrument in instruments:
            for raw in (instrument.symbol, instrument.name):
                alias = raw.strip()
                folded = alias.casefold()
                if alias and folded not in seen:
                    seen.add(folded)
                    aliases.append(alias)
        return " OR ".join(f'"{alias.replace(chr(34), "")}"' for alias in aliases)

    def _payload(self, instruments: tuple[Instrument, ...]) -> bytes:
        query = self._query(instruments)
        start_urls: list[dict[str, str]] = []
        for subreddit in self._subreddits:
            params = urlencode(
                {
                    "q": query,
                    "restrict_sr": "on",
                    "sort": "new",
                    "t": self._upstream_time_filter(self._lookback_days[subreddit]),
                }
            )
            start_urls.append(
                {"url": f"https://www.reddit.com/r/{subreddit}/search/?{params}"}
            )
        body = {
            "searchTerms": [],
            "startUrls": start_urls,
            "subredditUrls": [],
            "fastMode": True,
            "crawlCommentsPerPost": False,
            "includeNSFW": False,
            "maxPostsCount": self._max_posts,
            "maxCommentsCount": 0,
            "maxCommentsPerPost": 0,
            "maxCommunitiesCount": 0,
            "proxy": {
                "useApifyProxy": True,
                "apifyProxyGroups": ["RESIDENTIAL"],
            },
        }
        return json.dumps(body, separators=(",", ":")).encode()

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> HttpRequest:
        if method not in {"GET", "POST"}:
            raise AssertionError("unsupported Apify method")
        return HttpRequest(
            method=method,  # type: ignore[arg-type]
            url=url,
            params=params or {},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
            body=body,
            timeout_seconds=min(30.0, self._timeout),
        )

    async def _send(self, request: HttpRequest, *, operation: str) -> HttpResponse:
        response = await self._transport.send(request)
        if response.status_code == 429:
            raise ProviderRateLimitError(f"Apify {operation} rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderUnavailableError(f"Apify {operation} HTTP failure")
        return response

    async def _run(self, instruments: tuple[Instrument, ...]) -> list[object]:
        started = await self._send(
            self._request(
                "POST",
                f"{_API_ROOT}/acts/{_ACTOR_PATH_ID}/runs",
                params={"maxTotalChargeUsd": format(self._max_charge_usd, "f")},
                body=self._payload(instruments),
            ),
            operation="run start",
        )
        run = _data_object(started, operation="run start")
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise DataContractError("Apify run start response is missing id")
        deadline = self._clock.now().astimezone(UTC) + timedelta(seconds=self._timeout)
        while True:
            status = run.get("status")
            if isinstance(status, str) and status in _TERMINAL_STATUSES:
                break
            if self._clock.now().astimezone(UTC) >= deadline:
                raise ProviderTimeoutError("Apify Reddit Actor timed out")
            await self._sleep(self._poll_interval)
            polled = await self._send(
                self._request("GET", f"{_API_ROOT}/actor-runs/{run_id}"),
                operation="run poll",
            )
            run = _data_object(polled, operation="run poll")
        if run.get("status") == "TIMED-OUT":
            raise ProviderTimeoutError("Apify Reddit Actor timed out")
        if run.get("status") != "SUCCEEDED":
            raise ProviderUnavailableError("Apify Reddit Actor failed")
        dataset_id = run.get("defaultDatasetId")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise DataContractError("Apify successful run is missing dataset id")
        dataset = await self._send(
            self._request(
                "GET",
                f"{_API_ROOT}/datasets/{dataset_id}/items",
                params={"clean": "true", "format": "json", "limit": "500"},
            ),
            operation="dataset read",
        )
        try:
            items = json.loads(dataset.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise DataContractError("Apify dataset is not valid JSON") from None
        if not isinstance(items, list):
            raise DataContractError("Apify dataset must be a list")
        return items

    def _parse_post(self, raw: object) -> ApifyRedditPost | None:
        if not isinstance(raw, Mapping) or str(raw.get("dataType", "")).casefold() != "post":
            return None
        post_id = raw.get("parsedId") or raw.get("id")
        subreddit = raw.get("parsedCommunityName") or raw.get("communityName")
        title = raw.get("title")
        created = raw.get("createdAt")
        required = (post_id, subreddit, title, created)
        if not all(isinstance(value, str) and value.strip() for value in required):
            return None
        try:
            published_at = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        except ValueError:
            return None
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            return None
        body = raw.get("body")
        url = raw.get("postUrl") or raw.get("url")
        return ApifyRedditPost(
            post_id=str(post_id).strip(),
            subreddit=str(subreddit).strip().lower().removeprefix("r/"),
            title=str(title).strip(),
            body=str(body).strip() if isinstance(body, str) else "",
            published_at=published_at,
            url=str(url)[:2_000] if isinstance(url, str) and url else None,
            score=_bounded_int(raw.get("score") if "score" in raw else raw.get("upVotes")),
            comments=_bounded_int(raw.get("commentsCount")),
        )

    @staticmethod
    def _matches(post: ApifyRedditPost, instrument: Instrument) -> bool:
        text = f"{post.title} {post.body}".casefold()
        aliases = [instrument.name]
        symbol = instrument.symbol.strip()
        if symbol.upper() in _AMBIGUOUS_SYMBOLS or len(symbol) <= 2:
            aliases.append(f"${symbol}")
        else:
            aliases.append(symbol)
        for alias in aliases:
            candidate = alias.strip().casefold()
            if not candidate:
                continue
            start = 0
            while True:
                index = text.find(candidate, start)
                if index < 0:
                    break
                before = text[index - 1] if index else " "
                end = index + len(candidate)
                after = text[end] if end < len(text) else " "
                if not before.isalnum() and not after.isalnum():
                    return True
                start = index + 1
        return False

    async def fetch(
        self, instruments: tuple[Instrument, ...]
    ) -> dict[str, tuple[ApifyRedditPost, ...]]:
        if not instruments or len(instruments) > 10:
            raise DataContractError("Apify Reddit batch must contain 1..10 instruments")
        items = await self._run(instruments)
        now = self._clock.now().astimezone(UTC)
        posts: dict[str, ApifyRedditPost] = {}
        for raw in items:
            post = self._parse_post(raw)
            if post is None or post.subreddit not in self._lookback_days:
                continue
            cutoff = now - timedelta(days=self._lookback_days[post.subreddit])
            if post.published_at.astimezone(UTC) < cutoff or post.published_at > now:
                continue
            posts[post.post_id] = post
        return {
            instrument.instrument_id: tuple(
                sorted(
                    (post for post in posts.values() if self._matches(post, instrument)),
                    key=lambda post: post.published_at,
                    reverse=True,
                )
            )
            for instrument in instruments
        }
