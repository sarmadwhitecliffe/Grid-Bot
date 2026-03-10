#!/usr/bin/env python3
"""
Analyze HYPE/USDT Kill Switch Investigation

This script analyzes HYPE/USDT trade history to understand what triggered the kill switch.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

# Set up logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class HYPEKillSwitchAnalyzer:
    """Analyze HYPE/USDT trades to understand kill switch trigger."""

    def __init__(
        self, trade_history_file: Path = Path("data_futures/trade_history.json")
    ):
        self.trade_history_file = trade_history_file
        self.hype_trades: List[Dict[str, Any]] = []

    def load_hype_trades(self) -> None:
        """Load all HYPE/USDT trades from history."""
        try:
            with open(self.trade_history_file, "r", encoding="utf-8") as f:
                all_trades = json.load(f)

            self.hype_trades = [
                trade for trade in all_trades if trade.get("symbol") == "HYPE/USDT"
            ]
            logger.info(f"Loaded {len(self.hype_trades)} HYPE/USDT trades")

        except Exception as e:
            logger.error(f"Failed to load trade history: {e}")

    def calculate_performance_metrics(self) -> Dict[str, Any]:
        """Calculate performance metrics for HYPE/USDT trades."""
        if not self.hype_trades:
            return {}

        # Sort trades by timestamp
        sorted_trades = sorted(self.hype_trades, key=lambda x: x["timestamp"])

        # Calculate basic metrics
        total_trades = len(sorted_trades)
        winning_trades = [t for t in sorted_trades if float(t["pnl_usd"]) > 0]
        losing_trades = [t for t in sorted_trades if float(t["pnl_usd"]) <= 0]

        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0

        # Calculate profit factor
        total_profit = sum(float(t["pnl_usd"]) for t in winning_trades)
        total_loss = abs(sum(float(t["pnl_usd"]) for t in losing_trades))
        profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

        # Calculate equity curve and drawdown
        equity_curve = []
        current_equity = 100.0  # Starting equity
        peak_equity = current_equity
        max_drawdown = 0.0
        current_drawdown = 0.0

        for trade in sorted_trades:
            pnl = float(trade["pnl_usd"])
            current_equity += pnl
            equity_curve.append(current_equity)

            if current_equity > peak_equity:
                peak_equity = current_equity
                current_drawdown = 0.0
            else:
                current_drawdown = (peak_equity - current_equity) / peak_equity
                max_drawdown = max(max_drawdown, current_drawdown)

        # Calculate consecutive losses
        consecutive_losses = 0
        max_consecutive_losses = 0

        for trade in sorted_trades:
            if float(trade["pnl_usd"]) <= 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

        current_consecutive_losses = consecutive_losses  # Last streak

        # Calculate Sharpe ratio (simplified)
        returns = []
        prev_equity = 100.0
        for equity in equity_curve:
            ret = (equity - prev_equity) / prev_equity
            returns.append(ret)
            prev_equity = equity

        if returns:
            avg_return = sum(returns) / len(returns)
            std_return = (
                sum((r - avg_return) ** 2 for r in returns) / len(returns)
            ) ** 0.5
            sharpe_ratio = avg_return / std_return * (252**0.5) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        return {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "current_drawdown_pct": current_drawdown,
            "max_consecutive_losses": max_consecutive_losses,
            "current_consecutive_losses": current_consecutive_losses,
            "sharpe_ratio": sharpe_ratio,
            "final_equity": current_equity,
            "peak_equity": peak_equity,
            "total_return_pct": (current_equity - 100) / 100,
            "avg_win": total_profit / len(winning_trades) if winning_trades else 0,
            "avg_loss": total_loss / len(losing_trades) if losing_trades else 0,
        }

    def analyze_kill_switch_triggers(self, metrics: Dict[str, Any]) -> List[str]:
        """Analyze what kill switch conditions were met."""
        triggers = []

        # Kill switch thresholds from code
        DD_LIMIT = 0.30  # 30%
        CONSECUTIVE_LOSSES_LIMIT = 7
        PF_LIMIT = 0.5  # for trades >= 20

        if metrics["current_drawdown_pct"] > DD_LIMIT:
            triggers.append(
                f"Drawdown {metrics['current_drawdown_pct']:.1%} > {DD_LIMIT:.0%} limit"
            )

        if metrics["current_consecutive_losses"] >= CONSECUTIVE_LOSSES_LIMIT:
            triggers.append(
                f"Consecutive losses {metrics['current_consecutive_losses']} >= {CONSECUTIVE_LOSSES_LIMIT} limit"
            )

        if metrics["total_trades"] >= 20 and metrics["profit_factor"] < PF_LIMIT:
            triggers.append(
                f"Profit Factor {metrics['profit_factor']:.2f} < {PF_LIMIT} (with {metrics['total_trades']} trades)"
            )

        return triggers

    def print_analysis(self) -> None:
        """Print detailed analysis of HYPE/USDT performance."""
        if not self.hype_trades:
            print("No HYPE/USDT trades found!")
            return

        metrics = self.calculate_performance_metrics()
        triggers = self.analyze_kill_switch_triggers(metrics)

        print("🔍 HYPE/USDT Kill Switch Investigation")
        print("=" * 50)
        print(f"Total Trades: {metrics['total_trades']}")
        print(f"Win Rate: {metrics['win_rate']:.1%}")
        print(f"Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        print(f"Max Drawdown: {metrics['max_drawdown_pct']:.1%}")
        print(f"Current Drawdown: {metrics['current_drawdown_pct']:.1%}")
        print(f"Max Consecutive Losses: {metrics['max_consecutive_losses']}")
        print(f"Current Consecutive Losses: {metrics['current_consecutive_losses']}")
        print(f"Final Equity: ${metrics['final_equity']:.2f}")
        print(f"Peak Equity: ${metrics['peak_equity']:.2f}")
        print(f"Total Return: {metrics['total_return_pct']:.1%}")
        print(f"Average Win: ${metrics['avg_win']:.2f}")
        print(f"Average Loss: ${metrics['avg_loss']:.2f}")
        print()

        print("🚨 Kill Switch Triggers:")
        if triggers:
            for trigger in triggers:
                print(f"  ❌ {trigger}")
        else:
            print("  ✅ No kill switch conditions met")
        print()

        # Show recent trades
        print("📊 Recent HYPE/USDT Trades (last 10):")
        recent_trades = self.hype_trades[-10:]
        for i, trade in enumerate(recent_trades, 1):
            pnl = float(trade["pnl_usd"])
            side = trade["side"]
            exit_reason = trade["exit_reason"]
            timestamp = trade["timestamp"][:19]  # YYYY-MM-DDTHH:MM:SS
            print(f"  {i}. {timestamp} {side.upper()} {exit_reason}: ${pnl:.2f}")


def main():
    """Main analysis function."""
    analyzer = HYPEKillSwitchAnalyzer()
    analyzer.load_hype_trades()
    analyzer.print_analysis()


if __name__ == "__main__":
    main()
