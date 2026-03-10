"""
Grid State Model

Dataclass representing the current state of an active Grid session.
Used for persistence and recovery.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import datetime

@dataclass
class GridState:
    """
    Current state of a Grid Trading session for a symbol.
    """
    symbol_id: str
    is_active: bool = False
    centre_price: Optional[Decimal] = None
    
    # Track active limit orders by their exchange ID
    # mapping: order_id -> {price, amount, side, type: 'grid'|'counter'}
    active_orders: Dict[str, Dict] = field(default_factory=dict)
    
    # Session Metrics
    session_start_time: Optional[datetime] = None
    initial_equity: Decimal = Decimal("0")
    current_equity: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    grid_fills: int = 0
    counter_fills: int = 0
    
    # Recovery data
    last_tick_time: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        from bot_v2.utils.decimal_utils import decimal_to_str
        
        return {
            "symbol_id": self.symbol_id,
            "is_active": self.is_active,
            "centre_price": decimal_to_str(self.centre_price) if self.centre_price else None,
            "active_orders": self.active_orders,
            "session_start_time": self.session_start_time.isoformat() if self.session_start_time else None,
            "initial_equity": decimal_to_str(self.initial_equity),
            "current_equity": decimal_to_str(self.current_equity),
            "total_fees": decimal_to_str(self.total_fees),
            "grid_fills": self.grid_fills,
            "counter_fills": self.counter_fills,
            "last_tick_time": self.last_tick_time.isoformat() if self.last_tick_time else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GridState":
        """Create GridState from dict."""
        from bot_v2.utils.decimal_utils import to_decimal
        
        return cls(
            symbol_id=data["symbol_id"],
            is_active=data.get("is_active", False),
            centre_price=to_decimal(data.get("centre_price")) if data.get("centre_price") else None,
            active_orders=data.get("active_orders", {}),
            session_start_time=datetime.fromisoformat(data["session_start_time"]) if data.get("session_start_time") else None,
            initial_equity=to_decimal(data.get("initial_equity", "0")),
            current_equity=to_decimal(data.get("current_equity", "0")),
            total_fees=to_decimal(data.get("total_fees", "0")),
            grid_fills=data.get("grid_fills", 0),
            counter_fills=data.get("counter_fills", 0),
            last_tick_time=datetime.fromisoformat(data["last_tick_time"]) if data.get("last_tick_time") else None,
        )
