# Copilot Instructions — Grid Trading Bot

## Project Overview

A **greenfield, fully standalone** Python Grid Trading Bot that profits from ranging cryptocurrency markets using layered limit orders. Supports Spot and Futures markets via `ccxt`. The project is currently in the **Planned** phase — source files are to be created from scratch inside this repository; no imports from any external project directory are permitted.

---

## Build & Run

```bash
# Install dependencies
pip install -r requirements.txt

# Start the bot
bash run_grid_bot.sh
# or directly:
python main.py
```

## Testing

```bash
# Full test suite
pytest

# Single test file
pytest tests/test_grid_calculator.py

# Single test by name
pytest tests/test_grid_calculator.py::test_geometric_levels -v

# Async tests use pytest-asyncio (already configured)
pytest tests/test_price_feed.py
```

---

## Architecture

The bot is structured as a strict **6-layer pipeline** where each layer only depends on layers below it:

```
Config → Data → Strategy → OMS → Risk → Monitoring
```

| Layer | Location | Responsibility |
|-------|----------|----------------|
| Config | `config/settings.py` + `config/grid_config.yaml` | Pydantic BaseSettings loads `.env` secrets; YAML holds strategy params |
| Data | `src/data/price_feed.py` | Historical OHLCV (Parquet-cached) + real-time REST polling |
| Exchange | `src/exchange/exchange_client.py` | Async `ccxt` wrapper; all exchange I/O lives here |
| Strategy | `src/strategy/` | Stateless: regime detection (ADX + BB width) + grid level calculation |
| OMS | `src/oms/` | In-memory grid-level → order-ID map; fill polling; counter-order triggers |
| Risk | `src/risk/risk_manager.py` | Stop-loss, max-drawdown, take-profit, ADX circuit breakers |
| Persistence | `src/persistence/state_store.py` | Atomic JSON read/write for crash recovery (`data/state/grid_state.json`) |
| Monitoring | `src/monitoring/alerting.py` | Telegram Bot API alerts |
| Backtest | `src/backtest/grid_backtester.py` | Bar-by-bar OHLCV simulation engine |

`main.py` is the async entry point that wires all layers together.

---

## Key Conventions

### Configuration
- **All configurable values live in `config/grid_config.yaml`** (strategy params) or `.env` (secrets). No hardcoded values anywhere in `src/`.
- `config/settings.py` uses `Pydantic BaseSettings`. YAML defaults are passed as kwargs to `GridBotSettings(**yaml_defaults)`. ENV vars override YAML.
- Commit `.env.example`, never `.env`.

### Async-first
- All exchange calls and I/O are `async`/`await` using `ccxt.async_support`.
- `enableRateLimit=True` is mandatory on every `ccxt` exchange instance.
- All network calls wrap with exponential backoff retry (delays: `[1, 2, 5]` seconds, max 3 attempts) catching `ccxt.NetworkError` and `ccxt.RequestTimeout` only.

### Data Models
- Use **Python dataclasses** or **Pydantic models** for all inter-layer data transfer objects (e.g., `GridLevel`, `RegimeInfo`, `OrderRecord`).
- `MarketRegime` and `OrderStatus` are `Enum` types defined in their package's `__init__.py`.

### Strategy is stateless
- `regime_detector.py` and `grid_calculator.py` are **pure functions** — no instance state, no side effects. Inputs in, results out.
- Regime detection: ADX(14) via `ta.trend.ADXIndicator` + Bollinger Band width via `ta.volatility.BollingerBands`. Bot only deploys grid when regime is `RANGING`; cancels all orders on switch to `TRENDING`.
- Grid spacing: `geometric` mode uses fixed `%` gap (`GRID_SPACING_PCT`); `arithmetic` mode uses fixed `$` gap (`GRID_SPACING_ABS`). Grid prices must be quantized to exchange `price_step`.

### OHLCV Caching
- Historical candles are cached as Parquet at `data/cache/ohlcv_cache/{SYMBOL}_{TIMEFRAME}.parquet`.
- Cache is considered fresh if file mtime is within one candle period. Stale cache triggers a fresh fetch.
- Symbol `/` is replaced with `_` for safe filenames (e.g., `BTC_USDT_1h.parquet`).

### Persistence
- Runtime state is written atomically to `data/state/grid_state.json` for crash recovery.
- This file is git-ignored.

### Logging
- Use `structlog` (or Python `logging` with structured output) at every layer boundary.
- Log entries must include context (order IDs, prices, regime state) — not just free-text messages.

### Testing
- `pytest` + `pytest-asyncio` for all tests.
- Use `pytest-mock` to mock exchange calls — never hit live APIs in tests.
- Test files mirror `src/` structure under `tests/`.

---

## Environment Variables (`.env`)

Required:
```
EXCHANGE_ID=binance          # ccxt exchange ID
MARKET_TYPE=spot             # 'spot' or 'futures'
API_KEY=...
API_SECRET=...
```

Optional alerting:
```
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TESTNET=false
```

All other parameters (grid shape, risk limits, timing) are controlled via `config/grid_config.yaml`.

---

## Implementation Phases

The project is built in 5 atomic phases, each documented in `plan/`:

| Phase | File | Status |
|-------|------|--------|
| 1 — Foundation & Data | `feature-grid-bot-phase1-1.md` | Planned |
| 2 — Strategy Core | `feature-grid-bot-phase2-1.md` | Planned |
| 3 — Execution Engine (OMS & Risk) | `feature-grid-bot-phase3-1.md` | Planned |
| 4 — Persistence & Monitoring | `feature-grid-bot-phase4-1.md` | Planned |
| 5 — Backtesting & Verification | `feature-grid-bot-phase5-1.md` | Planned |

Always read the relevant phase plan file before implementing any module.

---

## Agent Roles (`.github/agents/`)

| Agent | Model | Role |
|-------|-------|------|
| `orchestrator` | Claude Sonnet 4.5 | Breaks tasks into phases, delegates to specialists, never writes code |
| `planner` | Claude Haiku 4.5 | Researches codebase + docs, outputs ordered steps — no code |
| `coder` | Claude Haiku 4.5 | Implements code per plan; always consults Context7 for library docs |
| `designer` | Gemini 3.1 Pro | UI/UX tasks (dormant for Phase 1-5) |
| `tdd-red` | Claude Haiku 4.5 | Write failing tests first from GitHub issues before implementation |
| `tdd-green` | Claude Haiku 4.5 | Implement minimal code to make tests pass quickly |
| `tdd-refactor` | Claude Haiku 4.5 | Improve quality, security, design while keeping tests green |
| `context7` | Claude Sonnet 4.5 | Up-to-date library docs via MCP (ccxt, pydantic, pytest-asyncio, ta-lib) |
| `critical-thinking` | Gemini 3 Pro | Challenge assumptions, identify edge cases before major decisions |
| `prompt-engineer` | Claude Sonnet 4.5 | Analyze and improve prompts using systematic framework |
| `memory-updater` | Claude Haiku 4.5 | Updates Memory Bank after each phase completion milestone |
| `janitor` | Claude Haiku 4.5 | Code cleanup, tech debt removal, dependency audit |
| `security-auditor` | Claude Haiku 4.5 | Pre-deployment security gates; API key scans, testnet validation |
| `async-sheriff` | Claude Haiku 4.5 | Async pattern validation; rate limiting checks, deadlock prevention |
| `performance-optimizer` | Claude Haiku 4.5 | Profile hot paths, optimize async loops, reduce memory overhead |
| `documentation-specialist` | Claude Sonnet 4.5 | API docs, user guides, diagrams, operations runbooks |
| `devops-engineer` | Claude Haiku 4.5 | Docker, CI/CD, monitoring stack, secret management, blue-green deploys |
| `data-analyst` | GPT-4.1 | Backtest analysis, performance metrics, risk stats, reporting |
| `4.1-beast` | GPT-4.1 | Autonomous complex problem solving with extensive research |

## Agent Usage Notes

- Use `documentation-specialist` for user guides, architecture diagrams, and runbooks.
- Use `performance-optimizer` when latency, throughput, or memory regressions are in scope.
- Use `devops-engineer` for Docker, CI/CD, monitoring, or deployment changes.
- Use `data-analyst` for backtest result analysis and reporting.

### TDD Workflow
The bot uses Test-Driven Development for all Grid Bot implementations:
1. **TDD Red** → Write failing tests from GitHub issue acceptance criteria
2. **TDD Green** → Implement minimal code to pass tests
3. **TDD Refactor** → Improve code quality, apply security best practices, maintain green tests

All code changes should follow this Red-Green-Refactor cycle with proper Grid Bot conventions (async, retry logic, mocking).

The Coder agent uses Context7 MCP to look up current library documentation before writing any code involving `ccxt`, `pydantic`, `ta`, etc.
