"""
src/backtest/grid_backtester.py
--------------------------------
Bar-by-bar grid strategy simulation engine.

Simulates the full limit-order lifecycle using historical OHLCV data.
Uses the same RegimeDetector and GridCalculator as the live bot to
ensure backtest logic mirrors production behaviour exactly.

Fill rules:
  Buy limit at P fills when bar.low  <= P.
  Sell limit at P fills when bar.high >= P.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Tuple

import pandas as pd

from config.settings import GridBotSettings
from src.strategy import GridType, MarketRegime
from src.strategy.grid_calculator import GridCalculator
from src.strategy.regime_detector import RegimeDetector

logger = logging.getLogger(__name__)

MAKER_FEE: float = 0.0002  # 0.02% Binance Futures maker fee (limit orders)
SLIPPAGE: float = 0.0001  # 0.01% adverse slippage on fills
MIN_ORDER_VALUE: float = 5.0  # Minimum USDT value per grid order


@dataclass
class SimOrder:
    """Represents a single simulated limit order."""

    price: float
    side: str  # 'buy' or 'sell'
    amount: float  # base currency quantity
    level_index: int
    position_side: str  # 'LONG' or 'SHORT'


@dataclass
class BacktestTrade:
    """
    A completed fill event in the simulation.

    Attributes:
        bar_index:    Index of the OHLCV bar where the fill occurred.
        timestamp:    UTC timestamp of the bar.
        side:         'buy' or 'sell'.
        position_side: 'LONG' or 'SHORT'.
        entry_price:  Actual fill price after slippage.
        amount:       Base currency quantity filled.
        fee_usdt:     Maker fee charged in USDT.
        realized_pnl: Net PnL for a completed buy->sell cycle (None for buys/opening).
        equity_after: Total equity value of the account immediately after this trade.
        exit_reason:  Optional string describing why the trade happened (e.g. 'grid', 'liquidation').
        open_orders_at_fill: Number of other open limit orders when this fill occurred.
    """

    bar_index: int
    timestamp: pd.Timestamp
    side: str
    position_side: str
    entry_price: float
    amount: float
    fee_usdt: float
    realized_pnl: Optional[float] = None
    equity_after: float = 0.0
    exit_reason: str = "grid"
    open_orders_at_fill: int = 0


@dataclass
class BacktestResult:
    """
    Aggregated output of a full backtest run.

    Attributes:
        trades:            All fill events.
        equity_curve:      Equity value sampled at the end of each simulated bar.
        open_orders_curve: Number of open orders at the end of each simulated bar.
        initial_capital:   Starting equity.
        final_equity:      Ending equity after simulation completes.
        total_fees_usdt:   Total maker fees paid during the simulation.
        funding_fees_usdt: Total funding costs paid.
        long_liquidations: Number of LONG position liquidations.
        short_liquidations: Number of SHORT position liquidations.
    """

    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    open_orders_curve: List[int] = field(default_factory=list)
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_fees_usdt: float = 0.0
    funding_fees_usdt: float = 0.0
    long_liquidations: int = 0
    short_liquidations: int = 0


class GridBacktester:
    """
    Simulate the grid bot strategy bar-by-bar on historical OHLCV data.

    Reuses RegimeDetector and GridCalculator directly so the simulation
    uses identical logic to the live trading loop.
    """

    def __init__(
        self,
        settings: GridBotSettings,
        initial_capital: float = 2000.0,
        maker_fee: float = MAKER_FEE,
        slippage: float = SLIPPAGE,
        indicator_warmup: int = 50,
    ) -> None:
        """
        Configure the backtester.

        Args:
            settings:         Validated bot settings (grid params, risk limits).
            initial_capital:  Starting USDT equity for the simulation.
            maker_fee:        Fractional maker fee applied on each fill.
            slippage:         Fractional adverse slippage applied on each fill.
            indicator_warmup: Number of leading candles skipped to allow
                              indicators (ADX, BB) to initialise properly.
        """
        self.settings = settings
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.slippage = slippage
        self.indicator_warmup = indicator_warmup

        self.regime_detector = RegimeDetector(
            adx_threshold=settings.ADX_THRESHOLD,
            bb_width_threshold=getattr(settings, "bb_width_threshold", 0.04),
        )
        leverage = settings.LEVERAGE
        available_capital = self.initial_capital * leverage
        max_grids = int(available_capital / MIN_ORDER_VALUE)
        num_grids = min(settings.NUM_GRIDS_UP, max_grids)

        self.calculator = GridCalculator(
            grid_type=GridType(settings.GRID_TYPE),
            spacing_pct=settings.GRID_SPACING_PCT,
            spacing_abs=settings.GRID_SPACING_ABS,
            num_grids_up=num_grids,
            num_grids_down=num_grids,
            order_size_quote=settings.ORDER_SIZE_QUOTE,
            price_step=0.0001,
            lower_bound=settings.LOWER_BOUND,
            upper_bound=settings.UPPER_BOUND,
        )

    def run(self, ohlcv_df: pd.DataFrame) -> BacktestResult:
        """
        Execute the bar-by-bar simulation with futures dual-side support.

        Args:
            ohlcv_df: DataFrame of historical candles.

        Returns:
            BacktestResult: All trades, equity curve, and summary metrics.
        """
        # Pre-calculate indicators for the whole DF to avoid O(N^2) in the loop
        from ta.trend import ADXIndicator
        from ta.volatility import BollingerBands

        adx_series = (
            ADXIndicator(
                high=ohlcv_df["high"],
                low=ohlcv_df["low"],
                close=ohlcv_df["close"],
                window=self.regime_detector.adx_period,
            )
            .adx()
            .ffill()
            .fillna(0)
        )

        bb = BollingerBands(
            close=ohlcv_df["close"],
            window=self.regime_detector.bb_period,
            window_dev=self.regime_detector.bb_std,
        )
        bb_mavg = bb.bollinger_mavg().ffill().fillna(0)
        bb_hband = bb.bollinger_hband().ffill().fillna(0)
        bb_lband = bb.bollinger_lband().ffill().fillna(0)

        result = BacktestResult(initial_capital=self.initial_capital)
        equity: float = self.initial_capital
        peak_equity: float = equity
        open_orders: List[SimOrder] = []
        centre_price: Optional[float] = None

        # Dual-side inventory tracking (FIFO)
        # List of (entry_price, amount)
        long_inventory: List[Tuple[float, float]] = []
        short_inventory: List[Tuple[float, float]] = []

        last_funding_time: Optional[pd.Timestamp] = None

        for i in range(self.indicator_warmup, len(ohlcv_df)):
            bar = ohlcv_df.iloc[i]
            current_time = bar["timestamp"]
            current_close = float(bar["close"])
            bar_low = float(bar["low"])
            bar_high = float(bar["high"])

            # 1. Funding Rates (TASK-501)
            if last_funding_time is not None:
                hours_passed = (current_time - last_funding_time).total_seconds() / 3600
                if hours_passed >= self.settings.FUNDING_INTERVAL_HOURS:
                    # Apply funding to all open positions
                    # Simplified: 0.01% per interval
                    funding_rate = 0.0001

                    long_val = sum(float(p * a) for p, a in long_inventory)
                    short_val = sum(float(p * a) for p, a in short_inventory)

                    # Longs pay shorts if funding is positive
                    # Here we just deduct from both as a conservative "cost"
                    funding_cost = float(long_val + short_val) * funding_rate
                    equity -= funding_cost
                    result.funding_fees_usdt += funding_cost
                    last_funding_time = current_time
            else:
                last_funding_time = current_time

            # 2. Liquidation Check (TASK-504)
            # Isolated margin liquidation price approximation:
            # Long Liq = Entry * (1 - 1/Leverage + MaintenanceMargin)
            # Short Liq = Entry * (1 + 1/Leverage - MaintenanceMargin)
            mm = 0.005  # 0.5% maintenance margin

            # Check Longs
            for entry_price, amount in long_inventory[:]:
                liq_price = entry_price * (
                    Decimal(1) - Decimal(1 / self.settings.LEVERAGE) + Decimal(str(mm))
                )
                if bar_low <= liq_price:
                    loss = float(liq_price - entry_price) * float(amount)
                    equity += loss  # loss is negative
                    long_inventory.remove((entry_price, amount))
                    logger.debug("Bar %d: LONG liquidated at %.2f", i, liq_price)
                    result.long_liquidations += 1
                    result.trades.append(
                        BacktestTrade(
                            bar_index=i,
                            timestamp=current_time,
                            side="sell",
                            position_side="LONG",
                            entry_price=liq_price,
                            amount=amount,
                            fee_usdt=0,
                            realized_pnl=loss,
                            equity_after=equity,
                            exit_reason="liquidation",
                        )
                    )

            # Check Shorts
            for entry_price, amount in short_inventory[:]:
                liq_price = entry_price * (
                    Decimal(1) + Decimal(1 / self.settings.LEVERAGE) - Decimal(str(mm))
                )
                if bar_high >= liq_price:
                    loss = float(entry_price - liq_price) * float(amount)
                    equity += loss  # loss is negative
                    short_inventory.remove((entry_price, amount))
                    logger.debug("Bar %d: SHORT liquidated at %.2f", i, liq_price)
                    result.short_liquidations += 1
                    result.trades.append(
                        BacktestTrade(
                            bar_index=i,
                            timestamp=current_time,
                            side="buy",
                            position_side="SHORT",
                            entry_price=liq_price,
                            amount=amount,
                            fee_usdt=0,
                            realized_pnl=loss,
                            equity_after=equity,
                            exit_reason="liquidation",
                        )
                    )

            # 3. Drift / re-centre check
            # Use pre-calculated indicators for regime detection
            adx_val = adx_series.iloc[i]
            mid = bb_mavg.iloc[i]
            if mid > 0:
                bb_width = (bb_hband.iloc[i] - bb_lband.iloc[i]) / mid
            else:
                bb_width = 0.0

            ranging = (
                adx_val < self.regime_detector.adx_threshold
                and bb_width < self.regime_detector.bb_width_threshold
            )
            regime_type = MarketRegime.RANGING if ranging else MarketRegime.TRENDING

            if centre_price is not None and open_orders:
                spacing = max(self.settings.GRID_SPACING_ABS, 0.01)
                drift = abs(current_close - centre_price) / spacing
                if drift > self.settings.RECENTRE_TRIGGER:
                    open_orders.clear()
                    centre_price = None

            # 4. Regime gate
            if regime_type == MarketRegime.TRENDING:
                open_orders.clear()
                centre_price = None
                result.equity_curve.append(equity)
                continue

            # 5. Deploy grid if no open orders
            if not open_orders:
                centre_price = current_close
                levels = self.calculator.calculate(centre_price)
                # In dual-side:
                # Levels above centre -> side="sell", position_side="SHORT" (Opening Short)
                # Levels below centre -> side="buy", position_side="LONG" (Opening Long)
                for lvl in levels[: self.settings.MAX_OPEN_ORDERS]:
                    amount = self.calculator.order_amount(lvl.price)
                    pos_side = "LONG" if lvl.side == "buy" else "SHORT"
                    open_orders.append(
                        SimOrder(
                            price=lvl.price,
                            side=lvl.side,
                            amount=amount,
                            level_index=lvl.level_index,
                            position_side=pos_side,
                        )
                    )
                logger.debug(
                    "Bar %d: dual-side grid deployed around %.2f", i, centre_price
                )

            # 6. Fill simulation
            newly_filled: List[SimOrder] = []
            counter_orders: List[SimOrder] = []

            for order in open_orders:
                filled = False
                fill_price = order.price

                if order.side == "buy" and bar_low <= order.price:
                    fill_price = order.price * (
                        Decimal(1) - Decimal(str(self.slippage))
                    )
                    filled = True
                elif order.side == "sell" and bar_high >= order.price:
                    fill_price = order.price * (
                        Decimal(1) + Decimal(str(self.slippage))
                    )
                    filled = True

                if not filled:
                    continue

                fee = float(fill_price * order.amount) * self.maker_fee
                equity -= fee
                result.total_fees_usdt += fee

                trade = BacktestTrade(
                    bar_index=i,
                    timestamp=current_time,
                    side=order.side,
                    position_side=order.position_side,
                    entry_price=fill_price,
                    amount=order.amount,
                    fee_usdt=fee,
                    equity_after=equity,
                    open_orders_at_fill=len(open_orders)
                    - 1,  # Number of other active orders
                )

                # Logic for LONG side
                if order.position_side == "LONG":
                    if order.side == "buy":  # Opening/Adding to Long
                        long_inventory.append((fill_price, order.amount))
                        # Place counter sell order (Reducing Long)
                        counter_price = self.calculator._price(order.price, 1, "up")
                        counter_orders.append(
                            SimOrder(
                                price=counter_price,
                                side="sell",
                                amount=order.amount,
                                level_index=order.level_index,
                                position_side="LONG",
                            )
                        )
                    else:  # side="sell" -> Closing/Reducing Long
                        if long_inventory:
                            entry_p, _ = long_inventory.pop(0)
                            pnl = float(fill_price - entry_p) * float(order.amount)
                            equity += pnl
                            trade.realized_pnl = pnl
                        # Place counter buy order (Opening Long)
                        counter_price = self.calculator._price(order.price, 1, "down")
                        counter_orders.append(
                            SimOrder(
                                price=counter_price,
                                side="buy",
                                amount=order.amount,
                                level_index=order.level_index,
                                position_side="LONG",
                            )
                        )

                # Logic for SHORT side
                else:  # position_side == "SHORT"
                    if order.side == "sell":  # Opening/Adding to Short
                        short_inventory.append((fill_price, order.amount))
                        # Place counter buy order (Reducing Short)
                        counter_price = self.calculator._price(order.price, 1, "down")
                        counter_orders.append(
                            SimOrder(
                                price=counter_price,
                                side="buy",
                                amount=order.amount,
                                level_index=order.level_index,
                                position_side="SHORT",
                            )
                        )
                    else:  # side="buy" -> Closing/Reducing Short
                        if short_inventory:
                            entry_p, _ = short_inventory.pop(0)
                            pnl = float(entry_p - fill_price) * float(order.amount)
                            equity += pnl
                            trade.realized_pnl = pnl
                        # Place counter sell order (Opening Short)
                        counter_price = self.calculator._price(order.price, 1, "up")
                        counter_orders.append(
                            SimOrder(
                                price=counter_price,
                                side="sell",
                                amount=order.amount,
                                level_index=order.level_index,
                                position_side="SHORT",
                            )
                        )

                result.trades.append(trade)
                newly_filled.append(order)

            # Update order book.
            for filled_order in newly_filled:
                open_orders.remove(filled_order)
            open_orders.extend(counter_orders)
            open_orders = open_orders[: self.settings.MAX_OPEN_ORDERS]

            # 7. Drawdown circuit breaker.
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown >= self.settings.MAX_DRAWDOWN_PCT:
                logger.warning("Bar %d: max drawdown %.2f%% hit", i, drawdown * 100)
                result.equity_curve.append(equity)
                result.open_orders_curve.append(len(open_orders))
                break

            result.equity_curve.append(equity)
            result.open_orders_curve.append(len(open_orders))

        result.final_equity = equity

        # Log liquidation summary if any occurred
        total_liquidations = result.long_liquidations + result.short_liquidations
        if total_liquidations > 0:
            logger.info(
                "Backtest complete: %d LONG liquidations, %d SHORT liquidations (total: %d)",
                result.long_liquidations,
                result.short_liquidations,
                total_liquidations,
            )

        return result
