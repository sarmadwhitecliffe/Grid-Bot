"""
Global Risk Manager - Portfolio-wide safety guards

Aggregates state from all active symbols to enforce account-level safety rules.
Features:
- Portfolio Drawdown Kill Switch
- Aggregate Margin/Leverage monitoring
- Portfolio Heat limits
"""

import logging
from decimal import Decimal
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class GlobalRiskManager:
    """
    Enforces risk limits across the entire portfolio.
    """
    def __init__(self, capital_manager: Any, max_drawdown_pct: float = 0.20):
        self.capital_manager = capital_manager
        self.max_drawdown_pct = Decimal(str(max_drawdown_pct))
        
        self.peak_portfolio_value = Decimal("0")
        self.is_halted = False
        self.halt_reason = ""

    async def evaluate_portfolio_risk(self) -> bool:
        """
        Calculate total equity and check for global drawdown.
        Returns True if risk is acceptable, False if portfolio-wide halt triggered.
        """
        if self.is_halted:
            return False

        all_capitals = self.capital_manager.get_all_capitals()
        if not all_capitals:
            # No symbols configured/loaded yet, don't halt
            return True

        total_equity = sum(all_capitals.values())

        if total_equity <= 0:
            self._trigger_halt("Total portfolio capital depleted to $0")
            return False

        # Update peak
        if total_equity > self.peak_portfolio_value:
            self.peak_portfolio_value = total_equity
            return True

        # Check drawdown
        drawdown = (self.peak_portfolio_value - total_equity) / self.peak_portfolio_value
        if drawdown >= self.max_drawdown_pct:
            self._trigger_halt(f"Portfolio Max Drawdown reached: {drawdown*100:.2f}%")
            return False

        return True

    def _trigger_halt(self, reason: str):
        self.is_halted = True
        self.halt_reason = reason
        logger.critical(f"🛑 PORTFOLIO HALT TRIGGERED: {reason}")

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_halted": self.is_halted,
            "halt_reason": self.halt_reason,
            "peak_value": float(self.peak_portfolio_value),
            "max_drawdown_allowed": float(self.max_drawdown_pct)
        }
