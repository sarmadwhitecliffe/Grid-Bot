---
goal: 'Implement a fully standalone Grid Trading Bot as a brand-new, independent Python project'
version: '2.0'
date_created: '2026-02-22'
last_updated: '2026-02-22'
owner: 'Antigravity'
status: 'Complete'
tags: ['feature', 'architecture', 'grid-bot', 'standalone']
---

# Grid Trading Bot — Master Plan

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

This master plan coordinates the end-to-end implementation of a brand-new, **fully standalone** Grid Trading Bot Python project. This is a **greenfield repository** with zero dependencies on any pre-existing codebase. Every module described in this plan must be written from scratch within the new project workspace.

The bot is designed to profit from sideways (ranging) market oscillations using a layered limit-order strategy. It supports both Spot and Futures cryptocurrency markets via the `ccxt` library.

---

## 1. Requirements & Constraints

### Functional Requirements

| ID | Requirement |
|----|-------------|
| **REQ-001** | Standalone project folder `grid_trading_bot/` with its own `requirements.txt`, `README.md`, and `.env`. |
| **REQ-002** | 6-layer architecture: Config → Data → Strategy → OMS → Risk → Monitoring. |
| **REQ-003** | Support both **Spot** and **Futures** markets via `ccxt`. Market type switchable via config. |
| **REQ-004** | Mandatory **regime detection** before placing any orders. Bot must not trade during trending conditions. |
| **REQ-005** | Grid spacing must support both **Arithmetic** (fixed price gap `$`) and **Geometric** (fixed `%` gap) modes. |
| **REQ-006** | Full **Limit Order Management (LOM)**: place, poll, cancel, and reconcile limit orders. |
| **REQ-007** | **Persistent state** written to disk (`data/state/grid_state.json`) for crash recovery and session resumption. |
| **REQ-008** | **Independent Telegram alerting** using a dedicated bot token and chat ID stored in `.env`. |
| **REQ-009** | Integrated **backtesting engine** for bar-by-bar strategy simulation against historical OHLCV data. |
| **REQ-010** | All configurable parameters (grid params, risk limits, API keys) must be in `config/settings.py` / `config/grid_config.yaml` — no hardcoded values in core logic. |

### Non-Functional Constraints

| ID | Constraint |
|----|------------|
| **CON-001** | This is a **greenfield project**. No imports from any external existing project directory are permitted. |
| **CON-002** | Performance targets: Win Rate ≥ 80%, Profit Factor ≥ 1.5, Max Drawdown ≤ 15%. |
| **CON-003** | API credentials must **never** be hardcoded; load only from environment variables via `.env`. |
| **CON-004** | `ccxt` rate limits must be strictly respected via built-in `rateLimit` and `enableRateLimit=True`. |
| **CON-005** | All networking must be **async** using `asyncio` and `ccxt.pro` (WebSocket) or async REST polling. |

### Patterns & Standards

| ID | Pattern |
|----|---------|
| **PAT-001** | Use **Pydantic `BaseSettings`** for configuration validation and loading from environment. |
| **PAT-002** | Use **YAML** (`grid_config.yaml`) for human-editable grid and risk parameters. |
| **PAT-003** | All classes use Python dataclasses or Pydantic models for data transfer. |
| **PAT-004** | Use `structlog` or Python's built-in `logging` with structured output for all log events. |
| **PAT-005** | Use `pytest` and `pytest-asyncio` for all unit and integration tests. |

---

## 2. Project Folder Structure

The new project will live in its own isolated directory (e.g. `grid_trading_bot/`). The complete expected folder structure is:

```
grid_trading_bot/
├── config/
│   ├── __init__.py
│   ├── settings.py           # Pydantic BaseSettings — loads from .env
│   └── grid_config.yaml      # Human-editable YAML: grid params, risk limits
├── src/
│   ├── __init__.py
│   ├── exchange/
│   │   ├── __init__.py
│   │   └── exchange_client.py     # ccxt async wrapper (place/cancel/status limit orders)
│   ├── data/
│   │   ├── __init__.py
│   │   └── price_feed.py          # Historical OHLCV fetcher + real-time WebSocket/polling feed
│   ├── strategy/
│   │   ├── __init__.py            # GridLevel, RegimeInfo dataclasses
│   │   ├── regime_detector.py     # ADX + Bollinger Band width range/trend classification
│   │   └── grid_calculator.py     # Arithmetic & Geometric grid level generator
│   ├── oms/
│   │   ├── __init__.py            # OrderRecord dataclass, OrderStatus enum
│   │   ├── order_manager.py       # In-memory grid-level → order-ID map, lifecycle management
│   │   └── fill_handler.py        # Poll fills, trigger counter-orders, re-centre logic
│   ├── risk/
│   │   ├── __init__.py            # RiskAction enum
│   │   └── risk_manager.py        # Stop-loss, max-drawdown, take-profit, ADX-pause circuit breakers
│   ├── persistence/
│   │   ├── __init__.py
│   │   └── state_store.py         # Atomic JSON read/write for crash recovery
│   ├── monitoring/
│   │   ├── __init__.py
│   │   └── alerting.py            # Telegram Bot API wrapper (send_message, rate-limited)
│   └── backtest/
│       ├── __init__.py
│       └── grid_backtester.py     # Bar-by-bar OHLC limit-order simulation engine
├── data/
│   ├── state/
│   │   └── grid_state.json        # Runtime-generated persistent state (git-ignored)
│   ├── cache/
│   │   └── ohlcv_cache/           # Parquet-cached historical OHLCV files
│   └── logs/
│       └── grid_bot.log           # Runtime log file (git-ignored)
├── tests/
│   ├── __init__.py
│   ├── test_grid_calculator.py
│   ├── test_regime_detector.py
│   ├── test_order_manager.py
│   ├── test_risk_manager.py
│   ├── test_state_store.py
│   └── test_backtester.py
├── main.py                        # Async entry point — orchestrates all layers
├── run_grid_bot.sh                # Bash launcher (venv activation + python main.py)
├── requirements.txt               # All Python dependencies
├── .env.example                   # Template for API keys (commit this, not .env)
├── .env                           # Actual API keys (add to .gitignore)
├── .gitignore
└── README.md
```

---

## 3. Implementation Phases

The implementation is divided into **5 atomic phases**, each delivered as a separate plan file.

| Phase | Plan File | Description | Sprint | Status |
|-------|-----------|-------------|--------|--------|
| [Phase 1](./feature-grid-bot-phase1-1.md) | `feature-grid-bot-phase1-1.md` | Foundation & Data Ingestion | Sprint 1 | Complete |
| [Phase 2](./feature-grid-bot-phase2-1.md) | `feature-grid-bot-phase2-1.md` | Strategy Core (Regime & Calculator) | Sprint 1 | Complete |
| [Phase 3](./feature-grid-bot-phase3-1.md) | `feature-grid-bot-phase3-1.md` | Execution Engine (OMS & Risk) | Sprint 2 | Complete |
| [Phase 4](./feature-grid-bot-phase4-1.md) | `feature-grid-bot-phase4-1.md` | Persistence & Monitoring | Sprint 2 | Complete |
| [Phase 5](./feature-grid-bot-phase5-1.md) | `feature-grid-bot-phase5-1.md` | Backtesting & Final Verification | Sprint 3 | Complete |

---

## 4. Configuration Reference

All parameters are defined in `config/grid_config.yaml` and loaded via `config/settings.py`.

### Grid Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `SYMBOL` | str | `BTC/USDT` | Trading pair |
| `MARKET_TYPE` | str | `spot` | `spot` or `futures` |
| `GRID_TYPE` | str | `geometric` | `arithmetic` (fixed $ gap) or `geometric` (fixed % gap) |
| `GRID_SPACING_PCT` | float | `0.01` | Gap between levels as a decimal (0.01 = 1%) |
| `GRID_SPACING_ABS` | float | `50.0` | Gap between levels in quote currency (arithmetic mode) |
| `NUM_GRIDS_UP` | int | `10` | Number of sell limit levels above centre price |
| `NUM_GRIDS_DOWN` | int | `10` | Number of buy limit levels below centre price |
| `ORDER_SIZE_QUOTE` | float | `100.0` | Capital in USDT per grid level order |
| `LOWER_BOUND` | float | `null` | Hard lower boundary (manual key support) |
| `UPPER_BOUND` | float | `null` | Hard upper boundary (manual key resistance) |

### Capital Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `TOTAL_CAPITAL` | float | — | Total USDT allocated to this bot instance |
| `RESERVE_CAPITAL_PCT` | float | `0.10` | Fraction kept in reserve as a re-centering buffer |
| `MAX_OPEN_ORDERS` | int | `20` | Hard cap on simultaneous live limit orders |

### Risk Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `STOP_LOSS_PCT` | float | `0.05` | Pause trading if price drops > 5% below lower bound |
| `MAX_DRAWDOWN_PCT` | float | `0.15` | Full emergency close if equity drops by 15% |
| `TAKE_PROFIT_PCT` | float | `0.30` | Lock profits and restart if cumulative gain hits 30% |
| `ADX_THRESHOLD` | int | `25` | Pause bot if ADX rises above this (market trending) |
| `RECENTRE_TRIGGER` | int | `3` | Re-centre grid if price drifts > N grid levels from centre |
| `POLL_INTERVAL_SEC` | int | `10` | Seconds between order status polling cycles |

---

## 5. Key Dependencies

All dependencies are installed via `pip install -r requirements.txt`. This is a **standalone** project; no dependency on any external project codebase.

```
# requirements.txt

# Exchange connectivity
ccxt>=4.2.0
ccxt.pro>=4.2.0          # async WebSocket edition

# Configuration
pydantic>=2.0.0
pydantic-settings>=2.0.0
python-dotenv>=1.0.0
PyYAML>=6.0.0

# Technical indicators (self-contained, no external project deps)
ta>=0.11.0               # ADX, Bollinger Bands, ATR indicators

# Data handling
pandas>=2.0.0
numpy>=1.26.0
pyarrow>=14.0.0          # Parquet caching for OHLCV data

# Alerting
python-telegram-bot>=20.0

# Async utilities
asyncio-throttle>=1.0.2

# Testing
pytest>=7.4.0
pytest-asyncio>=0.23.0
pytest-mock>=3.12.0

# Logging
structlog>=23.0.0
```

---

## 6. Testing Strategy

| Test ID | Scope | Tools |
|---------|-------|-------|
| **TEST-001** | Unit: Grid level generation math (arithmetic & geometric) | `pytest` |
| **TEST-002** | Unit: Regime detection ADX + BB width logic | `pytest` with synthetic OHLCV fixtures |
| **TEST-003** | Integration: Mock exchange limit order lifecycle | `pytest-mock` |
| **TEST-004** | Integration: Persistence crash-recovery round-trip | `pytest` with tmp filesystem |
| **TEST-005** | System: End-to-end backtests on 90-day BTC/USDT data | `pytest` + backtest engine |

---

## 7. Risks & Assumptions

| ID | Category | Detail |
|----|----------|--------|
| **RISK-001** | Network | Latency or exchange downtime affecting limit order fills in fast markets. *Mitigation: exponential backoff retry on all API calls.* |
| **RISK-002** | Strategy | False range signals leading to grid-lock during sudden trend breakouts. *Mitigation: ADX circuit breaker immediately cancels all orders.* |
| **RISK-003** | Data | Rate-limit bans from exchange API during aggressive polling. *Mitigation: `enableRateLimit=True` in ccxt + adaptive sleep.* |
| **ASSUMPTION-001** | User provides valid Binance or Bybit API keys with Spot/Futures trading permissions. |
| **ASSUMPTION-002** | Python 3.11+ runtime is available on the deployment machine. |
| **ASSUMPTION-003** | Historical OHLCV data fetched from the exchange REST API is sufficient for regime detection (no paid data feeds needed). |

---

## 8. Original Specification

- [Original Specification](./grid-bot.md)
