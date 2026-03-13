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
post_date: "2026-03-13"
---

## Current Focus

- **Production Hardening (Phase 7)**: Fixing critical bugs discovered during live simulation:
  - Order recovery on restart
  - Performance metrics accuracy
  - Autonomous grid auto-restart after hitting session TP

## Recent Changes (2026-03-13)

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
  "grid_auto_restart": true             // Auto-restart stopped grids
}
```

## Next Steps

1. Restart bot and verify grids recover from existing orders
2. Monitor ADA/USDT which was stuck due to regime detection
3. Verify auto-restart works after cooldown period
