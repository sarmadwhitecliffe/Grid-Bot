"""
src/oms/fill_handler.py
-----------------------
Detects filled grid orders by comparing the bot's in-memory state
against live open orders from the exchange, then places counter-orders.

Fill logic:
  Buy fill at price P  -> place sell limit one grid level above P.
  Sell fill at price P -> place buy limit one grid level below P.

Features:
  - Persistent fill logging to WAL and fill_log.jsonl
  - Partial fill handling
  - Fill deduplication to prevent duplicate counter-orders
  - Fill price recording from exchange
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Set

from config.settings import GridBotSettings
from src.exchange.exchange_client import ExchangeClient
from src.oms import FillRecord, OrderRecord, OrderStatus, PartialFill
from src.oms.order_manager import OrderManager
from src.persistence.fill_logger import FillLogger
from src.strategy import GridLevel
from src.strategy.grid_calculator import GridCalculator

logger = logging.getLogger(__name__)


class FillHandler:
    """
    Polls the exchange for filled orders and triggers counter-orders.

    On every polling cycle the handler:
    1. Fetches all open orders from the exchange.
    2. Compares against the bot's tracked open orders.
    3. Any order absent from the exchange is treated as filled.
    4. Places the corresponding counter-order via OrderManager.deploy_grid.
    """

    def __init__(
        self,
        order_manager: OrderManager,
        client: ExchangeClient,
        calculator: GridCalculator,
        settings: GridBotSettings,
        fill_logger: Optional[FillLogger] = None,
    ) -> None:
        """
        Initialise the FillHandler.

        Args:
            order_manager: The OrderManager holding all in-memory order state.
            client:        Async exchange client for fetching open orders.
            calculator:    GridCalculator for computing counter-order prices.
            settings:      Validated bot settings (MAX_OPEN_ORDERS, etc.).
            fill_logger:   Optional FillLogger for persistent fill logging.
        """
        self.order_manager = order_manager
        self.client = client
        self.calculator = calculator
        self.settings = settings
        self.fill_logger = fill_logger
        self._processed_fills: Set[str] = set()

        if self.fill_logger:
            self._processed_fills = self.fill_logger.load_processed_fills()

    def _is_fill_processed(self, order_id: str) -> bool:
        """Check if a fill has already been processed."""
        return order_id in self._processed_fills

    def _mark_fill_processed(self, order_id: str) -> None:
        """Mark a fill as processed."""
        self._processed_fills.add(order_id)
        if self.fill_logger:
            self.fill_logger.mark_fill_processed(order_id)

    async def _fetch_fill_details(self, order_id: str) -> dict:
        """Fetch actual fill details from exchange."""
        try:
            order_status = await self.client.get_order_status(order_id)
            return {
                "filled_qty": float(order_status.get("filled", 0)),
                "fill_price": float(order_status.get("average_price", 0)),
                "filled_at": order_status.get("filled_at"),
            }
        except Exception as e:
            logger.warning(f"Failed to fetch fill details for {order_id}: {e}")
            return {
                "filled_qty": 0,
                "fill_price": 0,
                "filled_at": None,
            }

    def _is_partial_fill(self, exchange_order: dict) -> bool:
        """Check if an order is partially filled."""
        filled = float(exchange_order.get("filled", 0))
        amount = float(exchange_order.get("amount", 0))
        return filled > 0 and filled < amount

    async def poll_and_handle(self, centre_price: float) -> List[OrderRecord]:
        """
        Check for fills and place counter-orders for each detected fill.

        Args:
            centre_price: Current grid centre price (passed to counter-order logic).

        Returns:
            list[OrderRecord]: Newly filled records detected in this cycle.
        """
        open_exchange = await self.client.fetch_open_orders()
        open_ids_on_exchange = {o["id"] for o in open_exchange}

        newly_filled: List[OrderRecord] = []

        for oid, record in list(self.order_manager.all_records.items()):
            if record.status != OrderStatus.OPEN:
                continue

            if self._is_fill_processed(oid):
                logger.debug(f"Fill already processed: {oid}")
                continue

            if oid not in open_ids_on_exchange:
                fill_details = await self._fetch_fill_details(oid)
                filled_qty = fill_details.get("filled_qty", record.amount)
                fill_price = fill_details.get("fill_price", record.grid_price)

                is_partial = filled_qty > 0 and filled_qty < record.amount

                if is_partial:
                    await self._handle_partial_fill(record, filled_qty, fill_price)
                else:
                    await self._handle_fill(record, fill_price)

                newly_filled.append(record)

        return newly_filled

    async def _handle_fill(self, record: OrderRecord, fill_price: float) -> None:
        """Handle a complete fill."""
        order_id = record.order_id

        if self._is_fill_processed(order_id):
            logger.warning(f"Duplicate fill detected: {order_id}")
            return

        fill = FillRecord(
            order_id=order_id,
            exchange_order_id=order_id,
            side=record.side,
            price=fill_price,
            qty=record.amount,
            grid_level=record.grid_level_id,
            parent_order_id=record.parent_order_id,
        )

        if self.fill_logger:
            try:
                self.fill_logger.log_fill(fill)
            except Exception as e:
                logger.error(f"Failed to log fill: {e}")

        record.status = OrderStatus.FILLED
        record.filled_price = fill_price
        record.filled_at = datetime.now(timezone.utc)
        record.filled_qty = record.amount

        self._mark_fill_processed(order_id)

        logger.info(
            "Fill detected: %s @ %.4f | ID: %s | Filled qty: %.6f",
            record.side,
            fill_price,
            order_id,
            record.amount,
        )

        await self._place_counter_order(record, filled_qty=record.amount)

    async def _handle_partial_fill(
        self,
        record: OrderRecord,
        filled_qty: float,
        fill_price: float,
    ) -> None:
        """Handle a partial fill."""
        order_id = record.order_id

        partial = PartialFill(
            fill_id=f"pf_{order_id[:6]}",
            filled_qty=filled_qty,
            fill_price=fill_price,
            timestamp=datetime.now(timezone.utc),
        )
        record.partial_fills.append(partial)

        record.filled_qty += filled_qty
        avg_price = (
            sum(pf.filled_qty * pf.fill_price for pf in record.partial_fills)
            / record.filled_qty
        )
        record.filled_price = avg_price

        if record.remaining_qty <= 0:
            record.status = OrderStatus.FILLED

            fill = FillRecord(
                order_id=order_id,
                exchange_order_id=order_id,
                side=record.side,
                price=avg_price,
                qty=record.filled_qty,
                grid_level=record.grid_level_id,
                parent_order_id=record.parent_order_id,
            )

            if self.fill_logger:
                try:
                    self.fill_logger.log_fill(fill)
                except Exception as e:
                    logger.error(f"Failed to log partial fill: {e}")

            self._mark_fill_processed(order_id)
            logger.info(
                "Partial fill complete: %s @ %.4f | Total filled: %.6f",
                record.side,
                avg_price,
                record.filled_qty,
            )
        else:
            record.status = OrderStatus.PARTIALLY_FILLED
            logger.info(
                "Partial fill: %s @ %.4f | Filled: %.6f / %.6f remaining",
                record.side,
                fill_price,
                filled_qty,
                record.remaining_qty,
            )

        await self._place_counter_order(record, filled_qty=filled_qty)

    async def _place_counter_order(
        self,
        filled: OrderRecord,
        filled_qty: Optional[float] = None,
    ) -> None:
        """
        Place the opposite-side counter-order one grid step away from the fill.

        Args:
            filled: The OrderRecord that was just filled.
            filled_qty: Quantity that was filled (for partial fills).
        """
        if filled.side == "buy":
            counter_price = self.calculator._price(filled.grid_price, 1, "up")
            counter_side = "sell"
            counter_level = (filled.grid_level_id or 0) + 1
        else:
            counter_price = self.calculator._price(filled.grid_price, 1, "down")
            counter_side = "buy"
            counter_level = (filled.grid_level_id or 0) - 1

        if self.order_manager.open_order_count >= self.settings.MAX_OPEN_ORDERS:
            logger.warning("MAX_OPEN_ORDERS hit — skipping counter-order placement.")
            return

        qty = filled_qty if filled_qty is not None else filled.amount

        level = GridLevel(
            price=counter_price,
            side=counter_side,
            level_index=counter_level,
            order_size_quote=self.settings.ORDER_SIZE_QUOTE,
        )

        counter_order_id = await self.order_manager.deploy_grid([level])

        if counter_order_id:
            for oid in counter_order_id:
                counter_record = self.order_manager.all_records.get(oid)
                if counter_record:
                    counter_record.parent_order_id = filled.order_id
                    counter_record.grid_level_id = counter_level

        logger.debug(
            "Counter-order placed: %s @ %.4f for filled %s",
            counter_side,
            counter_price,
            filled.order_id,
        )

    def cleanup_old_fills(self, max_age_hours: int = 24) -> int:
        """Clean up old processed fills to prevent memory growth."""
        if self.fill_logger:
            return self.fill_logger.cleanup_old_fills(max_age_hours)
        return 0
