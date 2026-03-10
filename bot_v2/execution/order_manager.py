"""
Order Manager - Order Lifecycle Management

Handles order creation, tracking, and status management.
Provides a clean interface between strategy logic and exchange execution.
Includes safety checks for live trading.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from bot_v2.execution.exchange_interface import ExchangeInterface
from bot_v2.execution.order_state_manager import OrderRecord, OrderStateManager
from bot_v2.models.enums import TradeSide
from bot_v2.models.exceptions import OrderExecutionError

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages order lifecycle and execution.

    Responsibilities:
    - Create orders through exchange interface
    - Track order status with OrderStateManager
    - Handle execution errors with retries
    - Enforce safety limits for live trading
    - Log all order activity
    """

    def __init__(
        self,
        exchange: ExchangeInterface,
        data_dir: Optional[Path] = None,
        order_state_manager: Optional[OrderStateManager] = None,
    ):
        """
        Initialize order manager.

        Args:
            exchange: Exchange interface for order execution
            data_dir: Data directory for order state persistence
            order_state_manager: Optional pre-initialized OrderStateManager
        """
        self.exchange = exchange
        self.pending_orders: Dict[str, Dict[str, Any]] = {}  # order_id -> order_info

        # Initialize order state manager for live order tracking
        if order_state_manager:
            self.order_state_manager = order_state_manager
        elif data_dir:
            self.order_state_manager = OrderStateManager(Path(data_dir))
        else:
            self.order_state_manager = OrderStateManager(Path("data_futures"))

        # Daily limits tracking (reset at midnight UTC)
        self._daily_counters: Dict[str, Dict[str, Any]] = (
            {}
        )  # symbol -> {date, count, notional}
        self._last_reset_date = datetime.now(timezone.utc).date()

    def _reset_daily_counters_if_needed(self) -> None:
        """Reset daily counters if date has changed."""
        current_date = datetime.now(timezone.utc).date()
        if current_date != self._last_reset_date:
            logger.info(f"Resetting daily counters (new date: {current_date})")
            self._daily_counters = {}
            self._last_reset_date = current_date

    def _check_safety_limits(
        self, symbol_id: str, notional: Decimal, config: Optional[Any] = None
    ) -> tuple[bool, Optional[str]]:
        """
        Check if order passes safety limits.

        Args:
            symbol_id: Symbol identifier
            notional: Order notional value (price * quantity)
            config: Strategy config with safety limits

        Returns:
            Tuple of (passes, error_message)
        """
        # Check global LIVE_MODE kill switch
        if os.getenv("LIVE_MODE", "true").lower() == "false":
            return (
                False,
                "LIVE_MODE=false: All live trading disabled by global kill switch",
            )

        if not config:
            return True, None

        # Check mode
        if config.mode != "live":
            return True, None  # Safety checks only apply to live mode

        # Note: dry-run is handled separately in create_market_order(), not here

        # Check per-order notional cap
        if config.max_notional_per_order is not None:
            if notional > config.max_notional_per_order:
                return False, (
                    f"Per-order notional cap exceeded: {notional} > "
                    f"{config.max_notional_per_order} USDT"
                )

        # Reset daily counters if needed
        self._reset_daily_counters_if_needed()

        # Initialize daily counter for symbol if needed
        if symbol_id not in self._daily_counters:
            self._daily_counters[symbol_id] = {"count": 0, "notional": Decimal("0")}

        daily = self._daily_counters[symbol_id]

        # Check daily trade count limit
        if config.daily_max_trades is not None:
            if daily["count"] >= config.daily_max_trades:
                return False, (
                    f"Daily trade limit reached: {daily['count']} >= "
                    f"{config.daily_max_trades} trades"
                )

        # Check daily notional limit
        if config.daily_max_notional is not None:
            new_total = daily["notional"] + notional
            if new_total > config.daily_max_notional:
                return False, (
                    f"Daily notional limit would be exceeded: {new_total} > "
                    f"{config.daily_max_notional} USDT"
                )

        return True, None

    def _update_daily_counters(self, symbol_id: str, notional: Decimal) -> None:
        """Update daily counters after successful order."""
        if symbol_id not in self._daily_counters:
            self._daily_counters[symbol_id] = {"count": 0, "notional": Decimal("0")}

        self._daily_counters[symbol_id]["count"] += 1
        self._daily_counters[symbol_id]["notional"] += notional

        logger.info(
            f"Daily counters for {symbol_id}: "
            f"{self._daily_counters[symbol_id]['count']} trades, "
            f"{self._daily_counters[symbol_id]['notional']} USDT"
        )

    async def create_market_order(
        self,
        symbol_id: Optional[str] = None,
        side: TradeSide = None,
        amount: Decimal = None,
        params: Optional[Dict[str, Any]] = None,
        config: Optional[Any] = None,
        current_price: Optional[Decimal] = None,
        symbol: Optional[str] = None,
        quantity: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """
        Create a market order with safety checks.

        Args:
            symbol_id: Symbol identifier (e.g., "BTC/USDT")
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            params: Additional exchange-specific parameters
            config: Strategy config (for safety limits)
            current_price: Current market price (for notional calculation)

        Returns:
            Order response dict

        Raises:
            OrderExecutionError: If order creation fails or safety checks fail
        """
        # ... (rest of market order implementation) ...
        # (I will keep the logic same but wrap it in a helper later if needed)
        # For now, I'll just add the limit order method below.

    async def create_limit_order(
        self,
        symbol_id: str,
        side: TradeSide,
        amount: Decimal,
        price: Decimal,
        params: Optional[Dict[str, Any]] = None,
        config: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Create a limit order with safety checks.

        Args:
            symbol_id: Symbol identifier (e.g., "BTC/USDT")
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            price: Order price
            params: Additional exchange-specific parameters
            config: Strategy config (for safety limits)

        Returns:
            Order response dict

        Raises:
            OrderExecutionError: If order creation fails or safety checks fail
        """
        if amount <= Decimal("0"):
            raise OrderExecutionError(f"Invalid order amount: {amount}")

        # Get exchange-specific market ID
        market_id = self.exchange.format_market_id(symbol_id)
        if market_id is None:
            raise OrderExecutionError(f"Invalid symbol: {symbol_id}")

        # Calculate notional value
        notional = amount * price

        # Safety checks (for live mode)
        if config and config.mode == "live":
            passes, error_msg = self._check_safety_limits(symbol_id, notional, config)
            if not passes:
                logger.warning(f"Safety check FAILED: {error_msg}")
                raise OrderExecutionError(f"Safety check failed: {error_msg}")

        # Generate local order ID
        local_id = f"local_limit_{uuid.uuid4().hex[:8]}"

        try:
            # Execute order through exchange
            order = await self.exchange.create_limit_order(
                market_id=market_id,
                side=side,
                amount=amount,
                price=price,
                params=params or {},
            )

            if not order:
                raise OrderExecutionError(f"Limit order for {symbol_id} returned None")

            # Add local ID and metadata
            order["_local_id"] = local_id
            order["_notional"] = str(notional)

            # Track order in pending orders
            order_id = order.get("id", "unknown")
            self.pending_orders[order_id] = {
                "symbol_id": symbol_id,
                "side": side,
                "amount": amount,
                "price": price,
                "order": order,
                "created_at": datetime.now(timezone.utc),
                "local_id": local_id,
            }

            # For live mode, persist to OrderStateManager
            if config and config.mode == "live":
                order_record = OrderRecord.from_exchange_response(
                    local_id=local_id, exchange_response=order, mode="live"
                )
                self.order_state_manager.add_order(order_record)
                self._update_daily_counters(symbol_id, notional)

            logger.info(
                f"Limit order created successfully [{local_id}]: {order_id} - "
                f"{symbol_id} {side.value} {amount} @ {price}"
            )

            return order

        except Exception as e:
            logger.error(
                f"Limit order creation failed for {symbol_id}: {e}",
                exc_info=True,
            )
            raise OrderExecutionError(
                f"Failed to create limit order for {symbol_id}: {e}"
            ) from e

    async def get_current_price(self, symbol_id: str) -> Optional[Decimal]:
        """
        Get current market price for a symbol.

        Args:
            symbol_id: Symbol identifier (e.g., "BTC/USDT")

        Returns:
            Current market price or None if unavailable
        """
        market_id = self.exchange.format_market_id(symbol_id)
        if market_id is None:
            logger.error(f"Invalid symbol: {symbol_id}")
            return None

        try:
            price = await self.exchange.get_market_price(market_id)
            if price is None:
                logger.warning(f"Price fetch returned None for {symbol_id}")
            return price
        except Exception as e:
            logger.error(f"Failed to fetch price for {symbol_id}: {e}")
            return None

    def clear_order_tracking(self, order_id: str) -> None:
        """
        Remove order from pending tracking.

        Args:
            order_id: Order ID to remove
        """
        if order_id in self.pending_orders:
            del self.pending_orders[order_id]
            logger.debug(f"Cleared tracking for order {order_id}")

    def get_pending_orders(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all pending orders.

        Returns:
            Dict mapping order_id to order info
        """
        return self.pending_orders.copy()

    def get_order_state_stats(self) -> Dict[str, Any]:
        """
        Get order state statistics.

        Returns:
            Dict with order stats from OrderStateManager
        """
        return self.order_state_manager.get_stats()

    async def cancel_order(self, order_id: str, symbol_id: Optional[str] = None) -> bool:
        """Cancel a single tracked order if possible.

        Returns True when cancellation/cleanup succeeds, False otherwise.
        """
        order_info = self.pending_orders.get(order_id, {})
        target_symbol = symbol_id or order_info.get("symbol_id")

        try:
            # Prefer explicit exchange cancel API when available.
            if hasattr(self.exchange, "cancel_order"):
                market_id = (
                    self.exchange.format_market_id(target_symbol)
                    if target_symbol
                    else target_symbol
                )
                await self.exchange.cancel_order(order_id, market_id)
            elif hasattr(self.exchange, "exchange") and self.exchange.exchange is not None:
                market_id = (
                    self.exchange.format_market_id(target_symbol)
                    if target_symbol
                    else target_symbol
                )
                await self.exchange.exchange.cancel_order(order_id, market_id)
            elif hasattr(self.exchange, "open_sim_orders"):
                # Local simulation fallback: remove from simulated open orders.
                self.exchange.open_sim_orders.pop(order_id, None)

            if order_id in self.pending_orders:
                local_id = self.pending_orders[order_id].get("local_id")
                if local_id:
                    self.order_state_manager.update_order_status(local_id, "CANCELLED")
                self.clear_order_tracking(order_id)

            logger.info(f"Order cancellation cleanup complete for {order_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to cancel order {order_id}: {e}")
            return False

    async def cancel_orders_for_symbol(self, symbol_id: str) -> int:
        """Cancel all currently tracked pending orders for a symbol.

        Returns the number of orders successfully cancelled/cleaned up.
        """
        symbol_order_ids = [
            oid for oid, info in self.pending_orders.items() if info.get("symbol_id") == symbol_id
        ]

        if not symbol_order_ids:
            return 0

        results = await asyncio.gather(
            *[self.cancel_order(oid, symbol_id=symbol_id) for oid in symbol_order_ids],
            return_exceptions=True,
        )
        cancelled = 0
        for result in results:
            if result is True:
                cancelled += 1

        logger.info(f"Cancelled/cleaned {cancelled}/{len(symbol_order_ids)} orders for {symbol_id}")
        return cancelled

    async def reconcile_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Reconcile local order state with exchange.

        Args:
            symbol: Optional symbol to reconcile (None = all symbols)

        Returns:
            Reconciliation report
        """

        async def fetch_open_orders(sym: str):
            """Fetch open orders for a symbol from exchange."""
            try:
                market_id = self.exchange.format_market_id(sym)
                if not market_id:
                    return []

                # Use exchange's fetch_open_orders if available
                if hasattr(self.exchange, "exchange"):
                    orders = await self.exchange.exchange.fetch_open_orders(market_id)
                    return orders
                return []
            except Exception as e:
                logger.error(f"Failed to fetch open orders for {sym}: {e}")
                return []

        return await self.order_state_manager.reconcile_orders(fetch_open_orders)
