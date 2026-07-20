"""US research business enums (Phase 1G G1).

Wire-format values are the member ``value`` strings. Once exposed in Tool Schema
they must not change without a migration.
"""

from enum import StrEnum


class USStatementType(StrEnum):
    """Financial statement type (design §3)."""

    INCOME = "income"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"


class USStatementFrequency(StrEnum):
    """Statement reporting frequency (design §3)."""

    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class USFundamentalBasis(StrEnum):
    """Fundamental metric reporting basis (design §3)."""

    CURRENT = "current"
    TTM = "ttm"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class USFilingForm(StrEnum):
    """SEC form types supported in Phase 1G (design §3)."""

    FORM_10K = "10-K"
    FORM_10Q = "10-Q"
    FORM_8K = "8-K"
    DEF_14A = "DEF 14A"
    FORM_4 = "4"
    S_1 = "S-1"
    SC_13D = "SC 13D"
    SC_13G = "SC 13G"


class USCorporateActionType(StrEnum):
    """US corporate action kinds (design §3)."""

    DIVIDEND = "dividend"
    SPLIT = "split"
    SHARE_ISSUANCE = "share_issuance"


class USInsiderAcquiredDisposed(StrEnum):
    """Form 4 acquired/disposed code (design §3)."""

    ACQUIRED = "acquired"
    DISPOSED = "disposed"


class USExternalEventType(StrEnum):
    """Unified company-event kinds covered by 1G (design §3 / §6)."""

    FILING = "filing"
    INSIDER_TRANSACTION = "insider_transaction"
    CORPORATE_ACTION = "corporate_action"
    NEWS = "news"
