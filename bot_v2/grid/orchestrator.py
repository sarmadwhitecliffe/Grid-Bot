"""
Grid Orchestrator - Manages the lifecycle of Grid Trading sessions.

Integrated into bot_v2 framework to provide:
- Grid deployment using production OrderManager.
- Multi-symbol support.
- State persistence integration.
- Regime-gated execution.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional

from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.models.enums import TradeSide
from src.strategy.grid_calculator import GridCalculator, GridType
from src.strategy.regime_detector import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)


class GridOrchestrator:
    """
    Manages the active grid session for a specific symbol.

    Responsibilities:
    - Initializing GridCalculator and RegimeDetector.
    - Deploying the initial grid.
    - Polling for fills and placing counter-orders (Scalping).
    - Monitoring drift for re-centering.
    - Handling regime-based pauses.
    """

    def __init__(
        self,
        symbol: str,
        config: StrategyConfig,
        order_manager: Any,
        exchange: Any,
        risk_manager: Optional[Any] = None,
        on_grid_trade_closed: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        on_grid_fill: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.symbol = symbol
        self.config = config
        self.order_manager = order_manager
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.on_grid_trade_closed = on_grid_trade_closed
        self.on_grid_fill = on_grid_fill

        # Initialize specialized components from src/
        self.calculator = self._init_calculator()
        self.regime_detector = RegimeDetector(
            adx_threshold=config.grid_adx_threshold
            if hasattr(config, "grid_adx_threshold")
            else 30,
            bb_width_threshold=float(config.grid_bb_width_threshold)
            if hasattr(config, "grid_bb_width_threshold")
            else 0.04,
        )

        # State
        self.is_active = False
        self.centre_price: Optional[Decimal] = None
        self.active_levels: List[Dict[str, Any]] = []
        self.grid_order_ids: set[str] = set()
        self.order_metadata: Dict[str, Dict[str, Any]] = {}
        self.session_realized_pnl_quote = Decimal("0")
        self.session_fill_count = 0
        self.session_buy_qty = Decimal("0")
        self.session_sell_qty = Decimal("0")
        # Open inventory lots used to pair fills into closed grid trades.
        self._open_long_lots: List[Dict[str, Any]] = []
        self._open_short_lots: List[Dict[str, Any]] = []

    def _pair_fill_into_closed_trades(
        self,
        fill_price: Decimal,
        amount: Decimal,
        side: TradeSide,
        filled_order_id: str,
    ) -> List[Dict[str, Any]]:
        """Pair a fill against opposite inventory and return closed trade records."""
        now = datetime.now(timezone.utc)
        remaining = amount
        closed_trades: List[Dict[str, Any]] = []
        epsilon = Decimal("0.00000001")

        if side == TradeSide.BUY:
            # Buy fill can close prior short inventory.
            while remaining > epsilon and self._open_short_lots:
                lot = self._open_short_lots[0]
                matched = min(remaining, lot["amount"])
                pnl = (lot["entry_price"] - fill_price) * matched
                closed_trades.append(
                    {
                        "timestamp": now.isoformat(),
                        "symbol": self.symbol,
                        "type": "grid_close",
                        "position_side": "SHORT",
                        "entry_price": str(lot["entry_price"]),
                        "exit_price": str(fill_price),
                        "quantity": str(matched),
                        "pnl_usd": str(pnl),
                        "entry_time": lot["entry_time"],
                        "exit_time": now.isoformat(),
                        "duration_sec": (now - datetime.fromisoformat(lot["entry_time"]))
                        .total_seconds(),
                        "open_order_id": lot.get("order_id"),
                        "close_order_id": filled_order_id,
                        "source": "grid",
                    }
                )
                lot["amount"] -= matched
                remaining -= matched
                if lot["amount"] <= epsilon:
                    self._open_short_lots.pop(0)

            if remaining > epsilon:
                self._open_long_lots.append(
                    {
                        "entry_price": fill_price,
                        "amount": remaining,
                        "entry_time": now.isoformat(),
                        "order_id": filled_order_id,
                    }
                )
        else:
            # Sell fill can close prior long inventory.
            while remaining > epsilon and self._open_long_lots:
                lot = self._open_long_lots[0]
                matched = min(remaining, lot["amount"])
                pnl = (fill_price - lot["entry_price"]) * matched
                closed_trades.append(
                    {
                        "timestamp": now.isoformat(),
                        "symbol": self.symbol,
                        "type": "grid_close",
                        "position_side": "LONG",
                        "entry_price": str(lot["entry_price"]),
                        "exit_price": str(fill_price),
                        "quantity": str(matched),
                        "pnl_usd": str(pnl),
                        "entry_time": lot["entry_time"],
                        "exit_time": now.isoformat(),
                        "duration_sec": (now - datetime.fromisoformat(lot["entry_time"]))
                        .total_seconds(),
                        "open_order_id": lot.get("order_id"),
                        "close_order_id": filled_order_id,
                        "source": "grid",
                    }
                )
                lot["amount"] -= matched
                remaining -= matched
                if lot["amount"] <= epsilon:
                    self._open_long_lots.pop(0)

            if remaining > epsilon:
                self._open_short_lots.append(
                    {
                        "entry_price": fill_price,
                        "amount": remaining,
                        "entry_time": now.isoformat(),
                        "order_id": filled_order_id,
                    }
                )

        return closed_trades

    def _init_calculator(self) -> GridCalculator:
        """Initialize calculator with proper pricing steps from exchange."""
        # Note: In production, we fetch price_step from the exchange market info
        # For now, using standard 0.0001 step
        return GridCalculator(
            grid_type=GridType.GEOMETRIC,
            spacing_pct=float(getattr(self.config, "grid_spacing_pct", 0.01)),
            num_grids_up=int(getattr(self.config, "grid_num_grids_up", 25)),
            num_grids_down=int(getattr(self.config, "grid_num_grids_down", 25)),
            order_size_quote=float(getattr(self.config, "grid_order_size_quote", 100)),
            price_step=0.0001,
        )

    def _get_risk_adjusted_order_size(self) -> Decimal:
        """Calculate order size quote adjusted by adaptive risk tier allocation."""
        base_order_size = Decimal(
            str(getattr(self.config, "grid_order_size_quote", 100))
        )

        if not self.risk_manager:
            return base_order_size

        try:
            tier_info = self.risk_manager.get_tier_info(self.symbol)
            allocation_pct = Decimal(str(tier_info.get("capital_allocation", 1.0)))
            adjusted_size = base_order_size * allocation_pct

            logger.info(
                f"[{self.symbol}] Risk Adjustment: Tier={tier_info.get('tier')}, "
                f"Allocation={float(allocation_pct) * 100:.0f}%, "
                f"OrderSize: {float(base_order_size)} -> {float(adjusted_size):.2f}"
            )
            return adjusted_size
        except Exception as e:
            logger.warning(
                f"[{self.symbol}] Failed to get risk tier info, using base order size: {e}"
            )
            return base_order_size

    async def start(self):
        """Start the grid session."""
        if self.is_active:
            logger.warning(f"[{self.symbol}] Grid session already active.")
            return

        logger.info(f"[{self.symbol}] Starting Grid session.")
        self.is_active = True

        # 1. Attempt recovery from persistent state
        await self.recover_state()

        # 2. Initial Deployment if no orders found
        if not self.grid_order_ids:
            ticker = await self.exchange.get_market_price(self.symbol)
            if ticker:
                logger.info(f"[{self.symbol}] No active orders found. Deploying grid.")
                await self.deploy_grid(ticker)
        else:
            logger.info(
                f"[{self.symbol}] Recovered {len(self.grid_order_ids)} active orders from state."
            )

    async def recover_state(self):
        """Recover active grid orders from the OrderStateManager."""
        if not hasattr(self.order_manager, "order_state_manager"):
            return

        osm = self.order_manager.order_state_manager
        open_records = osm.get_open_orders_by_symbol(self.symbol)

        # Filter for orders belonging to this grid session (if grid_id implemented)
        # For now, we take all open orders for this symbol as grid orders
        for record in open_records:
            if record.exchange_order_id:
                self.grid_order_ids.add(record.exchange_order_id)
                self.order_metadata[record.exchange_order_id] = {
                    "price": Decimal(record.avg_price or "0"),
                    "side": TradeSide.BUY if record.side == "BUY" else TradeSide.SELL,
                    "amount": Decimal(record.quantity),
                }
                # If it's a grid order, we should also track its grid_id
                if record.grid_id:
                    self.order_metadata[record.exchange_order_id]["grid_id"] = (
                        record.grid_id
                    )

    async def stop(self, reason: str = "Manual Stop"):
        """Gracefully stop the grid and cancel orders."""
        logger.info(f"[{self.symbol}] Stopping Grid session: {reason}")
        cancel_policy = getattr(self.config, "grid_stop_policy", "cancel_open_orders")
        should_cancel = cancel_policy != "keep_open_orders"

        if should_cancel and hasattr(self.order_manager, "cancel_orders_for_symbol"):
            try:
                await self.order_manager.cancel_orders_for_symbol(self.symbol)
            except Exception as e:
                logger.warning(
                    f"[{self.symbol}] Failed to cancel grid orders during stop: {e}"
                )

        self.grid_order_ids.clear()
        self.order_metadata.clear()
        self.is_active = False

    async def deploy_grid(self, centre: Decimal):
        """Calculate and place all limit orders."""
        logger.info(f"[{self.symbol}] Deploying grid around {centre}")
        self.centre_price = centre

        # Update calculator with risk-adjusted order size
        adjusted_order_size = self._get_risk_adjusted_order_size()
        self.calculator.order_size_quote = adjusted_order_size

        levels = self.calculator.calculate(centre)

        # Convert src levels to bot_v2 limit orders and place concurrently.
        order_tasks = []
        level_context: List[Dict[str, Any]] = []
        grid_id = f"grid_{int(time.time())}"  # Unique ID for this grid session

        for idx, level in enumerate(levels):
            side = TradeSide.BUY if level.side == "buy" else TradeSide.SELL
            amount = self.calculator.order_amount(level.price)
            order_tasks.append(
                self.order_manager.create_limit_order(
                    symbol_id=self.symbol,
                    side=side,
                    amount=Decimal(str(amount)),
                    price=Decimal(str(level.price)),
                    config=self.config,
                    params={"grid_id": grid_id, "level_index": idx},
                )
            )
            level_context.append(
                {
                    "price": Decimal(str(level.price)),
                    "side": side,
                    "amount": Decimal(str(amount)),
                    "grid_id": grid_id,
                }
            )

        results = await asyncio.gather(*order_tasks, return_exceptions=True)
        placed_count = 0
        failed_count = 0
        buy_count = 0
        sell_count = 0
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"[{self.symbol}] Failed to place level {level_context[idx]['price']}: {result}"
                )
                failed_count += 1
                continue

            order_id = str(result.get("id", ""))
            if not order_id:
                failed_count += 1
                continue
            self.grid_order_ids.add(order_id)
            self.order_metadata[order_id] = level_context[idx]
            placed_count += 1
            if level_context[idx]["side"] == TradeSide.BUY:
                buy_count += 1
            else:
                sell_count += 1

        logger.info(
            f"[{self.symbol}] Grid deployment complete: placed={placed_count}, failed={failed_count}, "
            f"buy_levels={buy_count}, sell_levels={sell_count}, centre={float(centre):.4f}"
        )

    async def tick(self, ohlcv_df: Any, current_price: Optional[Decimal] = None):
        """Perform periodic maintenance (regime check, fill polling, risk guards)."""
        if not self.is_active:
            return

        ticker = current_price

        # 1. Safety Guards (Quick-Bank Strategy)
        # Calculate current session PnL
        # Note: In local_sim, we approximate equity using filled orders and current price
        # In live, we'd use exchange balance.
        # For now, we'll implement placeholders for session TP and Max DD.

        # Placeholder for session PnL calculation logic
        initial_capital = Decimal(
            str(getattr(self.config, "initial_capital", Decimal("1")))
        )
        if initial_capital <= Decimal("0"):
            initial_capital = Decimal("1")
        session_profit_pct = self.session_realized_pnl_quote / initial_capital
        session_drawdown_pct = (
            max(Decimal("0"), -self.session_realized_pnl_quote) / initial_capital
        )

        if session_profit_pct >= Decimal("0.05"):
            logger.info(
                f"[{self.symbol}] Session Take Profit (5%) hit! Banking gains and stopping."
            )
            await self.stop(reason="Quick-Bank: Take Profit Hit")
            return

        if session_drawdown_pct >= Decimal("0.07"):
            logger.warning(
                f"[{self.symbol}] Session Max Drawdown (7%) hit! Emergency shutdown."
            )
            await self.stop(reason="Quick-Bank: Max Drawdown Hit")
            return

        # 2. Regime Detection
        regime = self.regime_detector.detect(ohlcv_df)
        logger.debug(
            f"[{self.symbol}] Regime={regime.regime.value} ADX={float(regime.adx):.2f} BB_width={float(regime.bb_width):.4f}"
        )
        if regime.regime == MarketRegime.TRENDING:
            logger.warning(
                f"[{self.symbol}] Trend detected! Cancelling grid to prevent trend-following risk."
            )
            await self.stop(reason="Regime Shift: TRENDING")
            return

        # 2. Fill Detection and Counter-Order Placement
        if ticker is None:
            ticker = await self.exchange.get_market_price(self.symbol)

        if ticker:
            # Check for simulated fills if on simulated exchange
            if hasattr(self.exchange, "check_fills"):
                filled_ids = await self.exchange.check_fills(self.symbol, ticker)
                for oid in filled_ids:
                    if oid in self.grid_order_ids:
                        meta = self.order_metadata.get(oid, {})
                        await self.handle_fill(
                            order_id=oid,
                            fill_price=Decimal(str(meta.get("price", ticker))),
                            amount=Decimal(str(meta.get("amount", 0))),
                            side=meta.get("side", TradeSide.BUY),
                        )

        # 3. Drift Monitoring (Re-centering)
        if self.centre_price:
            ticker = current_price
            if ticker is None:
                ticker = await self.exchange.get_market_price(self.symbol)
            if ticker:
                drift = abs(ticker - self.centre_price)
                # Calculate spacing in absolute terms
                spacing_abs = self.centre_price * Decimal(
                    str(self.config.grid_spacing_pct)
                )

                # Check if drift exceeds trigger threshold
                if drift > (
                    spacing_abs * Decimal(str(self.config.grid_recentre_trigger))
                ):
                    logger.warning(
                        f"[{self.symbol}] Drift detected: {float(drift):.4f} > trigger. "
                        f"Re-centering grid from {self.centre_price} to {ticker}."
                    )
                    await self.stop(reason="Re-centering")
                    await self.deploy_grid(ticker)
                    # BUG FIX: Call start() instead of manually setting is_active
                    # This ensures proper state initialization
                    await self.start()

    async def handle_fill(
        self, order_id: str, fill_price: Decimal, amount: Decimal, side: TradeSide
    ):
        """
        Called when a grid order is filled. Places the corresponding counter-order.
        """
        if not self.is_active:
            return

        logger.info(
            f"[{self.symbol}] Fill detected: {side.value} {amount} @ {fill_price}"
        )

        if self.on_grid_fill:
            fill_event = {
                "symbol": self.symbol,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "order_id": order_id,
                "side": side.value,
                "price": str(fill_price),
                "amount": str(amount),
                "source": "grid",
            }
            try:
                await self.on_grid_fill(fill_event)
            except Exception as callback_error:
                logger.error(
                    f"[{self.symbol}] Failed to persist fill log event: {callback_error}",
                    exc_info=True,
                )

        self.grid_order_ids.discard(order_id)
        self.session_fill_count += 1

        notional = fill_price * amount
        if side == TradeSide.SELL:
            self.session_realized_pnl_quote += notional
            self.session_sell_qty += amount
        else:
            self.session_realized_pnl_quote -= notional
            self.session_buy_qty += amount

        # Pair this fill against opposite inventory to produce closed grid trades.
        closed_trades = self._pair_fill_into_closed_trades(
            fill_price=fill_price,
            amount=amount,
            side=side,
            filled_order_id=order_id,
        )
        if self.on_grid_trade_closed:
            for trade in closed_trades:
                try:
                    await self.on_grid_trade_closed(trade)
                except Exception as callback_error:
                    logger.error(
                        f"[{self.symbol}] Failed to persist closed grid trade: {callback_error}",
                        exc_info=True,
                    )

        # Calculate counter-order price using optimized calculator
        # Counter-order is placed exactly 1 spacing away in the opposite direction
        is_buy = side == TradeSide.BUY
        counter_side = TradeSide.SELL if is_buy else TradeSide.BUY

        # Grid spacing from config
        spacing_pct = Decimal(str(self.config.grid_spacing_pct))
        if is_buy:
            # Filled a Buy -> Place a Sell limit higher
            counter_price = fill_price * (Decimal("1") + spacing_pct)
        else:
            # Filled a Sell -> Place a Buy limit lower
            counter_price = fill_price * (Decimal("1") - spacing_pct)

        try:
            logger.info(
                f"[{self.symbol}] Placing counter-order: {counter_side.value} @ {counter_price}"
            )

            # Inherit grid_id from original metadata
            original_meta = self.order_metadata.get(order_id, {})
            grid_id = original_meta.get("grid_id")

            order = await self.order_manager.create_limit_order(
                symbol_id=self.symbol,
                side=counter_side,
                amount=amount,
                price=counter_price,
                config=self.config,
                params={"grid_id": grid_id},
            )

            new_order_id = str(order.get("id", ""))
            if new_order_id:
                self.grid_order_ids.add(new_order_id)
                self.order_metadata[new_order_id] = {
                    "price": counter_price,
                    "side": counter_side,
                    "amount": amount,
                    "grid_id": grid_id,
                }

            logger.debug(
                f"[{self.symbol}] Session grid stats: fills={self.session_fill_count}, "
                f"realized_quote={self.session_realized_pnl_quote}"
            )
        except Exception as e:
            logger.error(f"[{self.symbol}] Failed to place counter-order: {e}")
