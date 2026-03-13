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
