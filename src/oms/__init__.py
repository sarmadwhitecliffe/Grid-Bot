"""
src/oms/__init__.py
-------------------
Data types for the Order Management System layer.

OrderRecord tracks individual limit orders.
OrderStatus enumerates all possible order states.
RiskAction enumerates the actions the RiskManager can signal to the main loop.
PartialFill tracks partial fills for an order.
FillRecord is a persistent record of a fill event.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import uuid4


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
class PartialFill:
    """Record of a partial fill for an order."""

    fill_id: str
    filled_qty: float
    fill_price: float
    fee: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "filled_qty": self.filled_qty,
            "fill_price": self.fill_price,
            "fee": self.fee,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PartialFill":
        ts = data.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif ts is None:
            ts = datetime.utcnow()
        return cls(
            fill_id=data.get("fill_id", str(uuid4())[:8]),
            filled_qty=float(data.get("filled_qty", 0)),
            fill_price=float(data.get("fill_price", 0)),
            fee=float(data.get("fee", 0)),
            timestamp=ts,
        )


@dataclass
class FillRecord:
    """
    Persistent record of a fill event.

    Attributes:
        fill_id: Unique identifier for this fill.
        order_id: Original order ID that was filled.
        exchange_order_id: Exchange-assigned order ID.
        side: 'buy' or 'sell'.
        price: Fill price.
        qty: Filled quantity.
        timestamp: When the fill occurred.
        grid_level: Grid level index (optional).
        parent_order_id: ID of the order that triggered this counter-order (optional).
    """

    fill_id: str = field(default_factory=lambda: str(uuid4())[:8])
    order_id: str = ""
    exchange_order_id: str = ""
    side: str = ""
    price: float = 0.0
    qty: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    grid_level: Optional[int] = None
    parent_order_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "exchange_order_id": self.exchange_order_id,
            "side": self.side,
            "price": self.price,
            "qty": self.qty,
            "timestamp": self.timestamp.isoformat(),
            "grid_level": self.grid_level,
            "parent_order_id": self.parent_order_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FillRecord":
        ts = data.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        elif ts is None:
            ts = datetime.utcnow()
        return cls(
            fill_id=data.get("fill_id", str(uuid4())[:8]),
            order_id=data.get("order_id", ""),
            exchange_order_id=data.get("exchange_order_id", ""),
            side=data.get("side", ""),
            price=float(data.get("price", 0)),
            qty=float(data.get("qty", 0)),
            timestamp=ts,
            grid_level=data.get("grid_level"),
            parent_order_id=data.get("parent_order_id"),
        )


@dataclass
class OrderRecord:
    """
    Tracks a single open grid limit order.

    Attributes:
        order_id:         Exchange-assigned order identifier.
        grid_price:      The intended grid level price used to place the order.
        side:            'buy' or 'sell'.
        amount:          Quantity in base currency.
        placed_at:       UTC timestamp when the order was placed.
        status:          Current lifecycle status of the order.
        filled_price:    Actual fill price (set when status becomes FILLED).
        filled_at:       UTC timestamp of fill (set when status becomes FILLED).
        filled_qty:      Total filled quantity (for partial fills).
        grid_level_id:   Grid level index for traceability.
        parent_order_id: ID of the order that triggered this counter-order.
        partial_fills:   List of partial fill records.
    """

    order_id: str
    grid_price: float
    side: str
    amount: float
    placed_at: datetime = field(default_factory=datetime.utcnow)
    status: OrderStatus = OrderStatus.OPEN
    filled_price: Optional[float] = None
    filled_at: Optional[datetime] = None
    filled_qty: float = 0.0
    grid_level_id: Optional[int] = None
    parent_order_id: Optional[str] = None
    partial_fills: List[PartialFill] = field(default_factory=list)

    @property
    def remaining_qty(self) -> float:
        """Calculate remaining quantity to fill."""
        return max(0.0, self.amount - self.filled_qty)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "grid_price": self.grid_price,
            "side": self.side,
            "amount": self.amount,
            "placed_at": self.placed_at.isoformat(),
            "status": self.status.value,
            "filled_price": self.filled_price,
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "filled_qty": self.filled_qty,
            "grid_level_id": self.grid_level_id,
            "parent_order_id": self.parent_order_id,
            "partial_fills": [pf.to_dict() for pf in self.partial_fills],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderRecord":
        status_val = data.get("status", "open")
        if isinstance(status_val, str):
            status = OrderStatus(status_val)
        else:
            status = OrderStatus.OPEN

        placed_at = data.get("placed_at")
        if isinstance(placed_at, str):
            placed_at = datetime.fromisoformat(placed_at.replace("Z", "+00:00"))
        elif placed_at is None:
            placed_at = datetime.utcnow()

        filled_at = data.get("filled_at")
        if isinstance(filled_at, str):
            filled_at = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))

        partial_fills = []
        for pf_data in data.get("partial_fills", []):
            partial_fills.append(PartialFill.from_dict(pf_data))

        return cls(
            order_id=data.get("order_id", ""),
            grid_price=float(data.get("grid_price", 0)),
            side=data.get("side", ""),
            amount=float(data.get("amount", 0)),
            placed_at=placed_at,
            status=status,
            filled_price=data.get("filled_price"),
            filled_at=filled_at,
            filled_qty=float(data.get("filled_qty", 0)),
            grid_level_id=data.get("grid_level_id"),
            parent_order_id=data.get("parent_order_id"),
            partial_fills=partial_fills,
        )
