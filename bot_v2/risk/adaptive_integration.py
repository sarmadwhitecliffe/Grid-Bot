"""
Adaptive Risk Integration for bot_v2

Wraps the existing adaptive_risk_manager.py to provide:
- Tier-based position sizing
- Kill switch checks
- Portfolio heat monitoring
- Integration with CapitalManager

This module bridges bot_v2's modular architecture with the proven
adaptive risk management system.
"""

import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

# Import adaptive risk manager from same package
from .adaptive_risk_manager import AdaptiveRiskManager

logger = logging.getLogger(__name__)


class AdaptiveRiskIntegration:
    """
    Integration layer between bot_v2 and adaptive_risk_manager.

    Responsibilities:
    - Calculate tier-based position sizes
    - Check kill switch before trades
    - Monitor portfolio heat
    - Track performance metrics
    - Notify on tier changes

    Example:
        >>> integration = AdaptiveRiskIntegration(data_dir=Path("data_futures"))
        >>> params = await integration.calculate_position_params(
        ...     symbol="BTCUSDT",
        ...     capital=Decimal("1000"),
        ...     current_price=Decimal("50000"),
        ...     atr=Decimal("1000")
        ... )
        >>> if params["allowed"]:
        ...     notional = params["notional"]
        ...     leverage = params["leverage"]
    """

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        config_path: Optional[Path] = None,
        capital_manager: Optional[Any] = None,
    ):
        """
        Initialize adaptive risk integration.

        Args:
            data_dir: Directory for data persistence
            config_path: Optional path to risk config JSON
            capital_manager: CapitalManager instance for consolidated tier storage
        """
        self.data_dir = Path(data_dir)

        # Initialize adaptive risk manager
        self.risk_manager = AdaptiveRiskManager(
            data_dir=self.data_dir,
            config_path=config_path,
            capital_manager=capital_manager,
        )

        logger.info("Adaptive Risk Integration initialized with full risk management")

    def initialize_from_history(
        self, trade_history: List[Dict[str, Any]], symbol_capitals: Dict[str, Decimal]
    ) -> None:
        """
        Initialize risk manager with historical data.

        Args:
            trade_history: List of completed trades
            symbol_capitals: Current capital per symbol
        """
        # Note: The new AdaptiveRiskManager doesn't need explicit initialization
        # It automatically calculates metrics on-demand when calculate_position_parameters is called
        # Trade history is passed per-call, and state is auto-loaded from disk
        logger.info(
            f"Risk manager ready (will process {len(trade_history)} historical trades on-demand)"
        )

    async def calculate_position_params(
        self,
        symbol: str,
        capital: Decimal,
        current_price: Decimal,
        atr: Decimal,
        active_positions: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Calculate position parameters with adaptive risk.

        Args:
            symbol: Trading symbol
            capital: Available capital for this symbol
            current_price: Current market price
            atr: Average True Range (volatility measure)
            active_positions: Dict of currently open positions

        Returns:
            Dict with keys:
                - allowed: bool, whether trade is permitted
                - reason: str, explanation
                - tier: str, risk tier name
                - capital_allocation_pct: float, % of capital to use
                - leverage: int, leverage to apply
                - notional: Decimal, position size in USD
                - position_size: Decimal, position size in base currency
                - base_allocation: float, base tier allocation
                - drawdown_mult: float, drawdown adjustment
                - portfolio_heat_mult: float, portfolio heat limiter
        """
        if active_positions is None:
            active_positions = {}

        # Get trade history for performance analysis
        trade_history = self._get_trade_history(symbol)

        # Convert active positions to list format expected by risk manager
        active_pos_list = list(active_positions.values())

        # Call adaptive risk manager to calculate position parameters
        params = self.risk_manager.calculate_position_parameters(
            symbol=symbol,
            capital=float(capital),
            current_price=float(current_price),
            atr=float(atr),
            trade_history=trade_history,
            active_positions=active_pos_list,
        )

        # Convert response to Decimal for bot_v2
        if params.get("allowed", False):
            params["notional"] = Decimal(str(params.get("notional", 0)))
            params["position_size"] = Decimal(str(params.get("position_size", 0)))

        return params

    def _get_trade_history(self, symbol: str) -> List[Dict[str, Any]]:
        """Load trade history for a symbol"""
        trade_history_file = self.data_dir / "trade_history.json"

        if not trade_history_file.exists():
            return []

        try:
            with open(trade_history_file) as f:
                all_trades = json.load(f)
                # Filter for this symbol
                return [t for t in all_trades if t.get("symbol") == symbol]
        except Exception as e:
            logger.error(f"Failed to load trade history: {e}")
            return []

    def get_tier_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get current tier information for a symbol.

        Returns:
            Dict with tier name, metrics, and parameters
        """
        return self.risk_manager.get_tier_info(symbol)

    def check_kill_switch(self, symbol: str) -> bool:
        """Check if kill switch is active for a symbol"""
        tier_info = self.risk_manager.get_tier_info(symbol)
        return tier_info.get("kill_switch_active", False)

    def reset_kill_switch(self, symbol: str) -> None:
        """Manually reset kill switch for a symbol"""
        self.risk_manager.reset_kill_switch(symbol)
        logger.info(f"Kill switch reset for {symbol}")

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Aggregate basic portfolio-level risk summary.

        Returns:
            Dict with keys:
                total_symbols: int - number of symbols with cached tier/metrics
                tier_distribution: Dict[str, int] - count per tier name
                kill_switch_symbols: List[str] - symbols currently blocked
                average_profit_factor: float | None - mean PF across symbols (if any metrics)
                risk_manager_active: bool - always True while integration instantiated
        """
        # Use underlying risk_manager caches
        tier_cache = getattr(self.risk_manager, "tier_cache", {}) or {}
        performance_cache = getattr(self.risk_manager, "performance_cache", {}) or {}
        kill_switch_active = getattr(self.risk_manager, "kill_switch_active", {}) or {}

        # Tier distribution
        dist: Dict[str, int] = {}
        for sym, tier_obj in tier_cache.items():
            name = getattr(tier_obj, "name", "UNKNOWN")
            dist[name] = dist.get(name, 0) + 1

        # Average profit factor
        pf_values = [
            m.profit_factor
            for m in performance_cache.values()
            if hasattr(m, "profit_factor")
        ]
        avg_pf = sum(pf_values) / len(pf_values) if pf_values else None

        kill_symbols = [s for s, active in kill_switch_active.items() if active]

        return {
            "total_symbols": max(len(tier_cache), len(performance_cache)),
            "tier_distribution": dist,
            "kill_switch_symbols": kill_symbols,
            "average_profit_factor": avg_pf,
            "risk_manager_active": True,
        }

    def get_all_tiers_status(self) -> Dict[str, Dict[str, Any]]:
        """Get tier status for all configured symbols."""
        if not self.risk_manager.capital_manager:
            logger.warning("No capital_manager configured, returning empty status")
            return {}

        tiers_status = {}
        for symbol in self.risk_manager.capital_manager._capitals.keys():
            tier_info = self.get_tier_info(symbol)
            tiers_status[symbol] = {
                "tier": tier_info.get("tier", "UNKNOWN"),
                "allocation_pct": tier_info.get("capital_allocation", 0),
                "leverage": tier_info.get("leverage_multiplier", 1),
                "kill_switch_active": tier_info.get("kill_switch_active", False),
            }
        return tiers_status
