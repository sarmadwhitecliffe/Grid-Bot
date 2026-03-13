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
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.models.enums import TradeSide
from bot_v2.models.exceptions import InsufficientGridCapital
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
        capital_manager: Optional[Any] = None,
        on_grid_trade_closed: Optional[
            Callable[[Dict[str, Any]], Awaitable[None]]
        ] = None,
        on_grid_fill: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ):
        self.symbol = symbol
        self.config = config
        self.order_manager = order_manager
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.capital_manager = capital_manager
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
        self.last_reinvest_time: Optional[float] = None
        self.session_reinvest_count = 0
        self.last_stop_time: Optional[float] = None
        self.last_stop_reason: Optional[str] = None
        self._last_regime_check_time: Optional[float] = None
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
                        "duration_sec": (
                            now - datetime.fromisoformat(lot["entry_time"])
                        ).total_seconds(),
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
                        "duration_sec": (
                            now - datetime.fromisoformat(lot["entry_time"])
                        ).total_seconds(),
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

    async def _calculate_grid_parameters(self) -> Tuple[int, int, Decimal]:
        """
        Calculate realistic grid parameters based on allocated capital.

        Formula:
            allocated_margin = capital × tier_allocation
            margin_per_level = min_notional / leverage
            max_levels = allocated_margin / margin_per_level

        Returns:
            Tuple[int, int, Decimal]: (levels_up, levels_down, order_size)

        Raises:
            InsufficientGridCapital: If capital cannot support minimum grid (2 levels)
        """
        if not self.capital_manager or not self.risk_manager:
            order_size = Decimal(
                str(getattr(self.config, "grid_order_size_quote", 100))
            )
            num_up = int(getattr(self.config, "grid_num_grids_up", 25))
            num_down = int(getattr(self.config, "grid_num_grids_down", 25))
            logger.info(
                f"[{self.symbol}] No capital_manager/risk_manager - using config values: "
                f"{num_up} up + {num_down} down @ ${order_size}/order"
            )
            return (num_up, num_down, order_size)

        capital = await self.capital_manager.get_capital(self.symbol)

        if capital <= Decimal("0"):
            raise InsufficientGridCapital(
                f"[{self.symbol}] Capital is ${capital:.2f} - cannot deploy grid"
            )

        tier_info = self.risk_manager.get_tier_info(self.symbol)
        allocation_pct = Decimal(str(tier_info.get("capital_allocation", 1.0)))

        leverage = Decimal(str(tier_info.get("max_leverage", 1)))
        grid_leverage = getattr(self.config, "grid_leverage", None)
        if grid_leverage is not None:
            try:
                leverage = Decimal(str(grid_leverage))
            except Exception:
                pass  # Keep default leverage if conversion fails

        allocated_margin = capital * allocation_pct

        min_notional = Decimal(os.getenv("BOT_MIN_NOTIONAL_USD", "5.0"))

        margin_per_level = min_notional / leverage

        max_levels = int(allocated_margin / margin_per_level)

        if max_levels < 2:
            required_capital = (2 * margin_per_level) / allocation_pct
            raise InsufficientGridCapital(
                f"[{self.symbol}] Insufficient capital for grid deployment. "
                f"Available: ${capital:.2f}, Allocated: ${allocated_margin:.2f}, "
                f"Min notional: ${min_notional:.2f}, Leverage: {leverage}x. "
                f"Need minimum ${required_capital:.2f} capital for 2-level grid."
            )

        config_up = int(getattr(self.config, "grid_num_grids_up", 25))
        config_down = int(getattr(self.config, "grid_num_grids_down", 25))
        config_total = config_up + config_down

        if max_levels < config_total:
            actual_up = max_levels // 2 + (max_levels % 2)
            actual_down = max_levels // 2
            logger.warning(
                f"[{self.symbol}] Grid levels reduced from {config_up}+{config_down} to "
                f"{actual_up}+{actual_down} due to capital constraints. "
                f"Capital: ${capital:.2f}, Allocation: {allocation_pct * 100:.0f}%, "
                f"Margin available: ${allocated_margin:.2f}, Max levels: {max_levels}"
            )
            levels_up, levels_down = actual_up, actual_down
        else:
            levels_up, levels_down = config_up, config_down

        order_size = min_notional

        logger.info(
            f"[{self.symbol}] Grid parameters: {levels_up} up + {levels_down} down @ ${order_size:.2f}/order. "
            f"Capital: ${capital:.2f}, Allocation: {allocation_pct * 100:.0f}%, Leverage: {leverage}x, "
            f"Margin per level: ${margin_per_level:.2f}, Max levels: {max_levels}"
        )

        return (levels_up, levels_down, order_size)

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
                # Pre-deployment regime check: skip grid if market is trending
                if await self._check_regime_before_deployment():
                    logger.warning(
                        f"[{self.symbol}] Skipping grid deployment - market is TRENDING. "
                        f"Will retry on next tick."
                    )
                    return

                logger.info(f"[{self.symbol}] No active orders found. Deploying grid.")
                await self.deploy_grid(ticker)
        else:
            logger.info(
                f"[{self.symbol}] Recovered {len(self.grid_order_ids)} active orders from state."
            )

    async def _check_regime_before_deployment(self) -> bool:
        """
        Check market regime before grid deployment.

        Returns:
            True if market is TRENDING (should skip deployment).
            False if market is RANGING or UNKNOWN (can deploy).
        """
        try:
            import pandas as pd
            import time as time_module

            # Fetch OHLCV data for regime detection
            timeframe = getattr(self.config, "timeframe", "15m")
            ohlcv_data = await self.exchange.fetch_ohlcv(self.symbol, timeframe, 100)

            if ohlcv_data is None:
                logger.warning(
                    f"[{self.symbol}] No OHLCV data received for regime check. "
                    f"Allowing deployment."
                )
                return False

            # Handle both list and DataFrame formats (simulated exchange returns DataFrame)
            if isinstance(ohlcv_data, pd.DataFrame):
                df = ohlcv_data
            else:
                df = pd.DataFrame(
                    ohlcv_data,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )

            if len(df) < 20:
                logger.warning(
                    f"[{self.symbol}] Insufficient OHLCV data for regime check "
                    f"({len(df)} rows). Allowing deployment."
                )
                return False

            # Detect regime
            regime_info = self.regime_detector.detect(df)
            logger.info(
                f"[{self.symbol}] Pre-deployment regime check: "
                f"regime={regime_info.regime.value} "
                f"ADX={float(regime_info.adx):.2f} BB_w={float(regime_info.bb_width):.4f}"
            )

            if regime_info.regime == MarketRegime.TRENDING:
                return True

            return False

        except Exception as e:
            logger.warning(
                f"[{self.symbol}] Regime check failed: {e}. Allowing deployment."
            )
            return False

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
        import time as time_module

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
        self.last_stop_time = time_module.time()
        self.last_stop_reason = reason

    async def _bank_and_reinvest(self, reason: str = "Session TP"):
        """Bank realized gains, reset session trackers, and deploy a new grid.

        This enables autonomous continuous trading by automatically starting
        a fresh profit cycle after hitting session take profit targets.
        """
        import time as time_module

        current_time = time_module.time()
        min_interval = getattr(self.config, "grid_reinvest_min_interval_seconds", 60)

        if self.last_reinvest_time is not None:
            elapsed = current_time - self.last_reinvest_time
            if elapsed < min_interval:
                logger.info(
                    f"[{self.symbol}] Re-invest cooldown active ({elapsed:.1f}s < {min_interval}s). "
                    f"Stopping instead."
                )
                await self.stop(reason=f"{reason}: Cooldown Active")
                return

        logger.info(
            f"[{self.symbol}] Session Take Profit hit! Banking gains and re-deploying grid. "
            f"Session PnL: {float(self.session_realized_pnl_quote):.2f}, "
            f"Reinvest count: {self.session_reinvest_count}"
        )

        cancel_policy = getattr(self.config, "grid_stop_policy", "cancel_open_orders")
        should_cancel = cancel_policy != "keep_open_orders"

        if should_cancel and hasattr(self.order_manager, "cancel_orders_for_symbol"):
            try:
                await self.order_manager.cancel_orders_for_symbol(self.symbol)
            except Exception as e:
                logger.warning(
                    f"[{self.symbol}] Failed to cancel grid orders during re-invest: {e}"
                )

        self.grid_order_ids.clear()
        self.order_metadata.clear()

        self.session_realized_pnl_quote = Decimal("0")
        self.session_fill_count = 0
        self.session_buy_qty = Decimal("0")
        self.session_sell_qty = Decimal("0")
        self._open_long_lots.clear()
        self._open_short_lots.clear()

        self.last_reinvest_time = current_time
        self.session_reinvest_count += 1

        try:
            ticker = await self.exchange.get_market_price(self.symbol)
            if ticker:
                await self.deploy_grid(Decimal(str(ticker)))
                logger.info(
                    f"[{self.symbol}] Grid re-deployed successfully after {reason}. "
                    f"New centre: {float(self.centre_price) if self.centre_price else 'N/A'}"
                )
            else:
                logger.error(
                    f"[{self.symbol}] Failed to get market price for re-deployment. Stopping."
                )
                self.is_active = False
        except Exception as e:
            logger.error(
                f"[{self.symbol}] Failed to re-deploy grid after {reason}: {e}"
            )
            self.is_active = False

    async def _maybe_restart_grid(self) -> bool:
        """Check if a stopped grid should auto-restart.

        Returns True if grid was restarted, False otherwise.
        """
        import time as time_module

        if self.is_active:
            return False

        if not getattr(self.config, "grid_auto_restart", True):
            return False

        if self.last_stop_time is None:
            return False

        # Check if stopped due to TRENDING - use shorter cooldown, allow retry with 0 fills
        is_trending_stop = (
            self.last_stop_reason is not None
            and "TRENDING" in self.last_stop_reason.upper()
        )

        if is_trending_stop:
            # Shorter cooldown for trending stops - retry frequently to catch ranging markets
            min_interval = getattr(
                self.config, "grid_trending_retry_interval_seconds", 30
            )
        else:
            min_interval = getattr(
                self.config, "grid_reinvest_min_interval_seconds", 60
            )

        elapsed = time_module.time() - self.last_stop_time
        if elapsed < min_interval:
            if is_trending_stop:
                logger.debug(
                    f"[{self.symbol}] Trend retry cooldown active ({elapsed:.1f}s < {min_interval}s)."
                )
            return False

        # Allow retry with 0 fills if stopped due to trending (couldn't deploy)
        if self.session_fill_count == 0:
            if is_trending_stop:
                logger.info(
                    f"[{self.symbol}] Retrying grid after trending stop (0 fills)."
                )
            else:
                logger.debug(
                    f"[{self.symbol}] No session fills, skipping auto-restart."
                )
                return False

        initial_capital = Decimal(
            str(getattr(self.config, "initial_capital", Decimal("1")))
        )
        if initial_capital <= Decimal("0"):
            initial_capital = Decimal("1")

        session_pnl_pct = self.session_realized_pnl_quote / initial_capital
        max_dd_pct = getattr(self.config, "grid_session_max_dd_pct", Decimal("0.07"))

        if session_pnl_pct < -max_dd_pct:
            logger.warning(
                f"[{self.symbol}] Session PnL {float(session_pnl_pct) * 100:.2f}% below max DD threshold, "
                f"not auto-restarting."
            )
            return False

        logger.info(
            f"[{self.symbol}] Auto-restarting grid after stop. "
            f"Session: {self.session_fill_count} fills, PnL: {float(self.session_realized_pnl_quote):.2f}"
        )

        await self.start()
        return True

    async def deploy_grid(self, centre: Decimal):
        """Calculate and place all limit orders."""
        logger.info(f"[{self.symbol}] Deploying grid around {centre}")
        self.centre_price = centre

        try:
            if getattr(self.config, "grid_capital_constraint", True):
                (
                    levels_up,
                    levels_down,
                    order_size,
                ) = await self._calculate_grid_parameters()
                self.calculator.num_grids_up = levels_up
                self.calculator.num_grids_down = levels_down
                self.calculator.order_size_quote = order_size
            else:
                order_size = self._get_risk_adjusted_order_size()
                self.calculator.order_size_quote = order_size
        except InsufficientGridCapital as e:
            logger.error(str(e))
            self.is_active = False
            return

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
            if await self._maybe_restart_grid():
                logger.info(f"[{self.symbol}] Grid auto-restarted, continuing tick.")
            else:
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

        session_tp_pct = getattr(self.config, "grid_session_tp_pct", Decimal("0.05"))
        session_max_dd_pct = getattr(
            self.config, "grid_session_max_dd_pct", Decimal("0.07")
        )
        session_profit_pct = self.session_realized_pnl_quote / initial_capital
        session_drawdown_pct = (
            max(Decimal("0"), -self.session_realized_pnl_quote) / initial_capital
        )

        if session_profit_pct >= session_tp_pct:
            logger.info(
                f"[{self.symbol}] Session Take Profit ({float(session_tp_pct) * 100:.1f}%) hit! "
                f"Profit: {float(session_profit_pct) * 100:.2f}%"
            )
            if getattr(self.config, "grid_session_tp_reinvest", True):
                await self._bank_and_reinvest(reason="Session TP")
            else:
                await self.stop(reason="Quick-Bank: Take Profit Hit")
            return

        if session_drawdown_pct >= session_max_dd_pct:
            logger.warning(
                f"[{self.symbol}] Session Max Drawdown ({float(session_max_dd_pct) * 100:.1f}%) hit! "
                f"Drawdown: {float(session_drawdown_pct) * 100:.2f}%"
            )
            if getattr(self.config, "grid_session_tp_reinvest", True):
                await self._bank_and_reinvest(reason="Session Max DD")
            else:
                await self.stop(reason="Quick-Bank: Max Drawdown Hit")
            return

        # 2. Regime Detection (throttled to reduce false positives)
        import time as time_module

        regime_check_interval = getattr(
            self.config, "grid_regime_check_interval_seconds", 300
        )  # Default 5 minutes

        current_time = time_module.time()
        should_check_regime = (
            self._last_regime_check_time is None
            or (current_time - self._last_regime_check_time) >= regime_check_interval
        )

        if should_check_regime:
            self._last_regime_check_time = current_time
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
        else:
            logger.debug(
                f"[{self.symbol}] Regime check skipped (throttled, next in "
                f"{regime_check_interval - (current_time - self._last_regime_check_time):.0f}s)"
            )

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
