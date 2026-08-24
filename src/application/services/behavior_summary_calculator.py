"""Application seam for the pure Phase 4D behavior calculator.

The calculator intentionally has no repository or Provider dependency.  This
module gives application callers the conventional service-path import while
keeping all behavior logic in the domain package.
"""

from domain.behavior.calculator import BehaviorSummaryCalculator

__all__ = ["BehaviorSummaryCalculator"]
