"""
src/risk/risk_manager.py
------------------------
Five circuit breakers that protect capital by evaluating risk conditions
on every polling tick and returning the appropriate RiskAction.

Priority order (most severe checked first):
1. EMERGENCY_CLOSE -- max drawdown exceeded.
2. STOP_LOSS       -- price below hard stop level.
3. TAKE_PROFIT     -- cumulative profit target reached.
4. PAUSE_ADX       -- ADX above threshold (trending market).
5. RECENTRE        -- price drifted too far from grid centre.
"""

import logging

from config.settings import GridBotSettings
from src.oms import RiskAction

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Evaluates all risk conditions on each tick.

    Returns a RiskAction enum value that the main loop uses to decide
    whether to cancel orders, re-centre the grid, or halt trading.
    """

    def __init__(
        self,
        settings: GridBotSettings,
        initial_equity: float,
    ) -> None:
        """
        Initialise the RiskManager.

        Args:
            settings:       Validated bot settings containing all risk thresholds.
            initial_equity: Starting account equity used for drawdown calculation.
        """
        self.settings = settings
        self.initial_equity = initial_equity
        self.start_equity = initial_equity
        self.peak_equity = initial_equity

    def evaluate(
        self,
        current_price: float,
        current_equity: float,
        centre_price: float,
        adx: float,
        grid_spacing_abs: float,
    ) -> RiskAction:
        """
        Evaluate all risk rules in priority order.

        Args:
            current_price:    Latest market price.
            current_equity:   Current account equity in USDT.
            centre_price:     The price around which the grid is centred.
            adx:              Current ADX value from RegimeDetector.
            grid_spacing_abs: Absolute price per grid level (for drift calc).

        Returns:
            RiskAction: The highest-priority triggered action, or NONE.
        """
        self.peak_equity = max(self.peak_equity, current_equity)

        # 1. Emergency drawdown (highest priority).
        drawdown = (self.peak_equity - current_equity) / self.peak_equity
        if drawdown >= self.settings.MAX_DRAWDOWN_PCT:
            logger.critical(
                "MAX DRAWDOWN HIT: %.2f%%. Triggering emergency close.",
                drawdown * 100,
            )
            return RiskAction.EMERGENCY_CLOSE

        # 2. Stop-loss (price below hard lower-bound threshold).
        if self.settings.LOWER_BOUND is not None:
            stop_level = self.settings.LOWER_BOUND * (
                1 - self.settings.STOP_LOSS_PCT
            )
            if current_price < stop_level:
                logger.warning(
                    "STOP LOSS: price %.2f < stop level %.2f",
                    current_price,
                    stop_level,
                )
                return RiskAction.STOP_LOSS

        # 3. Take-profit (cumulative gain target).
        profit_pct = (current_equity - self.start_equity) / self.start_equity
        if profit_pct >= self.settings.TAKE_PROFIT_PCT:
            logger.info(
                "TAKE PROFIT: cumulative gain %.2f%%", profit_pct * 100
            )
            return RiskAction.TAKE_PROFIT

        # 4. ADX pause (market turned trending).
        if adx >= self.settings.ADX_THRESHOLD:
            logger.info(
                "ADX PAUSE: ADX=%.2f >= threshold %d",
                adx,
                self.settings.ADX_THRESHOLD,
            )
            return RiskAction.PAUSE_ADX

        # 5. Re-centre trigger (price drifted too far from grid centre).
        if grid_spacing_abs > 0:
            drift_levels = abs(current_price - centre_price) / grid_spacing_abs
            if drift_levels > self.settings.RECENTRE_TRIGGER:
                logger.info(
                    "RECENTRE: price drifted %.1f levels from centre",
                    drift_levels,
                )
                return RiskAction.RECENTRE

        return RiskAction.NONE
