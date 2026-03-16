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

### 5. Leverage Multiplier Implementation (2026-03-14)
- **Problem**: Backtest-optimized leverage (e.g., 10x for BTC) didn't match adaptive risk tiers (PROBATION max 2x)
- **Solution**: Implemented leverage multiplier per tier, applied to strategy_config leverage
- **Files Changed**:
  - `config/adaptive_risk_tiers.json` - Replaced `min_leverage`/`max_leverage` with `leverage_multiplier` and `max_leverage_cap`
  - `bot_v2/risk/adaptive_risk_manager.py` - Updated `RiskTier` dataclass and `PositionSizer.calculate_position_size()`
- **New Tier Configuration**:
  - PROBATION: 0.5x multiplier (max 10x cap)
  - CONSERVATIVE: 0.7x multiplier (max 15x cap)
  - STANDARD: 1.0x multiplier (max 20x cap)
  - AGGRESSIVE: 1.0x multiplier (max 25x cap)
  - CHAMPION: 1.2x multiplier (max 30x cap)
- **Calculation**: `final_leverage = strategy_config_leverage × tier_multiplier`
- **Example**: BTC with 10x config leverage:
  - PROBATION: 10 × 0.5 = 5x
  - CHAMPION: 10 × 1.2 = 12x

### 6. QA Assessment Fixes (2026-03-16)
Based on log analysis of ~50min runtime (1,858 orders, 1,416 fills, $100→$171.54):

#### 6a. Order Pruning (CRITICAL)
- **Problem**: 0 CANCELED orders - FILLED orders never pruned, prune interval was 6 HOURS
- **Solution**: Changed prune interval from 21600s → 300s (5 min), added fill-based pruning (every 100 fills)
- **Files Changed**:
  - `bot_v2/bot.py` - Updated `PRUNE_INTERVAL_SECONDS` and added fill-based pruning threshold
  - Added `_total_fills_since_last_prune` counter

#### 6b. grid_level_id Tracking (MEDIUM)
- **Problem**: Fill events had no reference to which grid level triggered them
- **Solution**: Added `level_index` to initial grid orders, propagated to fill events
- **Files Changed**:
  - `bot_v2/grid/orchestrator.py` - Added `level_index` to order params, included in fill_event dict

#### 6c. parent_order_id Tracking (MEDIUM)
- **Problem**: Counter-orders had no reference to the order that triggered them
- **Solution**: Added `parent_order_id` to counter-order metadata
- **Files Changed**:
  - `bot_v2/grid/orchestrator.py` - Added `parent_order_id` to counter-order metadata

#### 6d. Price/Amount Precision Fix (MEDIUM)
- **Problem**: Excessive precision (30+ decimal places) in amounts/prices
- **Solution**: Quantized prices/amounts to exchange precision in OrderManager
- **Files Changed**:
  - `bot_v2/execution/order_manager.py` - Added `_get_market_precision()`, `_quantize_price()`, `_quantize_amount()`
  - Uses exchange market info for price_step/amount_step
  - Falls back to 0.0001/0.001 if unavailable

#### 6e. Grid Leverage Fix (HIGH)
- **Problem**: Grid orders not setting leverage - using default 1x or fractions
- **Solution**: Added leverage setting before grid deployment in orchestrator
- **Files Changed**:
  - `bot_v2/grid/orchestrator.py` - Added `_set_leverage()`, `_get_leverage_from_config()`, leverage cache
  - Calculates: base_leverage × tier_multiplier, capped at max_leverage_cap
  - Called before every grid deployment (including re-centering)

## Remaining to Build

- Test capital-aware grid sizing with live symbols
- Verify grid levels scale up as capital/tier improves

## Known Issues

- Previously: Grid exceeded capital limits (fixed)
- Previously: Order recovery failed on restart (fixed)
- Previously: Performance metrics showed wrong equity values (fixed)
- Previously: Grid stopped after TP and required manual restart (fixed)
- Previously: Leverage mismatch between backtest and adaptive risk (fixed)
- Previously: Orders never pruned causing memory buildup (fixed 2026-03-16)
- Previously: No grid_level_id in fill events (fixed 2026-03-16)
- Previously: No parent_order_id for counter-orders (fixed 2026-03-16)
- Previously: Excessive precision in order amounts (fixed 2026-03-16)
- Previously: Grid orders not setting leverage before placement (fixed 2026-03-16)

### 7. Simulated Exchange Fill Behavior Fix (2026-03-16)
- **Problem**: Local simulation generated 95+ trades in ~2.5 minutes due to candle-based fill detection causing cascade fills
- **Root Cause**: 
  - `check_fills()` used candle high/low range for fill simulation
  - Same cached candle (60s TTL) was checked every tick (~1s)
  - Orders within candle range would fill repeatedly on each tick
  - Counter-orders spawned and immediately filled, causing cascade
- **Solution**: Changed simulated exchange to match live behavior exactly
  - `check_fills()` now uses `current_price` only (not candle range)
  - Buy orders fill when `current_price <= order_price`
  - Sell orders fill when `current_price >= order_price`
  - Removed unused candle_high/candle_low/candle_timestamp parameters
- **Files Changed**:
  - `bot_v2/execution/simulated_exchange.py` - Simplified `check_fills()` method
  - `bot_v2/execution/live_exchange.py` - Added unused parameters for interface compatibility
  - `bot_v2/bot.py` - Removed candle data extraction from tick processing
  - `bot_v2/grid/orchestrator.py` - Simplified fill detection call
- **Result**: Simulated exchange now behaves identically to live exchange for fill detection

## Known Issues

- Previously: Grid exceeded capital limits (fixed)
- Previously: Order recovery failed on restart (fixed)
- Previously: Performance metrics showed wrong equity values (fixed)
- Previously: Grid stopped after TP and required manual restart (fixed)
- Previously: Leverage mismatch between backtest and adaptive risk (fixed)
- Previously: Orders never pruned causing memory buildup (fixed 2026-03-16)
- Previously: No grid_level_id in fill events (fixed 2026-03-16)
- Previously: No parent_order_id for counter-orders (fixed 2026-03-16)
- Previously: Excessive precision in order amounts (fixed 2026-03-16)
- Previously: Grid orders not setting leverage before placement (fixed 2026-03-16)
- Previously: Simulated exchange cascade fills due to candle-based detection (fixed 2026-03-16)
