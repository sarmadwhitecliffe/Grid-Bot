---
post_title: "Memory Bank - Progress"
author1: "Grid Bot Team"
post_slug: "memory-bank-progress"
microsoft_alias: "n/a"
featured_image: "https://example.com/placeholder.png"
categories:
  - internal-docs
tags:
  - memory-bank
  - progress
ai_note: "Generated with AI assistance."
summary: "Progress, known issues, and phase status."
post_date: "2026-03-14"
---

## Status

- **Phase 1-5 (Core Development)**: Completed. The core architecture (Config, Data, Exchange, Strategy, OMS, Risk, Persistence, Monitoring) is fully implemented.
- **Phase 6 (Backtest Validation & Futures Transition)**: Completed.
- **Phase 7 (Production Hardening)**: In Progress.
  - Capital-aware grid sizing implemented.
  - Grid auto-restart after hitting session TP.
  - Order recovery fix on restart.

## Recent Fixes (2026-03-14)

### 1. Capital-Aware Grid Sizing
- **Problem**: Grid deployed 50 levels × $30 = $1,500 exposure, but capital only $100
- **Solution**: Implemented dynamic level calculation based on allocated margin
- **Files Changed**:
  - `bot_v2/models/exceptions.py` - Added `InsufficientGridCapital` exception
  - `bot_v2/models/strategy_config.py` - Added `grid_capital_constraint`, `grid_leverage`
  - `bot_v2/grid/orchestrator.py` - Added `_calculate_grid_parameters()`
  - `bot_v2/bot.py` - Passed `capital_manager` to orchestrator
- **Calculation**:
  ```
  allocated_margin = capital × tier_allocation
  margin_per_level = min_notional / leverage
  max_levels = allocated_margin / margin_per_level
  ```
- **Example**: $100 capital, PROBATION tier (30%, 2x) → 6+6 levels @ $5/order

### 2. Performance Metrics Fix (2026-03-13)
- Fixed `symbol_performance.json` using wrong initial capital (100 instead of actual config value)
- Added `initial_capital` parameter to `PerformanceAnalyzer.calculate_metrics()`
- Updated all callers to pass actual capital from config

### 3. Grid Auto-Restart Feature (2026-03-13)
- Added `grid_auto_restart` config option (default: True)
- Added `_maybe_restart_grid()` method with cooldown
- Grid now continues trading autonomously after hitting TP/DD

### 4. Order Recovery Fix (2026-03-13)
- Fixed symbol format mismatch in `OrderStateManager.get_open_orders_by_symbol()`
- Orders stored as "BTCUSDT" but queried as "BTC/USDT"
- Now uses `normalize_to_market_format()` from symbol_utils.py

## Remaining to Build

- Test capital-aware grid sizing with live symbols
- Verify grid levels scale up as capital/tier improves

## Known Issues

- Previously: Grid exceeded capital limits (fixed)
- Previously: Order recovery failed on restart (fixed)
- Previously: Performance metrics showed wrong equity values (fixed)
- Previously: Grid stopped after TP and required manual restart (fixed)
