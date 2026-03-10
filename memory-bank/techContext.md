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
post_date: "2026-02-22"
---

## Stack

- Python, asyncio, ccxt.async_support.
- Pydantic settings with YAML defaults.
- Pytest with pytest-asyncio.

## Constraints

- No hardcoded values in `src/`.
- Secrets live only in `.env`.
- Exchange I/O requires rate limiting and retries.
