"""Phase 1I account and portfolio enums."""

from enum import StrEnum


class AccountEnvironment(StrEnum):
    REAL = "real"
    MANUAL = "manual"


class AccountPositionSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class AccountOpenOrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AccountOpenOrderStatus(StrEnum):
    PENDING = "pending"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class AccountTransactionSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class AccountTransactionKind(StrEnum):
    TRADE = "trade"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    FEE = "fee"
    TRANSFER = "transfer"
    CORPORATE_ACTION = "corporate_action"
    OTHER = "other"


class AccountActivityCoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
