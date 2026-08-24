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


class ActivityAnnotationStatus(StrEnum):
    """Human classification/link state for one immutable account activity."""

    LINKED_DECISION_PLAN = "LINKED_DECISION_PLAN"
    UNPLANNED = "UNPLANNED"
    CASH_MANAGEMENT = "CASH_MANAGEMENT"
    TRANSFER_OR_CORPORATE_ACTION = "TRANSFER_OR_CORPORATE_ACTION"
    PROVIDER_CORRECTION = "PROVIDER_CORRECTION"


# Compatibility name used by the transaction-link vocabulary.
TransactionDecisionLinkStatus = ActivityAnnotationStatus


class AccountActivityCoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class TradeCycleStatus(StrEnum):
    """Lifecycle of a reconstructed long-only trade cycle."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    UNRESOLVED = "UNRESOLVED"


class TradeCycleQuality(StrEnum):
    """Whether the deterministic cycle facts are complete."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


class TradeCycleClassification(StrEnum):
    """Behavior-statistics classification without inferring user intent."""

    ACTIVE_TRADE = "ACTIVE_TRADE"
    LONG_TERM_INVESTMENT = "LONG_TERM_INVESTMENT"
    HEDGE = "HEDGE"
    CASH_MANAGEMENT = "CASH_MANAGEMENT"
    TRANSFER_OR_ADMIN = "TRANSFER_OR_ADMIN"
    UNCLASSIFIED = "UNCLASSIFIED"
