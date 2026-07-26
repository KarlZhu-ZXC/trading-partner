"""Stable Sina A-share adapter façade."""

from __future__ import annotations

from infrastructure.providers.a_share.sina.common import (
    _PAPER_PREFIX,
    _SUPPORTED,
    CacheDisposition,
    Clock,
    DataCategory,
    DataContractError,
    Freshness,
    HttpTransport,
    Mapping,
    Market,
    ProviderNotConfigured,
    ProviderResultMeta,
    SinaHttpClient,
    SourceRole,
    StaleMarketData,
    SystemClock,
    TradingSession,
    VendorId,
    datetime,
    infer_session_basic,
    require_aware_datetime,
    require_nonnegative_exact_int,
)
from infrastructure.providers.a_share.sina.daily_flow import SinaDailyFlowMixin
from infrastructure.providers.a_share.sina.financials import SinaFinancialsMixin
from infrastructure.providers.a_share.sina.options import SinaOptionsMixin


class SinaAShareAdapter(SinaDailyFlowMixin, SinaFinancialsMixin, SinaOptionsMixin):
    """CategoryProvider façade retaining the original public adapter type."""

    def __init__(
        self,
        transport: HttpTransport,
        *,
        clock: Clock | None = None,
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        user_agent: str = "TradingPartner/1.0",
        current_window_seconds: int = 300,
        max_fresh_seconds: int = 15,
        max_delayed_seconds: int = 120,
    ) -> None:
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds <= 0
        ):
            raise DataContractError(
                "timeout_seconds must be a positive number",
                details={"field": "timeout_seconds", "rule": "positive"},
            )
        self._transport = transport
        self._clock = clock if clock is not None else SystemClock()
        self._enabled = bool(enabled)
        self._timeout_seconds = float(timeout_seconds)
        self._user_agent = user_agent
        self._client = SinaHttpClient(transport, user_agent=user_agent)
        self._current_window_seconds = require_nonnegative_exact_int(
            current_window_seconds, field="current_window_seconds"
        )
        if (
            not isinstance(max_fresh_seconds, int)
            or isinstance(max_fresh_seconds, bool)
            or max_fresh_seconds < 0
        ):
            raise DataContractError(
                "max_fresh_seconds must be a nonnegative int",
                details={"field": "max_fresh_seconds", "rule": "nonnegative"},
            )
        if (
            not isinstance(max_delayed_seconds, int)
            or isinstance(max_delayed_seconds, bool)
            or max_delayed_seconds < 0
        ):
            raise DataContractError(
                "max_delayed_seconds must be a nonnegative int",
                details={"field": "max_delayed_seconds", "rule": "nonnegative"},
            )
        if max_fresh_seconds > max_delayed_seconds:
            raise DataContractError(
                "max_fresh_seconds must be <= max_delayed_seconds",
                details={"field": "max_fresh_seconds", "rule": "fresh_le_delayed"},
            )
        self._max_fresh_seconds = max_fresh_seconds
        self._max_delayed_seconds = max_delayed_seconds

    @property
    def vendor_id(self) -> VendorId:
        return VendorId.SINA

    @property
    def provider_name(self) -> str:
        return VendorId.SINA.value

    def supports(self, market: Market, category: DataCategory) -> bool:
        return market is Market.A_SHARE and category in _SUPPORTED

    def is_configured(self) -> bool:
        return self._enabled

    def _require_configured(self) -> None:
        if not self.is_configured():
            raise ProviderNotConfigured(
                "Sina A-share adapter is disabled",
                details={"vendor": self.vendor_id.value},
            )

    def _require_as_of(self, as_of: datetime) -> datetime:
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={"field": "as_of", "rule": "not_future"},
            )
        return now

    def _raise_for_http_status(self, status_code: int, *, operation: str) -> None:
        self._client.require_success(status_code, operation=operation)

    def _require_json_content(self, headers: Mapping[str, str], *, operation: str) -> None:
        self._client.require_json_content(headers, operation=operation)

    def _meta(
        self,
        *,
        category: DataCategory,
        as_of: datetime,
        fetched_at: datetime,
        warnings: tuple[str, ...] = (),
        freshness: Freshness = Freshness.UNKNOWN,
        session: TradingSession | None = None,
        data_delay_seconds: int | None = None,
    ) -> ProviderResultMeta:
        if session is None:
            session = infer_session_basic(
                Market.A_SHARE, as_of, timezone="Asia/Shanghai"
            )
        if not isinstance(session, TradingSession):
            session = TradingSession.UNKNOWN
        return ProviderResultMeta(
            vendor=self.vendor_id,
            category=category,
            role=SourceRole.PRIMARY,
            as_of=as_of,
            fetched_at=fetched_at,
            freshness=freshness,
            session=session,
            latency_ms=None,
            cache_disposition=CacheDisposition.MISS,
            adjustment=None,
            data_delay_seconds=data_delay_seconds,
            warnings=warnings,
        )

    def _require_current_only_as_of(self, as_of: datetime, *, operation: str) -> datetime:
        """Sample clock once; reject future or stale as_of before network."""
        require_aware_datetime(as_of, field_name="as_of")
        now = self._clock.now()
        require_aware_datetime(now, field_name="clock.now")
        if as_of > now:
            raise DataContractError(
                "as_of must not be in the future relative to clock",
                details={
                    "field": "as_of",
                    "rule": "not_future",
                    "operation": operation,
                },
            )
        age = (now - as_of).total_seconds()
        if age > self._current_window_seconds:
            raise StaleMarketData(
                "as_of is outside the supported current window",
                details={
                    "operation": operation,
                    "rule": "current_window",
                    "window_seconds": self._current_window_seconds,
                },
            )
        return now

    def _paper_code(self, code6: str, suffix: str) -> str:
        prefix = _PAPER_PREFIX.get(suffix)
        if prefix is None:
            raise DataContractError(
                "unsupported A-share exchange suffix for Sina",
                details={"field": "symbol", "rule": "exchange_suffix"},
            )
        return f"{prefix}{code6}"

    @staticmethod
    def _ensure_no_body_leak(exc: DataContractError) -> None:
        # Guard against accidental payload embedding in details.
        for key, value in exc.details.items():
            if key in {"body", "raw", "payload", "response"}:
                raise DataContractError(
                    "error details must not embed raw provider payload",
                    details={"field": "details", "rule": "no_body_leak"},
                ) from exc
            if isinstance(value, (bytes, bytearray)):
                raise DataContractError(
                    "error details must not embed raw provider payload",
                    details={"field": "details", "rule": "no_body_leak"},
                ) from exc
