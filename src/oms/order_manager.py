"""
src/oms/order_manager.py
------------------------
Manages the full lifecycle of all active grid limit orders.

Maintains an in-memory mapping of grid_price -> order_id and a separate
order_id -> OrderRecord registry. All mutations are protected by an
asyncio.Lock to prevent race conditions during concurrent fill handling.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List

from config.settings import GridBotSettings
from src.exchange.exchange_client import ExchangeClient
from src.oms import OrderRecord, OrderStatus
from src.strategy import GridLevel

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Thread-safe in-memory order lifecycle manager.

    Key data structures:
    - _orders:   dict[order_id -> OrderRecord]  -- full record of every order.
    - _grid_map: dict[grid_price -> order_id]   -- fast lookup by price level.
    """

    def __init__(
        self,
        client: ExchangeClient,
        settings: GridBotSettings,
    ) -> None:
        """
        Initialise the OrderManager.

        Args:
            client:   Async exchange client for place/cancel API calls.
            settings: Validated bot settings (MAX_OPEN_ORDERS, etc.).
        """
        self.client = client
        self.settings = settings
        self._orders: Dict[str, OrderRecord] = {}
        self._grid_map: Dict[float, str] = {}
        self._lock = asyncio.Lock()

    # ── Placement ─────────────────────────────────────────────────────────────

    async def deploy_grid(self, levels: List[GridLevel]) -> None:
        """
        Place limit orders for all supplied grid levels.

        Skips levels that already have an active order at that price.
        Stops placing new orders once MAX_OPEN_ORDERS is reached.

        Args:
            levels: List of GridLevel objects (output of GridCalculator.calculate).
        """
        async with self._lock:
            open_count = self._count_open()
            for level in levels:
                if open_count >= self.settings.MAX_OPEN_ORDERS:
                    logger.warning(
                        "MAX_OPEN_ORDERS (%d) reached — halting grid deployment.",
                        self.settings.MAX_OPEN_ORDERS,
                    )
                    break
                if level.price in self._grid_map:
                    continue  # Already has an active order at this level.
                try:
                    amount = level.order_size_quote / level.price
                    result = await self.client.place_limit_order(
                        side=level.side, price=level.price, amount=amount
                    )
                    record = OrderRecord(
                        order_id=result["id"],
                        grid_price=level.price,
                        side=level.side,
                        amount=amount,
                    )
                    self._orders[result["id"]] = record
                    self._grid_map[level.price] = result["id"]
                    open_count += 1
                    logger.info(
                        "Placed %s limit @ %.4f | ID: %s",
                        level.side,
                        level.price,
                        result["id"],
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to place order at %.4f: %s", level.price, exc
                    )

    async def cancel_all_orders(self) -> None:
        """
        Cancel every open order tracked by this manager.

        Used on regime change, re-centering, or risk-triggered shutdown.
        Individual cancel failures are logged but don't abort the loop.
        """
        async with self._lock:
            open_ids = [
                oid
                for oid, rec in self._orders.items()
                if rec.status == OrderStatus.OPEN
            ]

        for oid in open_ids:
            try:
                await self.client.cancel_order(oid)
                async with self._lock:
                    if oid in self._orders:
                        rec = self._orders[oid]
                        rec.status = OrderStatus.CANCELED
                        self._grid_map.pop(rec.grid_price, None)
                logger.info("Canceled order %s", oid)
            except Exception as exc:
                logger.warning("Cancel failed for %s: %s", oid, exc)

    async def cancel_order(self, order_id: str) -> None:
        """
        Cancel a single order by its exchange ID.

        Args:
            order_id: Exchange-assigned order identifier.
        """
        await self.client.cancel_order(order_id)
        async with self._lock:
            if order_id in self._orders:
                rec = self._orders[order_id]
                rec.status = OrderStatus.CANCELED
                self._grid_map.pop(rec.grid_price, None)

    # ── Queries ───────────────────────────────────────────────────────────────

    @property
    def open_order_count(self) -> int:
        """Return the number of currently open orders."""
        return self._count_open()

    @property
    def all_records(self) -> Dict[str, OrderRecord]:
        """Return a shallow copy of the full order registry."""
        return dict(self._orders)

    def get_record(self, order_id: str) -> OrderRecord:
        """
        Retrieve a single OrderRecord by its exchange ID.

        Args:
            order_id: Exchange-assigned order identifier.

        Returns:
            OrderRecord or None if the order_id is not tracked.
        """
        return self._orders.get(order_id)

    # ── State Serialization (for Persistence layer) ───────────────────────────

    def export_state(self) -> dict:
        """
        Serialize in-memory state to a JSON-serializable dictionary.

        Returns:
            dict: {'orders': {order_id: {...}}} suitable for StateStore.save().
        """
        return {
            "orders": {
                oid: {
                    "order_id": r.order_id,
                    "grid_price": r.grid_price,
                    "side": r.side,
                    "amount": r.amount,
                    "status": r.status.value,
                    "placed_at": r.placed_at.isoformat(),
                }
                for oid, r in self._orders.items()
            }
        }

    def import_state(self, state: dict) -> None:
        """
        Restore in-memory state from a persisted dictionary (called on startup).

        Only orders with status OPEN are added back to _grid_map.

        Args:
            state: Dictionary previously produced by export_state().
        """
        for oid, data in state.get("orders", {}).items():
            rec = OrderRecord(
                order_id=data["order_id"],
                grid_price=data["grid_price"],
                side=data["side"],
                amount=data["amount"],
                status=OrderStatus(data["status"]),
                placed_at=datetime.fromisoformat(data["placed_at"]),
            )
            self._orders[oid] = rec
            if rec.status == OrderStatus.OPEN:
                self._grid_map[rec.grid_price] = oid

    # ── Private ───────────────────────────────────────────────────────────────

    def _count_open(self) -> int:
        """Count records with OPEN status (no lock — caller must hold lock)."""
        return sum(
            1 for r in self._orders.values() if r.status == OrderStatus.OPEN
        )
