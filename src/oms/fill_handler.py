"""
src/oms/fill_handler.py
-----------------------
Detects filled grid orders by comparing the bot's in-memory state
against live open orders from the exchange, then places counter-orders.

Fill logic:
  Buy fill at price P  -> place sell limit one grid level above P.
  Sell fill at price P -> place buy limit one grid level below P.
"""

import logging
from typing import List

from config.settings import GridBotSettings
from src.exchange.exchange_client import ExchangeClient
from src.oms import OrderRecord, OrderStatus
from src.oms.order_manager import OrderManager
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
    ) -> None:
        """
        Initialise the FillHandler.

        Args:
            order_manager: The OrderManager holding all in-memory order state.
            client:        Async exchange client for fetching open orders.
            calculator:    GridCalculator for computing counter-order prices.
            settings:      Validated bot settings (MAX_OPEN_ORDERS, etc.).
        """
        self.order_manager = order_manager
        self.client = client
        self.calculator = calculator
        self.settings = settings

    async def poll_and_handle(
        self, centre_price: float
    ) -> List[OrderRecord]:
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
            if oid not in open_ids_on_exchange:
                # Order is no longer open on exchange -> treat as filled.
                record.status = OrderStatus.FILLED
                newly_filled.append(record)
                logger.info(
                    "Fill detected: %s @ %.4f | ID: %s",
                    record.side,
                    record.grid_price,
                    oid,
                )
                await self._place_counter_order(record)

        return newly_filled

    async def _place_counter_order(self, filled: OrderRecord) -> None:
        """
        Place the opposite-side counter-order one grid step away from the fill.

        Args:
            filled: The OrderRecord that was just filled.
        """
        if filled.side == "buy":
            counter_price = self.calculator._price(filled.grid_price, 1, "up")
            counter_side = "sell"
        else:
            counter_price = self.calculator._price(filled.grid_price, 1, "down")
            counter_side = "buy"

        if self.order_manager.open_order_count >= self.settings.MAX_OPEN_ORDERS:
            logger.warning(
                "MAX_OPEN_ORDERS hit — skipping counter-order placement."
            )
            return

        level = GridLevel(
            price=counter_price,
            side=counter_side,
            level_index=1,
            order_size_quote=self.settings.ORDER_SIZE_QUOTE,
        )
        await self.order_manager.deploy_grid([level])
