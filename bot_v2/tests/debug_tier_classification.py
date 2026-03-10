#!/usr/bin/env python3
"""
Debug script to trace XRP tier classification bug.
Simulates the classification with actual XRP metrics.
"""

import json
from dataclasses import dataclass
from typing import Optional

# Load tier definitions
tier_config = json.load(open("config/adaptive_risk_tiers.json"))
ALL_TIERS_RAW = tier_config["tiers"]


@dataclass
class RiskTier:
    name: str
    min_trades: int
    max_trades: Optional[int]
    profit_factor_min: Optional[float]
    capital_allocation: float
    max_leverage: int
    description: str


# Convert to RiskTier objects
ALL_TIERS = [
    RiskTier(
        name=t["name"],
        min_trades=t["min_trades"],
        max_trades=t.get("max_trades"),
        profit_factor_min=t.get("min_profit_factor"),
        capital_allocation=t.get("capital_allocation_pct", 50.0),
        max_leverage=t["max_leverage"],
        description=t["description"],
    )
    for t in ALL_TIERS_RAW
]

# Sort by capital_allocation descending (highest to lowest tier)
ALL_TIERS = sorted(ALL_TIERS, key=lambda t: t.capital_allocation, reverse=True)

print("=" * 80)
print("TIER DEFINITIONS:")
print("=" * 80)
for i, tier in enumerate(ALL_TIERS):
    max_str = tier.max_trades if tier.max_trades else "∞"
    pf_str = f"PF≥{tier.profit_factor_min}" if tier.profit_factor_min else "PF≥0"
    print(f"{i}. {tier.name:12} | Trades: {tier.min_trades:2}-{max_str:3} | {pf_str}")
print()

# XRP actual metrics at time of demotion
xrp_metrics = {
    "total_trades": 17,
    "lookback_trades": 17,
    "profit_factor": 7.513310178570175,
    "win_rate": 0.9411764705882353,
}

# XRP tier history before demotion
xrp_tier_history = {
    "current_tier": "STANDARD",
    "tier_entry_time": "2025-11-10T21:15:23.374750+00:00",
    "trades_in_tier": 1,
    "consecutive_losses_in_tier": 0,
    "last_transition_time": "2025-11-10T21:15:23.374755+00:00",
    "previous_tier": "PROBATION",
    "last_total_trades": 16,
}

print("=" * 80)
print("XRP METRICS AT DEMOTION:")
print("=" * 80)
print(f"Total Trades: {xrp_metrics['total_trades']}")
print(f"Profit Factor: {xrp_metrics['profit_factor']:.2f}")
print(f"Win Rate: {xrp_metrics['win_rate']*100:.1f}%")
print()

print("=" * 80)
print("XRP TIER HISTORY BEFORE DEMOTION:")
print("=" * 80)
print(f"Current Tier: {xrp_tier_history['current_tier']}")
print(f"Trades in Tier: {xrp_tier_history['trades_in_tier']}")
print(f"Consecutive Losses: {xrp_tier_history['consecutive_losses_in_tier']}")
print()


def meets_criteria(total_trades: int, profit_factor: float, tier: RiskTier) -> bool:
    """Check if metrics meet tier criteria."""

    # Check min_trades
    if total_trades < tier.min_trades:
        print(f"  ❌ min_trades: {total_trades} < {tier.min_trades}")
        return False
    else:
        print(f"  ✅ min_trades: {total_trades} >= {tier.min_trades}")

    # Check max_trades (strict max-exclusive)
    if tier.max_trades is not None and total_trades >= tier.max_trades:
        print(
            f"  ❌ max_trades: {total_trades} >= {tier.max_trades} (strict exclusive)"
        )
        return False
    elif tier.max_trades is not None:
        print(f"  ✅ max_trades: {total_trades} < {tier.max_trades}")
    else:
        print("  ✅ max_trades: None (no upper limit)")

    # Check profit_factor_min
    if tier.profit_factor_min is not None:
        if profit_factor < tier.profit_factor_min:
            print(f"  ❌ profit_factor: {profit_factor:.2f} < {tier.profit_factor_min}")
            return False
        else:
            print(
                f"  ✅ profit_factor: {profit_factor:.2f} >= {tier.profit_factor_min}"
            )
    else:
        print("  ✅ profit_factor: No minimum required")

    return True


print("=" * 80)
print("TIER ELIGIBILITY CHECK (Iterating ALL_TIERS from highest to lowest):")
print("=" * 80)

best_eligible_tier = None
for i, tier in enumerate(ALL_TIERS):
    print(f"\n{i}. Checking {tier.name}:")
    if meets_criteria(xrp_metrics["total_trades"], xrp_metrics["profit_factor"], tier):
        print(f"  ✅ ELIGIBLE for {tier.name}")
        best_eligible_tier = tier
        break
    else:
        print(f"  ❌ NOT eligible for {tier.name}")

if best_eligible_tier is None:
    print("\n⚠️ No tier qualified, falling back to PROBATION (last tier)")
    best_eligible_tier = ALL_TIERS[-1]
else:
    print(f"\n✅ Best eligible tier: {best_eligible_tier.name}")

print()
print("=" * 80)
print("HYSTERESIS CHECK:")
print("=" * 80)

# Get current tier from history
current_tier_name = xrp_tier_history.get("current_tier")
current_tier_obj = next((t for t in ALL_TIERS if t.name == current_tier_name), None)

if current_tier_obj is None:
    print("No current tier in history - this is first classification")
    final_tier = best_eligible_tier
else:
    print(f"Current tier: {current_tier_obj.name}")
    print(f"Best eligible tier: {best_eligible_tier.name}")

    current_tier_index = ALL_TIERS.index(current_tier_obj)
    best_tier_index = ALL_TIERS.index(best_eligible_tier)

    print(f"Current tier index: {current_tier_index}")
    print(f"Best eligible tier index: {best_tier_index}")

    if best_tier_index < current_tier_index:
        print("\n📈 PROMOTION scenario (moving to higher tier)")
        print(
            "  → Would check: min_stay_trades, promote_buffer_pf, promote_after_trades"
        )
        print("  → For this debug, assuming promotion allowed")
        final_tier = best_eligible_tier
    elif best_tier_index > current_tier_index:
        print("\n📉 DEMOTION scenario (moving to lower tier)")
        print("  → Would check: demote_after_losses, demote_buffer_pf")

        # Load demotion rules
        settings = tier_config.get("settings", {})
        demotion_rules = settings.get("demotion_rules", {})
        demote_after_losses = demotion_rules.get("demote_after_losses", 0)
        consecutive_losses = xrp_tier_history.get("consecutive_losses_in_tier", 0)

        print(f"  → Consecutive losses: {consecutive_losses}")
        print(f"  → Demote after losses threshold: {demote_after_losses}")

        if demote_after_losses > 0 and consecutive_losses < demote_after_losses:
            print("  ✋ Demotion DEFERRED (not enough consecutive losses)")
            final_tier = current_tier_obj
        else:
            print("  ✅ Demotion CONFIRMED")
            final_tier = best_eligible_tier
    else:
        print("\n➡️ SAME TIER - no change")
        final_tier = current_tier_obj

print()
print("=" * 80)
print("FINAL RESULT:")
print("=" * 80)
print(f"Final tier: {final_tier.name}")
print()

print("=" * 80)
print("BUG ANALYSIS:")
print("=" * 80)

if final_tier.name == "PROBATION":
    print("🐛 BUG CONFIRMED!")
    print(f"   XRP has {xrp_metrics['total_trades']} trades but was assigned PROBATION")
    print(f"   PROBATION max_trades = {ALL_TIERS[-1].max_trades}")
    print(
        f"   {xrp_metrics['total_trades']} >= {ALL_TIERS[-1].max_trades} → XRP FAILS PROBATION criteria!"
    )
    print()
    print("   ROOT CAUSE:")

    # Check if PROBATION was incorrectly marked as eligible
    probation = ALL_TIERS[-1]
    print("\n   Testing PROBATION criteria directly:")
    probation_eligible = meets_criteria(
        xrp_metrics["total_trades"], xrp_metrics["profit_factor"], probation
    )

    if not probation_eligible:
        print("\n   ❌ PROBATION is NOT eligible for XRP!")
        print("   → The classifier should have continued to next tier")
        print(
            "   → Possible bug: classifier returns PROBATION as fallback without checking criteria"
        )

elif final_tier.name == "STANDARD":
    print("✅ CORRECT!")
    print(
        f"   XRP with {xrp_metrics['total_trades']} trades and PF={xrp_metrics['profit_factor']:.2f}"
    )
    print(f"   correctly qualifies for {final_tier.name}")
else:
    print(f"⚠️ UNEXPECTED: Final tier is {final_tier.name}")

print()
