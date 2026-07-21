"""Phase 1H US context business enums."""

from enum import StrEnum


class USNewsScope(StrEnum):
    COMPANY = "company"
    GLOBAL = "global"


class USSentimentDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class USSentimentLabelOrigin(StrEnum):
    USER_LABEL = "user_label"
    DETERMINISTIC_INFERENCE = "deterministic_inference"


class USSentimentSource(StrEnum):
    STOCKTWITS = "stocktwits"
    REDDIT = "reddit"
    MOOMOO = "moomoo"
    YAHOO = "yahoo"
    ALPHA_VANTAGE = "alpha_vantage"
