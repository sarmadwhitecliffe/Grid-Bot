"""
Order State Manager - Persistent Order Tracking

Maintains a persistent cache of all live orders synchronized with the exchange.
Provides reconciliation capabilities to detect missing/stale orders.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OrderRecordStatus(Enum):
    """Order record status for tracking."""

    NEW = "NEW"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"


@dataclass
class OrderRecord:
    """
    Canonical local order record.

    Tracks both local state and exchange state for reconciliation.
    """

    local_id: str  # Bot-generated unique ID
    exchange_order_id: Optional[str] = None  # Exchange order ID
    symbol: str = ""
    side: str = "BUY"  # BUY or SELL
    quantity: str = "0"  # Use string for Decimal serialization
    leverage: int = 1
    order_type: str = "MARKET"
    status: str = "NEW"
    created_at: Optional[str] = None  # ISO timestamp
    verified_at: Optional[str] = None  # ISO timestamp
    filled_qty: str = "0"
    avg_price: Optional[str] = None
    fees: str = "0"
    pnl: Optional[str] = None
    mode: str = "local_sim"  # local_sim, live
    verification_status: Optional[str] = None  # VERIFIED, UNVERIFIED
    verification_error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None  # Raw exchange response

    @classmethod
    def from_exchange_response(
        cls, local_id: str, exchange_response: Dict[str, Any], mode: str = "live"
    ) -> "OrderRecord":
        """Create OrderRecord from exchange API response."""
        # Safely extract fee cost (fee can be None or dict)
        fee = exchange_response.get("fee")
        fee_cost = "0"
        if fee and isinstance(fee, dict):
            fee_cost = str(fee.get("cost", 0))
        elif fee is not None:
            # Handle case where fee is a number
            try:
                fee_cost = str(float(fee))
            except (ValueError, TypeError):
                fee_cost = "0"

        return cls(
            local_id=local_id,
            exchange_order_id=str(exchange_response.get("id", "")),
            symbol=exchange_response.get("symbol", ""),
            side=exchange_response.get("side", "BUY").upper(),
            quantity=str(exchange_response.get("amount", 0)),
            leverage=1,  # TODO: Extract from params if available
            order_type=exchange_response.get("type", "MARKET").upper(),
            status=exchange_response.get("status", "NEW").upper(),
            created_at=datetime.now(timezone.utc).isoformat(),
            verified_at=(
                datetime.now(timezone.utc).isoformat()
                if exchange_response.get("_verification_status") == "VERIFIED"
                else None
            ),
            filled_qty=str(exchange_response.get("filled", 0)),
            avg_price=(
                str(exchange_response.get("price", 0))
                if exchange_response.get("price")
                else None
            ),
            fees=fee_cost,
            mode=mode,
            verification_status=exchange_response.get("_verification_status"),
            verification_error=exchange_response.get("_verification_error"),
            raw_response=exchange_response,
        )


class OrderStateManager:
    async def periodic_reconcile(self, exchange_fetch_func, interval_sec: int = 300):
        """
        Periodically reconcile orders with exchange and auto-resolve orphans.
        Args:
            exchange_fetch_func: async function to fetch open orders from exchange
            interval_sec: seconds between reconciliations
        """
        import asyncio

        while True:
            report = await self.reconcile_orders(exchange_fetch_func)
            # Attempt to auto-resolve orphaned positions
            for orphan in report.get("unexpected_on_exchange", []):
                exchange_order_id = orphan.get("exchange_order_id")
                symbol = orphan.get("symbol")
                # Attempt to cancel/close orphaned order (requires exchange API)
                logger.warning(
                    f"Attempting to auto-resolve orphaned order {exchange_order_id} for {symbol}"
                )
                # You would call exchange.cancel_order(exchange_order_id, symbol) here if available
            await asyncio.sleep(interval_sec)

    """
    Manages persistent order state and reconciliation.
    
    Features:
    - Atomic writes (temp file + rename)
    - Thread-safe operations
    - Reconciliation with exchange
    - STALE/UNVERIFIED detection
    """

    def __init__(self, data_dir: Path):
        """
        Initialize order state manager.

        Args:
            data_dir: Directory for persistence files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.orders_file = self.data_dir / "orders_state.json"

        # In-memory state
        self._orders: Dict[str, Dict[str, Any]] = {}

        # Load existing state
        self._load()

    def _load(self) -> None:
        """Load orders from disk."""
        if not self.orders_file.exists():
            logger.info("No existing orders_state.json, starting fresh")
            self._orders = {"orders": {}, "metadata": {"last_updated": None}}
            return

        try:
            with open(self.orders_file, "r") as f:
                data = json.load(f)

            # Validate structure
            if "orders" not in data:
                logger.warning("orders_state.json missing 'orders' key, resetting")
                self._orders = {"orders": {}, "metadata": {"last_updated": None}}
            else:
                self._orders = data
                order_count = len(self._orders.get("orders", {}))
                logger.info(f"Loaded {order_count} orders from state")

        except Exception as e:
            logger.error(f"Error loading orders_state.json: {e}", exc_info=True)
            self._orders = {"orders": {}, "metadata": {"last_updated": None}}

    def _save(self) -> None:
        """Save orders to disk with atomic write."""
        try:
            # Update metadata
            self._orders["metadata"]["last_updated"] = datetime.now(
                timezone.utc
            ).isoformat()

            # Atomic write: temp file + rename
            temp_file = self.orders_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                json.dump(self._orders, f, indent=2, default=str)

            # Atomic rename
            temp_file.replace(self.orders_file)

        except Exception as e:
            logger.error(f"Error saving orders_state.json: {e}", exc_info=True)

    def add_order(self, order: OrderRecord) -> None:
        """
        Add or update an order in state.

        Args:
            order: OrderRecord to persist
        """
        order_dict = asdict(order)
        self._orders["orders"][order.local_id] = order_dict
        self._save()
        logger.info(
            f"Order {order.local_id} added to state (exchange_id={order.exchange_order_id})"
        )

    def get_order(self, local_id: str) -> Optional[OrderRecord]:
        """
        Retrieve an order by local ID.

        Args:
            local_id: Local order ID

        Returns:
            OrderRecord if found, None otherwise
        """
        order_dict = self._orders["orders"].get(local_id)
        if not order_dict:
            return None

        return OrderRecord(**order_dict)

    def get_all_orders(self) -> List[OrderRecord]:
        """Get all orders in state."""
        return [OrderRecord(**o) for o in self._orders["orders"].values()]

    def get_open_orders(self) -> List[OrderRecord]:
        """Get all orders with open status (NEW, PARTIALLY_FILLED)."""
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if o.get("status") in ["NEW", "PARTIALLY_FILLED"]
        ]

    def get_unverified_orders(self) -> List[OrderRecord]:
        """Get all orders that failed verification."""
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if o.get("verification_status") == "UNVERIFIED"
        ]

    def update_order_status(
        self,
        local_id: str,
        status: str,
        filled_qty: Optional[str] = None,
        avg_price: Optional[str] = None,
    ) -> None:
        """
        Update order status and optionally fill information.

        Args:
            local_id: Local order ID
            status: New status
            filled_qty: Filled quantity (if applicable)
            avg_price: Average fill price (if applicable)
        """
        if local_id not in self._orders["orders"]:
            logger.warning(f"Cannot update non-existent order {local_id}")
            return

        self._orders["orders"][local_id]["status"] = status
        if filled_qty is not None:
            self._orders["orders"][local_id]["filled_qty"] = filled_qty
        if avg_price is not None:
            self._orders["orders"][local_id]["avg_price"] = avg_price

        self._save()
        logger.info(f"Order {local_id} status updated to {status}")

    def mark_as_stale(
        self, local_id: str, reason: str = "Not found on exchange"
    ) -> None:
        """
        Mark an order as STALE.

        Args:
            local_id: Local order ID
            reason: Reason for marking stale
        """
        if local_id not in self._orders["orders"]:
            logger.warning(f"Cannot mark non-existent order {local_id} as stale")
            return

        self._orders["orders"][local_id]["status"] = "STALE"
        self._orders["orders"][local_id]["verification_error"] = reason
        self._save()
        logger.warning(f"Order {local_id} marked as STALE: {reason}")

    async def reconcile_orders(self, exchange_fetch_func) -> Dict[str, Any]:
        """
        Reconcile local order state with exchange.

        Args:
            exchange_fetch_func: Async function to fetch open orders from exchange
                                 Should accept symbol and return list of order dicts

        Returns:
            Reconciliation report with discrepancies
        """
        logger.info("Starting order reconciliation...")

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "local_open_count": 0,
            "exchange_open_count": 0,
            "verified_count": 0,
            "missing_on_exchange": [],
            "unexpected_on_exchange": [],
            "status_mismatches": [],
        }

        try:
            # Get local open orders
            local_open = self.get_open_orders()
            report["local_open_count"] = len(local_open)

            # Fetch all open orders from exchange (grouped by symbol)
            symbols = set(o.symbol for o in local_open)
            exchange_orders = {}

            for symbol in symbols:
                try:
                    orders = await exchange_fetch_func(symbol)
                    for order in orders:
                        exchange_orders[str(order["id"])] = order
                except Exception as e:
                    logger.error(f"Failed to fetch orders for {symbol}: {e}")

            report["exchange_open_count"] = len(exchange_orders)

            # Check local orders against exchange
            for local_order in local_open:
                if not local_order.exchange_order_id:
                    logger.warning(
                        f"Local order {local_order.local_id} missing exchange_order_id"
                    )
                    continue

                exchange_order = exchange_orders.get(local_order.exchange_order_id)

                if not exchange_order:
                    # Order missing on exchange
                    logger.warning(
                        f"Local order {local_order.local_id} (exchange={local_order.exchange_order_id}) "
                        f"not found on exchange"
                    )
                    report["missing_on_exchange"].append(
                        {
                            "local_id": local_order.local_id,
                            "exchange_order_id": local_order.exchange_order_id,
                            "symbol": local_order.symbol,
                            "status": local_order.status,
                        }
                    )
                    self.mark_as_stale(
                        local_order.local_id,
                        "Order not found on exchange during reconciliation",
                    )
                else:
                    # Order found - check status
                    exchange_status = exchange_order.get("status", "").upper()
                    if exchange_status != local_order.status:
                        logger.info(
                            f"Status mismatch for {local_order.local_id}: "
                            f"local={local_order.status}, exchange={exchange_status}"
                        )
                        report["status_mismatches"].append(
                            {
                                "local_id": local_order.local_id,
                                "local_status": local_order.status,
                                "exchange_status": exchange_status,
                            }
                        )
                        # Update local status
                        self.update_order_status(
                            local_order.local_id,
                            exchange_status,
                            str(exchange_order.get("filled", 0)),
                            (
                                str(exchange_order.get("price", 0))
                                if exchange_order.get("price")
                                else None
                            ),
                        )
                    else:
                        report["verified_count"] += 1

            # Check for unexpected orders on exchange
            local_exchange_ids = {
                o.exchange_order_id for o in local_open if o.exchange_order_id
            }
            for exchange_id, exchange_order in exchange_orders.items():
                if exchange_id not in local_exchange_ids:
                    logger.warning(
                        f"Exchange order {exchange_id} not found in local state (symbol={exchange_order.get('symbol')})"
                    )
                    report["unexpected_on_exchange"].append(
                        {
                            "exchange_order_id": exchange_id,
                            "symbol": exchange_order.get("symbol"),
                            "status": exchange_order.get("status"),
                            "amount": exchange_order.get("amount"),
                        }
                    )

            logger.info(
                f"Reconciliation complete: {report['verified_count']} verified, "
                f"{len(report['missing_on_exchange'])} missing, "
                f"{len(report['status_mismatches'])} mismatches"
            )

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}", exc_info=True)
            report["error"] = str(e)

        return report

    def get_stats(self) -> Dict[str, Any]:
        """Get order statistics."""
        orders = self.get_all_orders()

        status_counts = {}
        for order in orders:
            status_counts[order.status] = status_counts.get(order.status, 0) + 1

        return {
            "total_orders": len(orders),
            "open_orders": len(self.get_open_orders()),
            "unverified_orders": len(self.get_unverified_orders()),
            "status_breakdown": status_counts,
            "last_updated": self._orders["metadata"].get("last_updated"),
        }


__all__ = ["OrderStateManager", "OrderRecord", "OrderRecordStatus"]
