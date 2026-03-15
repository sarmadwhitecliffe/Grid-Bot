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
post_date: "2026-03-16"
---

## Current Focus

- **QA Assessment Fixes**: Applied fixes based on log analysis of ~50min runtime (1,858 orders, 1,416 fills, $100→$171.54)
- **bot_v2 Running**: 9 grids deployed (BTC, ETH, LINK, BCH, SOL, ADA, NEAR, APT, OP), OP has 7 fills

## Recent Changes (2026-03-16)

### QA Assessment Fixes

1. **Order Pruning (CRITICAL)**:
   - Problem: 0 CANCELED orders - FILLED orders never pruned, prune interval was 6 HOURS
   - Solution: Changed prune interval from 21600s → 300s (5 min), added fill-based pruning every 100 fills
   - Files: `bot_v2/bot.py`

2. **grid_level_id Tracking (MEDIUM)**:
   - Problem: Fill events had no reference to which grid level triggered them
   - Solution: Added `level_index` to initial grid orders, propagated to fill_event dict
   - Files: `bot_v2/grid/orchestrator.py`

3. **parent_order_id Tracking (MEDIUM)**:
   - Problem: Counter-orders had no reference to the order that triggered them
   - Solution: Added `parent_order_id` to counter-order metadata
   - Files: `bot_v2/grid/orchestrator.py`

4. **Price/Amount Precision Fix (MEDIUM)**:
   - Problem: Excessive precision (30+ decimal places) in amounts/prices
   - Solution: Quantized prices/amounts to exchange precision in OrderManager
   - Files: `bot_v2/execution/order_manager.py`
   - Added: `_get_market_precision()`, `_quantize_price()`, `_quantize_amount()`

5. **Grid Leverage Fix (HIGH)**:
   - Problem: Grid orders not setting leverage - using default 1x or fractions
   - Solution: Added leverage setting before grid deployment in orchestrator
   - Files: `bot_v2/grid/orchestrator.py`
   - Added: `_set_leverage()`, `_get_leverage_from_config()`, leverage cache
   - Calculates: base_leverage × tier_multiplier, capped at max_leverage_cap

### Runtime Results (Before Fixes)
- Runtime: ~50 minutes
- Orders created: 1,858
- Fills: 1,416
- Capital: $100 → $171.54 (+71.5%)
- **CRITICAL**: 0 CANCELED orders (FILLED orders never pruned!)
- Only OP/USDT had fills (1,414), other symbols 0 fills

## Previous Changes (2026-03-14)

### Capital-Aware Grid Sizing

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

## Next Steps

1. Verify pruning now works (CANCELED count > 0)
2. Verify fill_log.jsonl has grid_level_id and parent_order_id
3. Verify precision is reasonable in orders
4. Monitor bot running via webhook_server.py
