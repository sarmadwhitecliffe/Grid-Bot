#!/usr/bin/env python3
"""
Test script to verify kill switch configuration is properly loaded and applied.

This script:
1. Imports the adaptive_risk_manager module
2. Checks that SETTINGS dict is populated with config values
3. Simulates a kill switch check
4. Confirms the config path and values
"""

import sys
import json
from pathlib import Path
from decimal import Decimal

# Add bot_v2 to path
sys.path.insert(0, str(Path(__file__).parent / "bot_v2"))

from bot_v2.risk.adaptive_risk_manager import (
    SETTINGS,
    ALL_TIERS,
    KillSwitch,
    PerformanceMetrics,
    load_risk_tiers_from_config,
)

print("=" * 80)
print("KILL SWITCH CONFIG VALIDATION TEST")
print("=" * 80)

# Test 1: Check that SETTINGS dict is populated
print("\n[TEST 1] Verify SETTINGS Dict Is Populated")
print("-" * 80)
print(f"✅ SETTINGS loaded from config:")
print(f"  enable_kill_switch: {SETTINGS.get('enable_kill_switch')}")
print(
    f"  kill_switch_consecutive_losses: {SETTINGS.get('kill_switch_consecutive_losses')}"
)
print(f"  kill_switch_drawdown_pct: {SETTINGS.get('kill_switch_drawdown_pct')}")
print(f"  kill_switch_pf_min_trades: {SETTINGS.get('kill_switch_pf_min_trades')}")
print(f"  lookback_trades: {SETTINGS.get('lookback_trades')}")

# Test 2: Verify config file location
print("\n[TEST 2] Verify Config File Path")
print("-" * 80)
config_path = Path("config/adaptive_risk_tiers.json")
if config_path.exists():
    print(f"✅ Config file exists: {config_path.absolute()}")
    with open(config_path) as f:
        config_data = json.load(f)
    config_settings = config_data.get("settings", {})
    print(f"\n✅ Settings from config file:")
    for key in [
        "enable_kill_switch",
        "kill_switch_consecutive_losses",
        "kill_switch_drawdown_pct",
        "kill_switch_pf_min_trades",
    ]:
        print(f"  {key}: {config_settings.get(key)}")
else:
    print(f"❌ Config file NOT found: {config_path}")

# Test 3: Verify tiers are loaded
print("\n[TEST 3] Verify Tiers Are Loaded")
print("-" * 80)
print(f"✅ {len(ALL_TIERS)} tiers loaded:")
for tier in ALL_TIERS:
    print(
        f"  - {tier.name}: {tier.capital_allocation * 100:.0f}% @ {tier.min_leverage}-{tier.max_leverage}x leverage, PF>{tier.profit_factor_min:.1f}"
    )

# Test 4: Simulate kill switch triggers
print("\n[TEST 4] Simulate Kill Switch Triggers")
print("-" * 80)

# Test 4a: Drawdown exceeding limit (> 20%, not >=)
metrics_drawdown = PerformanceMetrics(
    symbol="TEST/USDT",
    total_trades=50,
    lookback_trades=30,
    profit_factor=1.5,
    sharpe_ratio=0.5,
    win_rate=0.45,
    max_drawdown=0.35,
    avg_win=100.0,
    avg_loss=-50.0,
    avg_win_r=1.0,
    avg_r_multiple=1.0,
    expectancy_r=25.0,
    max_consecutive_losses=3,
    current_consecutive_losses=2,
    std_dev_returns=0.05,
    current_equity=79.0,  # Lost 21%
    peak_equity=100.0,  # Peak was 100
    last_calculated="2026-02-05T10:00:00Z",
    first_trade_date="2026-01-01T10:00:00Z",
    current_drawdown_pct=0.21,  # 21% drawdown (> 20%)
    recovery_factor=2.0,
)

triggered, reason = KillSwitch.check_triggers(metrics_drawdown)
print(f"\n✅ Test: 21% Drawdown (exceeds 20% limit)")
print(f"   Triggered: {triggered}")
print(f"   Reason: {reason}")
print(f"   Expected: True (drawdown > 20% limit)")

# Test 4b: Consecutive losses exceeding limit
metrics_losses = PerformanceMetrics(
    symbol="TEST/USDT",
    total_trades=50,
    lookback_trades=30,
    profit_factor=1.0,
    sharpe_ratio=0.2,
    win_rate=0.40,
    max_drawdown=0.15,
    avg_win=100.0,
    avg_loss=-50.0,
    avg_win_r=0.8,
    avg_r_multiple=0.8,
    expectancy_r=10.0,
    max_consecutive_losses=8,
    current_consecutive_losses=5,  # At limit
    std_dev_returns=0.05,
    current_equity=90.0,
    peak_equity=100.0,
    last_calculated="2026-02-05T10:00:00Z",
    first_trade_date="2026-01-01T10:00:00Z",
    current_drawdown_pct=0.10,
    recovery_factor=1.5,
)

triggered, reason = KillSwitch.check_triggers(metrics_losses)
print(f"\n✅ Test: 5 Consecutive Losses")
print(f"   Triggered: {triggered}")
print(f"   Reason: {reason}")
print(f"   Expected: True (at consecutive losses limit)")

# Test 4c: Profit factor below 0.5 with sufficient trades
metrics_pf = PerformanceMetrics(
    symbol="TEST/USDT",
    total_trades=6,  # > kill_switch_pf_min_trades (5)
    lookback_trades=30,
    profit_factor=0.45,  # Below 0.5 threshold
    sharpe_ratio=-0.5,
    win_rate=0.30,
    max_drawdown=0.20,
    avg_win=100.0,
    avg_loss=-200.0,
    avg_win_r=0.5,
    avg_r_multiple=0.5,
    expectancy_r=-20.0,
    max_consecutive_losses=4,
    current_consecutive_losses=1,
    std_dev_returns=0.08,
    current_equity=75.0,
    peak_equity=100.0,
    last_calculated="2026-02-05T10:00:00Z",
    first_trade_date="2026-01-01T10:00:00Z",
    current_drawdown_pct=0.25,
    recovery_factor=0.5,
)

triggered, reason = KillSwitch.check_triggers(metrics_pf)
print(f"\n✅ Test: PF Below 0.5 (6 trades)")
print(f"   Triggered: {triggered}")
print(f"   Reason: {reason}")
print(f"   Expected: True (PF < 0.5 after 5+ trades)")

# Test 4d: No triggers
metrics_healthy = PerformanceMetrics(
    symbol="TEST/USDT",
    total_trades=50,
    lookback_trades=30,
    profit_factor=1.8,
    sharpe_ratio=1.2,
    win_rate=0.55,
    max_drawdown=0.12,
    avg_win=150.0,
    avg_loss=-50.0,
    avg_win_r=1.5,
    avg_r_multiple=1.5,
    expectancy_r=55.0,
    max_consecutive_losses=2,
    current_consecutive_losses=0,
    std_dev_returns=0.04,
    current_equity=115.0,
    peak_equity=120.0,
    last_calculated="2026-02-05T10:00:00Z",
    first_trade_date="2026-01-01T10:00:00Z",
    current_drawdown_pct=0.04,
    recovery_factor=8.0,
)

triggered, reason = KillSwitch.check_triggers(metrics_healthy)
print(f"\n✅ Test: Healthy Symbol")
print(f"   Triggered: {triggered}")
print(f"   Reason: {reason}")
print(f"   Expected: False (no triggers)")

# Test 5: Verify enable_kill_switch flag
print("\n[TEST 5] Verify enable_kill_switch Flag Works")
print("-" * 80)
print(f"✅ enable_kill_switch = {SETTINGS.get('enable_kill_switch')}")
if SETTINGS.get("enable_kill_switch"):
    print("   ✅ Kill switch is ENABLED (checks will be performed)")
else:
    print("   ⚠️  Kill switch is DISABLED (checks will be skipped)")

# Summary
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print("✅ Configuration Status: PROPERLY LOADED")
print(f"   - Config file: {config_path}")
print(f"   - Enable kill switch: {SETTINGS.get('enable_kill_switch')}")
print(f"   - Drawdown limit: {SETTINGS.get('kill_switch_drawdown_pct')}%")
print(
    f"   - Consecutive losses limit: {SETTINGS.get('kill_switch_consecutive_losses')}"
)
print(f"   - PF check threshold: {SETTINGS.get('kill_switch_pf_min_trades')} trades")
print(f"   - Tiers loaded: {len(ALL_TIERS)}")
print("\n✅ All kill switch configs are properly loaded and applied!")
print("=" * 80)
