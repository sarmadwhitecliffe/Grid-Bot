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
from decimal import Decimal
from typing import Any, Dict, List, Optional

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
        risk_manager: Optional[Any] = None
    ):
        self.symbol = symbol
        self.config = config
        self.order_manager = order_manager
        self.exchange = exchange
        self.risk_manager = risk_manager
        
        # Initialize specialized components from src/
        self.calculator = self._init_calculator()
        self.regime_detector = RegimeDetector(
            adx_threshold=config.grid_adx_threshold if hasattr(config, 'grid_adx_threshold') else 30,
            bb_width_threshold=float(config.grid_bb_width_threshold) if hasattr(config, 'grid_bb_width_threshold') else 0.04
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

    def _init_calculator(self) -> GridCalculator:
        """Initialize calculator with proper pricing steps from exchange."""
        # Note: In production, we fetch price_step from the exchange market info
        # For now, using standard 0.0001 step
        return GridCalculator(
            grid_type=GridType.GEOMETRIC,
            spacing_pct=float(getattr(self.config, 'grid_spacing_pct', 0.01)),
            num_grids_up=int(getattr(self.config, 'grid_num_grids_up', 25)),
            num_grids_down=int(getattr(self.config, 'grid_num_grids_down', 25)),
            order_size_quote=float(getattr(self.config, 'grid_order_size_quote', 100)),
            price_step=0.0001 
        )

    def _get_risk_adjusted_order_size(self) -> float:
        """Calculate order size quote adjusted by adaptive risk tier allocation."""
        base_order_size = float(getattr(self.config, 'grid_order_size_quote', 100))
        
        if not self.risk_manager:
            return base_order_size
            
        try:
            tier_info = self.risk_manager.get_tier_info(self.symbol)
            allocation_pct = float(tier_info.get("capital_allocation", 1.0))
            adjusted_size = base_order_size * allocation_pct
            
            logger.info(
                f"[{self.symbol}] Risk Adjustment: Tier={tier_info.get('tier')}, "
                f"Allocation={allocation_pct*100:.0f}%, "
                f"OrderSize: {base_order_size} -> {adjusted_size:.2f}"
            )
            return adjusted_size
        except Exception as e:
            logger.warning(f"[{self.symbol}] Failed to get risk tier info, using base order size: {e}")
            return base_order_size

    async def start(self):
        """Start the grid session."""
        if self.is_active:
            logger.warning(f"[{self.symbol}] Grid session already active.")
            return
            
        logger.info(f"[{self.symbol}] Starting Grid session.")
        self.is_active = True
        
        # 1. Initial Deployment
        ticker = await self.exchange.get_market_price(self.symbol)
        if ticker:
            logger.info(f"[{self.symbol}] Deploying grid around centre price {float(ticker):.2f}")
            await self.deploy_grid(ticker)

    async def stop(self, reason: str = "Manual Stop"):
        """Gracefully stop the grid and cancel orders."""
        logger.info(f"[{self.symbol}] Stopping Grid session: {reason}")
        cancel_policy = getattr(self.config, "grid_stop_policy", "cancel_open_orders")
        should_cancel = cancel_policy != "keep_open_orders"

        if should_cancel and hasattr(self.order_manager, "cancel_orders_for_symbol"):
            try:
                await self.order_manager.cancel_orders_for_symbol(self.symbol)
            except Exception as e:
                logger.warning(f"[{self.symbol}] Failed to cancel grid orders during stop: {e}")

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
        
        levels = self.calculator.calculate(float(centre))

        # Convert src levels to bot_v2 limit orders and place concurrently.
        order_tasks = []
        level_context: List[Dict[str, Any]] = []
        for level in levels:
            side = TradeSide.BUY if level.side == "buy" else TradeSide.SELL
            amount = self.calculator.order_amount(level.price)
            order_tasks.append(
                self.order_manager.create_limit_order(
                    symbol_id=self.symbol,
                    side=side,
                    amount=Decimal(str(amount)),
                    price=Decimal(str(level.price)),
                    config=self.config,
                )
            )
            level_context.append(
                {
                    "price": Decimal(str(level.price)),
                    "side": side,
                    "amount": Decimal(str(amount)),
                }
            )

        results = await asyncio.gather(*order_tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    f"[{self.symbol}] Failed to place level {level_context[idx]['price']}: {result}"
                )
                continue

            order_id = str(result.get("id", ""))
            if not order_id:
                continue
            self.grid_order_ids.add(order_id)
            self.order_metadata[order_id] = level_context[idx]

    async def tick(self, ohlcv_df: Any, current_price: Optional[Decimal] = None):
        """Perform periodic maintenance (regime check, fill polling, risk guards)."""
        if not self.is_active:
            return

        # 1. Safety Guards (Quick-Bank Strategy)
        # Calculate current session PnL
        # Note: In local_sim, we approximate equity using filled orders and current price
        # In live, we'd use exchange balance. 
        # For now, we'll implement placeholders for session TP and Max DD.
        
        # Placeholder for session PnL calculation logic
        initial_capital = Decimal(str(getattr(self.config, "initial_capital", Decimal("1"))))
        if initial_capital <= Decimal("0"):
            initial_capital = Decimal("1")
        session_profit_pct = float(self.session_realized_pnl_quote / initial_capital)
        session_drawdown_pct = float(max(Decimal("0"), -self.session_realized_pnl_quote) / initial_capital)
        
        if session_profit_pct >= 0.05:
            logger.info(f"[{self.symbol}] Session Take Profit (5%) hit! Banking gains and stopping.")
            await self.stop(reason="Quick-Bank: Take Profit Hit")
            return
            
        if session_drawdown_pct >= 0.07:
            logger.warning(f"[{self.symbol}] Session Max Drawdown (7%) hit! Emergency shutdown.")
            await self.stop(reason="Quick-Bank: Max Drawdown Hit")
            return

        # 2. Regime Detection
        regime = self.regime_detector.detect(ohlcv_df)
        logger.debug(f"[{self.symbol}] Regime={regime.regime.value} ADX={regime.adx:.2f} BB_width={regime.bb_width:.4f}")
        if regime.regime == MarketRegime.TRENDING:
            logger.warning(f"[{self.symbol}] Trend detected! Cancelling grid to prevent trend-following risk.")
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
                            side=meta.get("side", TradeSide.BUY)
                        )

        # 3. Drift Monitoring (Re-centering)
        if self.centre_price:
            ticker = current_price
            if ticker is None:
                ticker = await self.exchange.get_market_price(self.symbol)
            if ticker:
                drift = abs(ticker - self.centre_price)
                # Calculate spacing in absolute terms
                spacing_abs = self.centre_price * self.config.grid_spacing_pct
                
                # Check if drift exceeds trigger threshold
                if drift > (spacing_abs * Decimal(str(self.config.grid_recentre_trigger))):
                    logger.warning(
                        f"[{self.symbol}] Drift detected: {drift:.4f} > trigger. "
                        f"Re-centering grid from {self.centre_price} to {ticker}."
                    )
                    await self.stop(reason="Re-centering")
                    await self.deploy_grid(ticker)
                    self.is_active = True

    async def handle_fill(self, order_id: str, fill_price: Decimal, amount: Decimal, side: TradeSide):
        """
        Called when a grid order is filled. Places the corresponding counter-order.
        """
        if not self.is_active:
            return

        logger.info(f"[{self.symbol}] Fill detected: {side.value} {amount} @ {fill_price}")
        self.grid_order_ids.discard(order_id)
        self.session_fill_count += 1

        notional = fill_price * amount
        if side == TradeSide.SELL:
            self.session_realized_pnl_quote += notional
            self.session_sell_qty += amount
        else:
            self.session_realized_pnl_quote -= notional
            self.session_buy_qty += amount
        
        # Calculate counter-order price using optimized calculator
        # Counter-order is placed exactly 1 spacing away in the opposite direction
        is_buy = (side == TradeSide.BUY)
        counter_side = TradeSide.SELL if is_buy else TradeSide.BUY
        
        # Grid spacing from config
        spacing_pct = float(self.config.grid_spacing_pct)
        if is_buy:
            # Filled a Buy -> Place a Sell limit higher
            counter_price = fill_price * Decimal(str(1 + spacing_pct))
        else:
            # Filled a Sell -> Place a Buy limit lower
            counter_price = fill_price * Decimal(str(1 - spacing_pct))

        try:
            logger.info(f"[{self.symbol}] Placing counter-order: {counter_side.value} @ {counter_price}")
            await self.order_manager.create_limit_order(
                symbol_id=self.symbol,
                side=counter_side,
                amount=amount,
                price=counter_price,
                config=self.config
            )
            logger.debug(
                f"[{self.symbol}] Session grid stats: fills={self.session_fill_count}, "
                f"realized_quote={self.session_realized_pnl_quote}"
            )
        except Exception as e:
            logger.error(f"[{self.symbol}] Failed to place counter-order: {e}")
