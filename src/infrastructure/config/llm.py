"""Provider-neutral Agent LLM endpoint configuration.

Only this module knows how the legacy Bailian/DeepSeek settings are converted
to the shared endpoint shape.  The model provider and codecs never branch on a
vendor name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from domain.common.errors import ConfigurationError

LLMApiStyle = Literal["chat_completions", "responses"]
LLMReasoningMode = Literal["none", "effort", "thinking"]
LLMNativeWebSearch = Literal["disabled", "responses_web_search"]
LLMNativeWebExtractor = Literal["disabled", "responses_web_extractor"]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("LLM base URL must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigurationError("LLM base URL must not contain credentials")
    # Query strings/fragments are not part of a stable endpoint contract.  A
    # caller can still put a path (for example ``/v1``) in the configured URL.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True, slots=True)
class LLMEndpointConfig:
    """Fully resolved endpoint settings consumed by infrastructure adapters."""

    api_style: LLMApiStyle
    base_url: str
    api_key: str = field(repr=False)
    model: str
    reasoning_mode: LLMReasoningMode = "none"
    reasoning_effort: str | None = None
    native_web_search: LLMNativeWebSearch = "disabled"
    native_web_extractor: LLMNativeWebExtractor = "disabled"
    timeout_seconds: float = 120.0
    max_output_tokens: int = 8000

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _safe_endpoint(self.base_url))
        for field_name in ("api_key", "model"):
            value = _clean(getattr(self, field_name))
            if value is None:
                raise ConfigurationError(f"LLM {field_name} must be configured")
            object.__setattr__(self, field_name, value)
        if self.api_style not in {"chat_completions", "responses"}:
            raise ConfigurationError("LLM_API_STYLE must be chat_completions or responses")
        if self.reasoning_mode not in {"none", "effort", "thinking"}:
            raise ConfigurationError("LLM_REASONING_MODE must be none, effort, or thinking")
        if self.native_web_search not in {"disabled", "responses_web_search"}:
            raise ConfigurationError(
                "LLM_NATIVE_WEB_SEARCH must be disabled or responses_web_search"
            )
        if self.native_web_search == "responses_web_search" and self.api_style != "responses":
            raise ConfigurationError(
                "LLM_NATIVE_WEB_SEARCH=responses_web_search requires LLM_API_STYLE=responses"
            )
        if self.native_web_extractor not in {"disabled", "responses_web_extractor"}:
            raise ConfigurationError(
                "LLM_NATIVE_WEB_EXTRACTOR must be disabled or responses_web_extractor"
            )
        if self.native_web_extractor == "responses_web_extractor" and (
            self.api_style != "responses"
            or self.native_web_search != "responses_web_search"
        ):
            raise ConfigurationError(
                "LLM_NATIVE_WEB_EXTRACTOR=responses_web_extractor requires Responses web search"
            )
        if self.timeout_seconds <= 0:
            raise ConfigurationError("LLM_TIMEOUT_SECONDS must be positive")
        if self.max_output_tokens <= 0:
            raise ConfigurationError("LLM_MAX_OUTPUT_TOKENS must be positive")
        effort = _clean(self.reasoning_effort)
        object.__setattr__(self, "reasoning_effort", effort)

    @property
    def web_search_enabled(self) -> bool:
        """Compatibility spelling used by older provider settings."""

        return self.native_web_search == "responses_web_search"

    def redacted_dict(self) -> dict[str, object]:
        """Return endpoint metadata safe for logs and diagnostics."""

        return {
            "api_style": self.api_style,
            "base_url": self.base_url,
            "api_key": "***REDACTED***",
            "model": self.model,
            "reasoning_mode": self.reasoning_mode,
            "reasoning_effort": self.reasoning_effort,
            "native_web_search": self.native_web_search,
            "native_web_extractor": self.native_web_extractor,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
        }

    def model_dump_redacted(self) -> dict[str, object]:
        return self.redacted_dict()


def resolve_llm_endpoint_config(
    *,
    generic_explicit: bool,
    api_style: str,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
    reasoning_mode: str,
    reasoning_effort: str | None,
    native_web_search: str,
    native_web_extractor: str,
    timeout_seconds: float,
    max_output_tokens: int,
    legacy_provider: str,
    bailian_api_key: str | None,
    bailian_base_url: str,
    bailian_model: str,
    bailian_web_search_enabled: bool,
    bailian_web_extractor_enabled: bool,
    deepseek_api_key: str | None,
    deepseek_base_url: str,
    deepseek_model: str,
    opencode_zen_api_key: str | None,
    opencode_zen_base_url: str,
    opencode_zen_model: str,
    opencode_go_api_key: str | None,
    opencode_go_base_url: str,
    opencode_go_model: str,
) -> LLMEndpointConfig:
    """Resolve generic settings, or exactly one legacy endpoint.

    ``generic_explicit`` is intentionally supplied by the Settings boundary.
    Once any generic value is supplied, *all* required generic values must be
    present; missing values are not filled from a legacy provider.  This keeps
    a stale BAILIAN/DEEPSEEK key from accidentally being paired with a new
    generic endpoint.
    """

    if generic_explicit:
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", base_url),
                ("LLM_API_KEY", api_key),
                ("LLM_MODEL", model),
            )
            if _clean(value) is None
        ]
        if missing:
            raise ConfigurationError(
                "Generic LLM configuration is incomplete",
                details={"missing": tuple(missing)},
            )
        return LLMEndpointConfig(
            api_style=api_style,  # type: ignore[arg-type]
            base_url=base_url or "",
            api_key=api_key or "",
            model=model or "",
            reasoning_mode=reasoning_mode,  # type: ignore[arg-type]
            reasoning_effort=reasoning_effort,
            native_web_search=native_web_search,  # type: ignore[arg-type]
            native_web_extractor=native_web_extractor,  # type: ignore[arg-type]
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )

    provider = legacy_provider.strip().lower()
    if provider == "bailian":
        if _clean(bailian_api_key) is None:
            raise ConfigurationError("BAILIAN_API_KEY is required for the legacy LLM endpoint")
        return LLMEndpointConfig(
            api_style="responses",
            base_url=bailian_base_url,
            api_key=bailian_api_key or "",
            model=bailian_model,
            reasoning_mode="effort",
            reasoning_effort=reasoning_effort,
            native_web_search="responses_web_search" if bailian_web_search_enabled else "disabled",
            native_web_extractor=(
                "responses_web_extractor"
                if bailian_web_search_enabled and bailian_web_extractor_enabled
                else "disabled"
            ),
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    if provider == "deepseek":
        if _clean(deepseek_api_key) is None:
            raise ConfigurationError("DEEPSEEK_API_KEY is required for the legacy LLM endpoint")
        return LLMEndpointConfig(
            api_style="chat_completions",
            base_url=deepseek_base_url,
            api_key=deepseek_api_key or "",
            model=deepseek_model,
            reasoning_mode="thinking",
            reasoning_effort=reasoning_effort,
            native_web_search="disabled",
            native_web_extractor="disabled",
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    if provider == "opencode_go":
        if _clean(opencode_go_api_key) is None:
            raise ConfigurationError(
                "OPENCODE_GO_API_KEY is required for the OpenCode Go endpoint"
            )
        return LLMEndpointConfig(
            api_style="chat_completions",
            base_url=opencode_go_base_url,
            api_key=opencode_go_api_key or "",
            model=opencode_go_model,
            reasoning_mode="thinking",
            reasoning_effort=reasoning_effort,
            native_web_search="disabled",
            native_web_extractor="disabled",
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    if provider == "opencode_zen":
        if _clean(opencode_zen_api_key) is None:
            raise ConfigurationError(
                "OPENCODE_ZEN_API_KEY is required for the OpenCode Zen endpoint"
            )
        return LLMEndpointConfig(
            api_style="responses",
            base_url=opencode_zen_base_url,
            api_key=opencode_zen_api_key or "",
            model=opencode_zen_model,
            reasoning_mode="effort",
            reasoning_effort=reasoning_effort,
            native_web_search="disabled",
            native_web_extractor="disabled",
            timeout_seconds=timeout_seconds,
            max_output_tokens=max_output_tokens,
        )
    raise ConfigurationError(
        "LLM_PROVIDER must be bailian, deepseek, opencode_zen, or opencode_go"
    )


__all__ = [
    "LLMApiStyle",
    "LLMConfig",
    "LLMEndpointConfig",
    "LLMNativeWebSearch",
    "LLMNativeWebExtractor",
    "LLMReasoningMode",
    "ResolvedLLMConfig",
    "resolve_llm_endpoint_config",
]

# Short aliases keep imports readable for application services while the
# descriptive names remain the canonical public boundary.
LLMConfig = LLMEndpointConfig
ResolvedLLMConfig = LLMEndpointConfig
