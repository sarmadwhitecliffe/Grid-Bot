---
post_title: "Memory Bank - Tech Context"
author1: "Grid Bot Team"
post_slug: "memory-bank-tech-context"
microsoft_alias: "n/a"
featured_image: "https://example.com/placeholder.png"
categories:
  - internal-docs
tags:
  - memory-bank
  - tech-context
ai_note: "Generated with AI assistance."
summary: "Technologies, setup, and constraints."
post_date: "2026-03-14"
---

## Stack

- Python, asyncio, ccxt.async_support.
- Pydantic settings with YAML defaults.
- Pytest with pytest-asyncio.

## Constraints

- No hardcoded values in `src/`.
- Secrets live only in `.env`.
- Exchange I/O requires rate limiting and retries.

## Grid Capital Management (2026-03-14)

- Grid orders now validate against allocated capital before deployment
- Formula: `max_levels = (capital × tier_allocation) / (min_notional / leverage)`
- Example: $100 capital, PROBATION (30%, 2x) → 6+6 levels @ $5/order
- Config: `grid_capital_constraint` (default: True), `grid_leverage` (optional)

## Simulated Exchange Fill Behavior (2026-03-16)

- `SimulatedExchange.check_fills()` uses `current_price` only (not candle range)
- Matches live behavior: fills occur when price crosses order price
- No deduplication needed - same logic as live exchange polling `fetch_my_trades()`
- Interface shared with `LiveExchange.check_fills()` for polymorphic dispatch

## Grid Bot Runtime (bot_v2)

- **Entry Point**: `webhook_server.py` (FastAPI) or `python main.py` (legacy)
- **Main Loop**: ~1 second tick interval (`BOTV2_IDLE_SLEEP_SECS`)
- **OHLCV Cache**: 60 second TTL (`OHLCV_TTL_SECONDS`)
- **Order Pruning**: Every 5 minutes (300s) or every 100 fills

## Exchange Interface

```
ExchangeInterface.check_fills(symbol, current_price, ...)
├── LiveExchange: fetch_my_trades() → return filled order IDs
└── SimulatedExchange: check open orders → return IDs where price crossed
```
