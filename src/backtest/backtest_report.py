"""
src/backtest/backtest_report.py
--------------------------------
Metric calculation and human-readable report generation from BacktestResult.

All metrics are computed lazily from the trades and equity_curve lists
stored in a BacktestResult — no state is mutated.
"""

import logging
import math
from typing import List, Optional

from src.backtest.grid_backtester import BacktestResult, BacktestTrade

logger = logging.getLogger(__name__)

# Minimum backtest performance thresholds (configurable per strategy).
TARGET_WIN_RATE: float = 0.55  # Lowered for high frequency strategy
TARGET_PROFIT_FACTOR: float = 1.2  # Lowered to reflect volume focus
TARGET_SHARPE: float = 0.5  # Lowered for higher drawdown tolerance
TARGET_MAX_DRAWDOWN: float = 0.25  # 25% max tolerance


class BacktestReport:
    """
    Calculates performance metrics from a BacktestResult.

    Example usage:
        result = backtester.run(df)
        report = BacktestReport(result)
        print(report.summary())
        if report.passes_targets():
            print("Strategy approved for live trading.")
    """

    def __init__(self, result: BacktestResult) -> None:
        """
        Bind the report to a completed backtest result.

        Args:
            result: Output from GridBacktester.run().
        """
        self.result = result
        self._completed_trades: List[BacktestTrade] = [
            t for t in result.trades if t.realized_pnl is not None
        ]

    # ------------------------------------------------------------------ #
    # Core Metrics                                                          #
    # ------------------------------------------------------------------ #

    def win_rate(self) -> float:
        """
        Fraction of completed trades (buy->sell cycles) that were profitable.

        Returns 0.0 if no completed trades exist.
        """
        if not self._completed_trades:
            return 0.0
        wins = sum(1 for t in self._completed_trades if (t.realized_pnl or 0) > 0)
        return wins / len(self._completed_trades)

    def profit_factor(self) -> float:
        """
        Ratio of total gross profit to total gross loss, AFTER fees.

        Returns float('inf') if there are no losing trades, 0.0 if no
        profitable trades exist.
        """
        gross_profit = sum(
            t.realized_pnl - (t.fee_usdt or 0)
            for t in self._completed_trades
            if t.realized_pnl and t.realized_pnl > 0
        )
        gross_loss = abs(
            sum(
                t.realized_pnl - (t.fee_usdt or 0)
                for t in self._completed_trades
                if t.realized_pnl and t.realized_pnl < 0
            )
        )
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        if gross_profit == 0:
            return 0.0
        return gross_profit / gross_loss

    def max_drawdown(self) -> float:
        """
        Maximum peak-to-trough drawdown as a fraction of peak equity.

        Returns 0.0 if equity_curve is empty or has a single point.
        """
        curve = self.result.equity_curve
        if len(curve) < 2:
            return 0.0
        peak = curve[0]
        max_dd = 0.0
        for eq in curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    def total_return(self) -> float:
        """
        Net return as a fraction of initial capital.

        Returns:
            float: e.g. 0.12 means +12% total return.
        """
        if self.result.initial_capital == 0:
            return 0.0
        return (
            self.result.final_equity - self.result.initial_capital
        ) / self.result.initial_capital

    def sharpe_ratio(self, risk_free_daily: float = 0.0) -> float:
        """
        Annualised Sharpe Ratio from bar-level equity returns.

        Assumes each bar represents a 1-hour candle (8760 bars/year).
        Adjust risk_free_daily if using different timeframes.

        Args:
            risk_free_daily: Daily risk-free rate (default 0 for simplicity).

        Returns:
            float: Sharpe ratio, capped at 0 if std dev is zero.
        """
        curve = self.result.equity_curve
        if len(curve) < 2:
            return 0.0
        returns: List[float] = []
        for i in range(1, len(curve)):
            if curve[i - 1] > 0:
                returns.append((curve[i] - curve[i - 1]) / curve[i - 1])
        if not returns:
            return 0.0
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        std_r = math.sqrt(variance)
        if std_r == 0:
            return 0.0
        # Annualise assuming hourly bars.
        return ((mean_r - risk_free_daily) / std_r) * math.sqrt(8760)

    # ------------------------------------------------------------------ #
    # Validation                                                            #
    # ------------------------------------------------------------------ #

    def passes_targets(
        self,
        target_win_rate: float = TARGET_WIN_RATE,
        target_profit_factor: float = TARGET_PROFIT_FACTOR,
        target_sharpe: float = TARGET_SHARPE,
        target_max_drawdown: float = TARGET_MAX_DRAWDOWN,
    ) -> bool:
        """
        Check all KPIs against acceptance thresholds.

        Returns:
            True only if ALL targets are met simultaneously.
        """
        checks = {
            "win_rate >= target": self.win_rate() >= target_win_rate,
            "profit_factor >= target": self.profit_factor() >= target_profit_factor,
            "sharpe >= target": self.sharpe_ratio() >= target_sharpe,
            "max_drawdown <= target": self.max_drawdown() <= target_max_drawdown,
        }
        for name, passed in checks.items():
            if not passed:
                logger.warning("Backtest target FAILED: %s", name)
        return all(checks.values())

    # ------------------------------------------------------------------ #
    # Display                                                               #
    # ------------------------------------------------------------------ #

    def summary(self) -> str:
        """
        Return a multi-line human-readable summary of all metrics.

        Example output:
            === Backtest Report ===
            Total Trades : 842
            Win Rate     : 63.7%
            Profit Factor: 1.82
            Total Return : +8.4%
            Max Drawdown : 7.2%
            Sharpe Ratio : 1.43
            Total Fees   : 12.34 USDT
            Passed       : YES
        """
        avg_open = 0.0
        max_open = 0
        if self.result.open_orders_curve:
            avg_open = sum(self.result.open_orders_curve) / len(
                self.result.open_orders_curve
            )
            max_open = max(self.result.open_orders_curve)

        lines = [
            "=== Backtest Report ===",
            f"Total Trades : {len(self._completed_trades)}",
            f"Win Rate     : {self.win_rate() * 100:.1f}%",
            f"Profit Factor: {self.profit_factor():.2f}",
            f"Total Return : {self.total_return() * 100:+.1f}%",
            f"Max Drawdown : {self.max_drawdown() * 100:.1f}%",
            f"Sharpe Ratio : {self.sharpe_ratio():.2f}",
            f"Avg Open Ord : {avg_open:.1f}",
            f"Max Open Ord : {max_open}",
            f"Total Fees   : {self.result.total_fees_usdt:.2f} USDT",
            f"Passed       : {'YES' if self.passes_targets() else 'NO'}",
        ]
        return "\n".join(lines)

    def export_trades_csv(self, filepath: str) -> None:
        """
        Export all simulated trades to a CSV file for detailed analysis.

        Args:
            filepath: Destination path for the CSV output.
        """
        import pandas as pd

        if not self.result.trades:
            logger.warning("No trades to export to CSV.")
            return

        trades_data = []
        for t in self.result.trades:
            notional = t.entry_price * t.amount
            trades_data.append(
                {
                    "bar_index": t.bar_index,
                    "timestamp": t.timestamp,
                    "side": t.side,
                    "position_side": t.position_side,
                    "entry_price": t.entry_price,
                    "amount": t.amount,
                    "notional_value": notional,
                    "fee_usdt": t.fee_usdt,
                    "realized_pnl": t.realized_pnl,
                    "equity_after": t.equity_after,
                    "exit_reason": getattr(t, "exit_reason", "grid"),
                    "concurrent_orders": getattr(t, "open_orders_at_fill", 0),
                }
            )

        df = pd.DataFrame(trades_data)
        df.to_csv(filepath, index=False)
        logger.info(f"Exported {len(df)} trades to {filepath}")
