"""
src/oms/__init__.py
-------------------
Data types for the Order Management System layer.

OrderRecord tracks individual limit orders.
OrderStatus enumerates all possible order states.
RiskAction enumerates the actions the RiskManager can signal to the main loop.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(Enum):
    """All possible states of a tracked grid order."""

    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    PARTIALLY_FILLED = "partial"
    UNKNOWN = "unknown"


class RiskAction(Enum):
    """
    Actions returned by RiskManager to direct the main loop.

    Priority order (most severe first):
    EMERGENCY_CLOSE > STOP_LOSS > TAKE_PROFIT > PAUSE_ADX > RECENTRE > NONE
    """

    NONE = "none"
    PAUSE_ADX = "pause_adx"
    STOP_LOSS = "stop_loss"
    EMERGENCY_CLOSE = "emergency_close"
    TAKE_PROFIT = "take_profit"
    RECENTRE = "recentre"


@dataclass
class OrderRecord:
    """
    Tracks a single open grid limit order.

    Attributes:
        order_id:     Exchange-assigned order identifier.
        grid_price:   The intended grid level price used to place the order.
        side:         'buy' or 'sell'.
        amount:       Quantity in base currency.
        placed_at:    UTC timestamp when the order was placed.
        status:       Current lifecycle status of the order.
        filled_price: Actual fill price (set when status becomes FILLED).
        filled_at:    UTC timestamp of fill (set when status becomes FILLED).
    """

    order_id: str
    grid_price: float
    side: str
    amount: float
    placed_at: datetime = field(default_factory=datetime.utcnow)
    status: OrderStatus = OrderStatus.OPEN
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None
