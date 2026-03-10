"""
Cost floor filter for entry signals.

Ensures TP1 distance covers transaction costs.
"""

import logging
import os
from decimal import Decimal

from bot_v2.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)


class CostFilter:
    """
    Cost floor filter to ensure profitability.

    Ensures TP1 distance covers transaction costs (spread + slippage + fees).
    """

    @staticmethod
    def is_cost_floor_met(
        config: StrategyConfig, entry_price: Decimal, atr_in_usd: Decimal
    ) -> bool:
        """
        Ensure TP1 distance covers transaction costs (EXACT from bot_v1).

        Args:
            config: Strategy configuration
            entry_price: Entry price for cost calculation
            atr_in_usd: ATR value in USD

        Returns:
            True if cost floor requirements are met
        """
        if atr_in_usd <= 0:
            logger.warning(
                f"[{config.symbol_id}] ATR in USD is zero or negative ({atr_in_usd}). "
                f"Cost floor check cannot be performed."
            )
            return False

        # Calculate cost components (convert percentages to decimals)
        spread_cost = entry_price * (
            config.slippage_pct / Decimal("100") / Decimal("2")
        )
        slippage_cost = entry_price * (config.slippage_pct / Decimal("100"))
        fee_cost = entry_price * Decimal(str(os.getenv("FEE_PERCENTAGE", "0.0004")))

        total_friction_usd = spread_cost + slippage_cost + fee_cost

        # Convert cost into ATR units
        fee_atr = total_friction_usd / atr_in_usd
        required_dist_atr = fee_atr * config.cost_floor_multiplier

        tp1_dist_atr = config.tp1_atr_mult

        if tp1_dist_atr >= required_dist_atr:
            logger.info(
                f"[{config.symbol_id}] Cost floor check PASSED. "
                f"TP1: {tp1_dist_atr:.2f} ATR, Required: {required_dist_atr:.2f} ATR."
            )
            return True

        logger.warning(
            f"[{config.symbol_id}] Trade REJECTED by cost floor. "
            f"TP1 distance ({tp1_dist_atr:.2f} ATR) < required {config.cost_floor_multiplier}x cost "
            f"({required_dist_atr:.2f} ATR). "
            f"Details: Spread={spread_cost:.6f}, Slippage={slippage_cost:.6f}, Fee={fee_cost:.6f}, "
            f"Total={total_friction_usd:.6f}."
        )
        return False
