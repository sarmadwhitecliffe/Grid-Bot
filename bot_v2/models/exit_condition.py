"""
Exit Condition Data Model

Represents an exit signal with priority, amount, and reason.
Used by ExitConditionEngine to communicate exit decisions.
"""

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class ExitCondition:
    """
    Represents a triggered exit condition.

    Used by ExitConditionEngine to communicate which exit was triggered,
    with what priority, and how much should be closed.

    Attributes:
        reason: Exit reason string (e.g., "HardSL", "TrailExit", "TP1a")
        priority: Priority level (lower = higher priority, 1 = most urgent)
        amount: Amount to close (Decimal)
        price: Price at which exit triggered (Decimal)
        message: Human-readable description
    """

    reason: str
    priority: int
    amount: Decimal
    price: Decimal
    message: str

    def __str__(self) -> str:
        return f"{self.reason} (P{self.priority}): {self.message} - Close {self.amount} @ {self.price}"

    def is_full_exit(self, current_amount: Decimal) -> bool:
        """Check if this exit closes the entire position."""
        return self.amount >= current_amount

    def is_partial_exit(self, current_amount: Decimal) -> bool:
        """Check if this exit is a partial close."""
        return Decimal("0") < self.amount < current_amount
