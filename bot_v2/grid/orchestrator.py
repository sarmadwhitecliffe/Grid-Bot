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
from bot_v2.models.grid_state import GridState
from src.strategy.grid_calculator import GridCalculator, GridType
from src.strategy.regime_detector import RegimeDetector, MarketRegime

logger = logging.getLogger(__name__)

MAKER_FEE = Decimal("0.0002")  # 0.02% Binance Futures maker fee (limit orders)


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
        on_state_persist: Optional[Callable[[str, "GridState"], None]] = None,
        on_session_pnl_bank: Optional[Callable[[str, Decimal], Awaitable[None]]] = None,
    ):
        self.symbol = symbol
        self.config = config
        self.order_manager = order_manager
        self.exchange = exchange
        self.risk_manager = risk_manager
        self.capital_manager = capital_manager
        self.on_grid_trade_closed = on_grid_trade_closed
        self.on_grid_fill = on_grid_fill
        self.on_state_persist = on_state_persist
        self.on_session_pnl_bank = on_session_pnl_bank

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
        self._deployment_count: int = 0  # Track number of deployments
        # Open inventory lots used to pair fills into closed grid trades.
        self._open_long_lots: List[Dict[str, Any]] = []
        self._open_short_lots: List[Dict[str, Any]] = []

        # Memory management - max lot age before cleanup
        self._max_lot_age_seconds = float(os.getenv("GRID_MAX_LOT_AGE_SECONDS", "3600"))

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

        def calculate_fees(
            entry_p: Decimal, exit_p: Decimal, qty: Decimal
        ) -> tuple[Decimal, Decimal, Decimal]:
            """Calculate entry fee, exit fee, and total fees."""
            entry_fee = entry_p * qty * MAKER_FEE
            exit_fee = exit_p * qty * MAKER_FEE
            total_fees = entry_fee + exit_fee
            return entry_fee, exit_fee, total_fees

        if side == TradeSide.BUY:
            # Buy fill can close prior short inventory.
            while remaining > epsilon and self._open_short_lots:
                lot = self._open_short_lots[0]
                matched = min(remaining, lot["amount"])
                gross_pnl = (lot["entry_price"] - fill_price) * matched
                entry_fee, exit_fee, total_fees = calculate_fees(
                    lot["entry_price"], fill_price, matched
                )
                net_pnl = gross_pnl - total_fees
                closed_trades.append(
                    {
                        "timestamp": now.isoformat(),
                        "symbol": self.symbol,
                        "type": "grid_close",
                        "position_side": "SHORT",
                        "entry_price": str(lot["entry_price"]),
                        "exit_price": str(fill_price),
                        "quantity": str(matched),
                        "pnl_usd": str(net_pnl),
                        "gross_pnl_usd": str(gross_pnl),
                        "entry_fee_usd": str(entry_fee),
                        "exit_fee_usd": str(exit_fee),
                        "total_fees_usd": str(total_fees),
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
                gross_pnl = (fill_price - lot["entry_price"]) * matched
                entry_fee, exit_fee, total_fees = calculate_fees(
                    lot["entry_price"], fill_price, matched
                )
                net_pnl = gross_pnl - total_fees
                closed_trades.append(
                    {
                        "timestamp": now.isoformat(),
                        "symbol": self.symbol,
                        "type": "grid_close",
                        "position_side": "LONG",
                        "entry_price": str(lot["entry_price"]),
                        "exit_price": str(fill_price),
                        "quantity": str(matched),
                        "pnl_usd": str(net_pnl),
                        "gross_pnl_usd": str(gross_pnl),
                        "entry_fee_usd": str(entry_fee),
                        "exit_fee_usd": str(exit_fee),
                        "total_fees_usd": str(total_fees),
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
            # Use level_allocation_ratio for grid sizing (convert to Decimal for compatibility)
            level_ratio = tier_info.get("level_allocation_ratio", 1.0)
            adjusted_size = base_order_size * Decimal(str(level_ratio))

            logger.info(
                f"[{self.symbol}] Risk Adjustment: Tier={tier_info.get('tier')}, "
                f"LevelRatio={level_ratio:.0%}, "
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
        Calculate grid parameters based on capital, tier, and leverage using the new v2 model.

        Uses PositionSizer.calculate_grid_params() for grid sizing.

        Returns:
            Tuple[int, int, Decimal]: (levels_up, levels_down, order_size)

        Raises:
            InsufficientGridCapital: If capital cannot support minimum grid
        """
        from bot_v2.risk.adaptive_risk_manager import PositionSizer
        from bot_v2.models.exceptions import InsufficientCapitalError

        if not self.capital_manager or not self.risk_manager:
            # Fallback: use config values without capital constraint
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

        # Get tier and configuration
        tier_info = self.risk_manager.get_tier_info(self.symbol)
        tier_name = tier_info.get("tier", "PROBATION")

        # Get the RiskTier object
        from bot_v2.risk.adaptive_risk_manager import ALL_TIERS

        tier = next((t for t in ALL_TIERS if t.name == tier_name), ALL_TIERS[-1])

        # Get grid configuration
        configured_up = int(getattr(self.config, "grid_num_grids_up", 25))
        configured_down = int(getattr(self.config, "grid_num_grids_down", 25))
        strategy_leverage = float(getattr(self.config, "leverage", 5))

        # Get min/max order size from settings or environment
        min_order_size = float(getattr(self.config, "min_order_size_usd", 5.0))
        max_order_size = float(getattr(self.config, "max_order_size_usd", 100.0))
        min_grid_levels = int(getattr(self.config, "min_grid_levels", 10))

        # Calculate grid parameters using new v2 logic
        try:
            result = PositionSizer.calculate_grid_params(
                capital=float(capital),
                tier=tier,
                configured_up=configured_up,
                configured_down=configured_down,
                base_leverage=strategy_leverage,
                min_order_size_usd=min_order_size,
                max_order_size_usd=max_order_size,
                min_grid_levels=min_grid_levels,
            )

            levels_up = result["levels_up"]
            levels_down = result["levels_down"]
            order_size = Decimal(str(result["order_size_quote"]))

            logger.info(
                f"[{self.symbol}] Grid v2 params: {levels_up} up + {levels_down} down @ ${result['order_size_quote']}/order, "
                f"notional ${result['notional_capital']}, leverage {result['effective_leverage']}x, "
                f"tier={tier_name} ratio={result['level_allocation_ratio']:.0%}"
            )

            return (levels_up, levels_down, order_size)

        except InsufficientCapitalError as e:
            raise InsufficientGridCapital(str(e))

    async def start(self, persisted_state: Optional[GridState] = None):
        """Start the grid session.

        Args:
            persisted_state: Optional GridState loaded from grid_states.json on startup.
                           If provided, will attempt to recover orders from this state first.
        """
        if self.is_active:
            logger.warning(f"[{self.symbol}] Grid session already active.")
            return

        logger.info(f"[{self.symbol}] Starting Grid session.")
        self.is_active = True

        # 1. Check persisted state from previous session (from grid_states.json)
        if persisted_state:
            # Check if we should auto-resume based on shutdown reason
            if persisted_state.shutdown_reason:
                safety_reasons = ["PORTFOLIO_HALT", "EMERGENCY", "MAX_DD", "CRASH"]
                if any(
                    r in persisted_state.shutdown_reason.upper() for r in safety_reasons
                ):
                    logger.warning(
                        f"[{self.symbol}] Grid was stopped for safety reason "
                        f"'{persisted_state.shutdown_reason}'. Requires manual restart."
                    )
                    self.is_active = False
                    return

                # If stopped due to TRENDING, wait for regime check before deploying
                if "TRENDING" in persisted_state.shutdown_reason.upper():
                    logger.info(
                        f"[{self.symbol}] Grid was stopped due to TRENDING market. "
                        f"Will check regime before deployment."
                    )

            # Recover orders from persisted state
            if persisted_state.active_orders:
                recovered_count = 0
                for order_id, metadata in persisted_state.active_orders.items():
                    self.grid_order_ids.add(order_id)
                    self.order_metadata[order_id] = metadata
                    recovered_count += 1

                if recovered_count > 0:
                    logger.info(
                        f"[{self.symbol}] Recovered {recovered_count} orders from persisted state."
                    )

                # Restore other session data
                self.centre_price = persisted_state.centre_price
                self._deployment_count = persisted_state.deployment_count

        # 2. Also try to recover from OrderStateManager (for orders created during session)
        await self.recover_state()

        # 3. Initial Deployment if no orders found
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

        # BANK SESSION PNL BEFORE STOPPING
        # This ensures accumulated session PnL is not lost when resetting
        await self._bank_session_pnl(reason)

        # PERSIST STATE BEFORE CANCELLING ORDERS
        # This preserves order info for recovery on next startup
        await self._persist_state_for_shutdown(reason)

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

    async def _bank_session_pnl(self, reason: str = "Session End"):
        """Bank the accumulated session PnL to capital before resetting."""
        if self.session_realized_pnl_quote == Decimal("0"):
            logger.debug(f"[{self.symbol}] No session PnL to bank (0)")
            return

        pnl_to_bank = self.session_realized_pnl_quote
        logger.info(
            f"[{self.symbol}] Banking session PnL: ${pnl_to_bank:+.2f} "
            f"(fills={self.session_fill_count}, reason={reason})"
        )

        if self.on_session_pnl_bank:
            try:
                await self.on_session_pnl_bank(self.symbol, pnl_to_bank)
                logger.info(
                    f"[{self.symbol}] Session PnL ${pnl_to_bank:+.2f} successfully banked to capital"
                )
            except Exception as e:
                logger.error(
                    f"[{self.symbol}] Failed to bank session PnL ${pnl_to_bank:+.2f}: {e}",
                    exc_info=True,
                )
        else:
            logger.warning(
                f"[{self.symbol}] No on_session_pnl_bank callback - PnL not banked!"
            )

    async def _persist_state_for_shutdown(self, reason: str):
        """Persist grid state before shutdown for recovery on next startup."""
        from bot_v2.models.grid_state import GridState

        # Determine if we should auto-resume based on shutdown reason
        safety_reasons = ["PORTFOLIO_HALT", "EMERGENCY", "MAX_DD", "CRASH"]
        should_resume = not any(r in reason.upper() for r in safety_reasons)

        # Determine shutdown reason for smart resume
        shutdown_reason = reason

        state = GridState(
            symbol_id=self.symbol,
            is_active=False,  # Mark as inactive (will be set true on resume)
            centre_price=self.centre_price,
            active_orders={
                str(oid): self.order_metadata.get(str(oid), {})
                for oid in self.grid_order_ids
            },
            session_start_time=None,  # Will be set on next start
            grid_fills=self.session_fill_count,
            counter_fills=0,
            last_tick_time=datetime.now(timezone.utc),
            shutdown_time=datetime.now(timezone.utc),
            shutdown_reason=shutdown_reason,
            should_resume=should_resume,
            deployment_count=getattr(self, "_deployment_count", 0),
        )

        if self.on_state_persist:
            try:
                self.on_state_persist(self.symbol, state)
                logger.info(
                    f"[{self.symbol}] Persisted grid state for shutdown: "
                    f"orders={len(state.active_orders)}, reason={reason}, should_resume={should_resume}"
                )
            except Exception as e:
                logger.error(
                    f"[{self.symbol}] Failed to persist state on shutdown: {e}"
                )

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

        # Bank session PnL before resetting
        await self._bank_session_pnl(reason=reason)

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

        # Reset session PnL AFTER banking (already done in _bank_session_pnl)
        # Keeping reset here for safety - _bank_session_pnl logs the value before clearing
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
        self._deployment_count += 1  # Track deployment count

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

    def _cleanup_stale_lots(self) -> Tuple[int, int]:
        """
        Clean up stale lots from _open_long_lots and _open_short_lots.

        Lots older than _max_lot_age_seconds are removed to prevent memory growth
        from failed/unmatched fills.

        Returns:
            Tuple of (long_lots_removed, short_lots_removed)
        """
        if not self._open_long_lots and not self._open_short_lots:
            return 0, 0

        now = datetime.now(timezone.utc)
        long_removed = 0
        short_removed = 0

        # Clean stale long lots
        self._open_long_lots = [
            lot
            for lot in self._open_long_lots
            if (now - datetime.fromisoformat(lot["entry_time"])).total_seconds()
            < self._max_lot_age_seconds
        ]
        long_removed = len(self._open_long_lots)

        # Clean stale short lots
        self._open_short_lots = [
            lot
            for lot in self._open_short_lots
            if (now - datetime.fromisoformat(lot["entry_time"])).total_seconds()
            < self._max_lot_age_seconds
        ]
        short_removed = len(self._open_short_lots)

        return long_removed, short_removed

    async def tick(self, ohlcv_df: Any, current_price: Optional[Decimal] = None):
        """Perform periodic maintenance (regime check, fill polling, risk guards)."""
        if not self.is_active:
            if await self._maybe_restart_grid():
                logger.info(f"[{self.symbol}] Grid auto-restarted, continuing tick.")
            else:
                return

        ticker = current_price

        # Clean up stale lots to prevent memory growth
        long_removed, short_removed = self._cleanup_stale_lots()
        if long_removed > 0 or short_removed > 0:
            logger.debug(
                f"[{self.symbol}] Cleaned up stale lots: {long_removed} long, {short_removed} short"
            )

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
        fill_fee = notional * MAKER_FEE  # Fee for this fill
        if side == TradeSide.SELL:
            self.session_realized_pnl_quote += notional - fill_fee
            self.session_sell_qty += amount
        else:
            self.session_realized_pnl_quote -= notional - fill_fee
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
