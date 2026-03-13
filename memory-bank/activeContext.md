---
post_title: "Memory Bank - Active Context"
author1: "Grid Bot Team"
post_slug: "memory-bank-active-context"
microsoft_alias: "n/a"
featured_image: "https://example.com/placeholder.png"
categories:
  - internal-docs
tags:
  - memory-bank
  - active-context
ai_note: "Generated with AI assistance."
summary: "Current focus, recent changes, and next steps."
post_date: "2026-03-14"
---

## Current Focus

- **Capital-Aware Grid Sizing**: Implementing dynamic grid parameter calculation based on allocated capital, tier allocation, and leverage constraints.

## Recent Changes (2026-03-14)

### Feature: Capital-Aware Grid Order Sizing

1. **Problem Identified**:
   - Grid deployed 50 levels × $30/order = $1,500 exposure
   - Symbol capital only $100 (30% of $100 = $30 effective)
   - Exchange rejected orders with InsufficientFunds

2. **Solution Implemented**:
   - Added `InsufficientGridCapital` exception
   - Added `grid_capital_constraint` config (default: True)
   - Added `grid_leverage` config (optional override)
   - Implemented `_calculate_grid_parameters()` in orchestrator.py

3. **Calculation Formula**:
   ```
   allocated_margin = capital × tier_allocation
   margin_per_level = min_notional / leverage
   max_levels = allocated_margin / margin_per_level
   ```

4. **Example Result**:
   ```
   Capital: $100, Tier: PROBATION (30%, 2x leverage)
   → 6 buy + 6 sell levels @ $5/order
   (was 25+25 levels, reduced to capital constraints)
   ```

## Previous Changes (2026-03-13)

### Bug Fixes

1. **Performance Metrics** (`adaptive_risk_manager.py`):
   - Was: Used hardcoded `100.0` as starting equity
   - Now: Uses actual `initial_capital` from config (e.g., 2000)
   - Impact: `symbol_performance.json` now shows correct equity values

2. **Order Recovery** (`order_state_manager.py`):
   - Was: Symbol mismatch - orders stored as "BTCUSDT", queried as "BTC/USDT"
   - Now: Uses `normalize_to_market_format()` for both storage and queries
   - Impact: 991 orders now properly recovered on restart

3. **Grid Auto-Restart** (`orchestrator.py` + `strategy_config.py`):
   - New: `grid_auto_restart` config option (default: True)
   - New: `_maybe_restart_grid()` method with cooldown
   - Impact: Grid automatically restarts after hitting 5% TP or max DD

### New Config Options

```json
{
  "grid_session_tp_reinvest": true,   // Re-invest after TP (default)
  "grid_session_tp_pct": "0.05",       // 5% TP threshold
  "grid_session_max_dd_pct": "0.07",   // 7% DD threshold
  "grid_reinvest_min_interval_seconds": 60,
  "grid_auto_restart": true,
  "grid_capital_constraint": true,      // NEW: Capital-aware sizing
  "grid_leverage": null                // NEW: Override leverage (null = use tier)
}
```

## Next Steps

1. Test capital-aware grid sizing with live symbols
2. Verify grid levels scale up as capital/tier improves
3. Monitor for any edge cases with minimal capital
