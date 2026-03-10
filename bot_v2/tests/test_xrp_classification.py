#!/usr/bin/env python3
"""
Test XRP tier classification with actual metrics to reproduce the bug.
"""

import sys

sys.path.insert(0, "/home/user/NonML_Bot")

from bot_v2.risk.adaptive_risk_manager import PerformanceMetrics, RiskTierClassifier

# XRP actual metrics from symbol_performance.json
xrp_metrics = PerformanceMetrics(
    symbol="XRP/USDT",
    total_trades=17,
    lookback_trades=17,
    win_rate=0.941,  # 94.1%
    profit_factor=7.51,
    sharpe_ratio=0.0,
    max_drawdown=0.0,
    avg_win=0.0,
    avg_loss=0.0,
    avg_win_r=0.0,
    avg_r_multiple=0.0,
    expectancy_r=0.0,
    max_consecutive_losses=0,
    current_consecutive_losses=0,
    std_dev_returns=0.0,
    current_equity=1000.0,
    peak_equity=1000.0,
    last_calculated="2025-11-11T00:00:00",
    first_trade_date="2025-11-01T00:00:00",
    current_drawdown_pct=0.0,
    recovery_factor=0.0,
)

# XRP tier history from tier_history.json
xrp_tier_history = {
    "current_tier": "STANDARD",
    "trades_in_tier": 1,
    "tier_entry_time": "2025-11-10T16:23:45.123456",
    "last_total_trades": 16,
    "consecutive_losses_in_tier": 0,
    "tier_transitions": [
        {
            "timestamp": "2025-11-10T16:23:45.123456",
            "from_tier": "PROBATION",
            "to_tier": "STANDARD",
            "total_trades": 16,
            "profit_factor": 7.89,
        }
    ],
}

print("=" * 80)
print("Testing XRP Tier Classification")
print("=" * 80)
print("\nMetrics:")
print(f"  Total Trades: {xrp_metrics.total_trades}")
print(f"  Profit Factor: {xrp_metrics.profit_factor:.2f}")
print(f"  Win Rate: {xrp_metrics.win_rate * 100:.1f}%")
print("\nCurrent Tier History:")
print(f"  Current Tier: {xrp_tier_history['current_tier']}")
print(f"  Trades in Tier: {xrp_tier_history['trades_in_tier']}")
print(f"  Last Total Trades: {xrp_tier_history['last_total_trades']}")

print("\n" + "=" * 80)
print("Calling RiskTierClassifier.classify()...")
print("=" * 80 + "\n")

# This will trigger the debug logs we just added
classified_tier = RiskTierClassifier.classify(xrp_metrics, xrp_tier_history)

print("\n" + "=" * 80)
print(f"🎯 RESULT: Classified as {classified_tier.name}")
print("=" * 80)

# Expected: STANDARD (15+ trades, PF>=1.2)
if classified_tier.name == "STANDARD":
    print("✅ CORRECT! XRP should be in STANDARD tier")
elif classified_tier.name == "PROBATION":
    print("❌ BUG REPRODUCED! XRP incorrectly assigned to PROBATION")
    print(
        f"   PROBATION requires max_trades=10, but XRP has {xrp_metrics.total_trades} trades"
    )
else:
    print(f"⚠️ Unexpected tier: {classified_tier.name}")
