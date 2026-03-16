---
post_title: "Memory Bank - System Patterns"
author1: "Grid Bot Team"
post_slug: "memory-bank-system-patterns"
microsoft_alias: "n/a"
featured_image: "https://example.com/placeholder.png"
categories:
  - internal-docs
tags:
  - memory-bank
  - system-patterns
ai_note: "Generated with AI assistance."
summary: "Key architecture patterns and design decisions."
post_date: "2026-02-22"
---

## Architecture Pattern

- Strict layer pipeline with one-directional dependencies.
- Async-first I/O using `ccxt.async_support`.

## Design Principles

- Pure strategy functions with no side effects.
- Structured logging with context at layer boundaries.
- Configuration as the source of truth.

## Performance Patterns

- **O(N) Backtesting:** All technical indicators are pre-calculated for the entire dataset before entering the simulation loop. This avoids the O(N²) overhead of recalculating indicators on growing windows at every bar.
- **Worker Level Silencing:** Multi-processing workers in the optimizer are set to `CRITICAL` log level to prevent I/O bottlenecks during high-volume parameter searches.

## Validation & Verification Patterns

- **Honest Optimizer Logic:** Best parameters are verified against the **full data period** (combined Train+Test) before being saved. This ensures metrics match standalone backtests and prevents "luck" in short validation windows.
- **Strict Data Alignment:** Both `optimize_params.py` and `run_backtest.py` share identical data fetching and lookback logic to ensure 1:1 metric consistency.
- **Min Trades Guardrail:** A mandatory `MIN_TRADES` filter is applied to all results to ensure statistical significance and exclude low-activity outlier configurations.

## Production Integration Pattern (bot_v2)

- **Unified Orchestration:** The Grid logic is integrated as a specialized `GridOrchestrator` module within `bot_v2`, allowing side-by-side execution of grid strategies and signal-based trades.
- **Enhanced Execution:** Grid deployment utilizes the production `OrderManager` for unified safety checks (notional caps, daily limits) and automatic amount normalization.
- **State Resilience:** Grid session states (centre price, active orders) are persisted via `StateManager`, ensuring the bot can recover grid positions after restarts.
- **Webhook Command Layer:** `webhook_server.py` acts as the remote management layer, translating incoming signals into `grid_start` and `grid_stop` lifecycle events.
- **Production Entry Point:** `run_grid_bot.sh` provides a single script to activate the virtual environment and launch the webhook server, ensuring all dependencies are sourced correctly.

## Simulated Exchange Behavior Pattern (2026-03-16)

- **Live Parity Principle**: `SimulatedExchange.check_fills()` must behave identically to `LiveExchange.check_fills()` for fill detection.
- **Fill Logic**: Orders fill based on `current_price` crossing order price only:
  - Buy orders: `current_price <= order_price` (price drops to order level)
  - Sell orders: `current_price >= order_price` (price rises to order level)
- **No Candle-Based Detection**: Never use candle high/low for fill simulation as it causes cascade fills when the same candle is checked repeatedly.
- **Interface Compatibility**: Both implementations share the same `check_fills()` signature for polymorphic dispatch.
- **Order State Management**: Fills are tracked via `OrderStateManager`, not in the exchange implementation.
