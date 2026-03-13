---
description: 'Grid Bot project memory — architecture decisions, patterns, and hard-won lessons'
applyTo: '**'
---

# Grid Bot Project Memory

Authoritative patterns, conventions, and lessons for the standalone Python Grid Trading Bot. Follow these before writing any code or plan.

## Architecture: Strict 6-Layer Pipeline

The bot enforces a one-directional dependency chain. Each layer only imports from layers below it:

```
Config → Data → Exchange → Strategy → OMS → Risk → Persistence → Monitoring
```

Never let a lower layer import from a higher one. Violations break the isolation contract.

## Configuration Is the Source of Truth

- All strategy/trading parameters live in `config/grid_config.yaml`.
- All secrets (API keys, Telegram tokens) live in `.env` only — never committed.
- `config/settings.py` uses `Pydantic BaseSettings`; YAML defaults are passed as `GridBotSettings(**yaml_defaults)` and ENV vars override them.
- **No hardcoded values anywhere in `src/`.** If it's configurable, it belongs in YAML or `.env`.

## Async-First, Always

- All exchange I/O uses `ccxt.async_support` — never the synchronous ccxt client.
- Every ccxt exchange instance must set `enableRateLimit=True`.
- All network calls wrap with exponential backoff retry:
  - Max 3 attempts, delays `[1, 2, 5]` seconds
  - Catch only `ccxt.NetworkError` and `ccxt.RequestTimeout`

## Strategy Layer Is Purely Stateless

- `regime_detector.py` and `grid_calculator.py` are pure functions — no instance state, no side effects.
- Inputs produce outputs; they never call the exchange or write to disk.
- The bot deploys the grid only when regime is `RANGING`; it cancels all orders on switch to `TRENDING`.

## Grid Spacing Rules

- **Geometric**: fixed `%` gap per level (`GRID_SPACING_PCT`)
- **Arithmetic**: fixed `$` gap per level (`GRID_SPACING_ABS`)
- All computed grid prices must be quantized to the exchange `price_step` before placing orders to avoid rejection.

## Regime Detection Indicators

- ADX(14) via `ta.trend.ADXIndicator` — trending signal
- Bollinger Band Width via `ta.volatility.BollingerBands` — range compression signal
- Both computed from historical OHLCV fetched via the price feed
- **Pre-deployment check**: Regime is checked BEFORE grid deployment in `start()` method. If TRENDING, grid deployment is skipped.
- **Runtime throttling**: Regime is checked every 5 minutes (configurable via `grid_regime_check_interval_seconds`) during active grid to reduce false positives from brief price spikes.
- **Recovered grids**: First tick always runs regime check, then throttled.

## OHLCV Caching

- Cache location: `data/cache/ohlcv_cache/{SYMBOL}_{TIMEFRAME}.parquet`
- Replace `/` with `_` in symbol for safe filenames (e.g., `BTC_USDT_1h.parquet`).
- Cache is fresh if file mtime is within one candle period; stale cache triggers a fresh fetch.

## Persistence Pattern

- Runtime grid state is written atomically to `data/state/grid_state.json`.
- This file is git-ignored; it exists only for crash recovery.
- Use `src/persistence/state_store.py` for all reads and writes — never raw `open()` in other layers.

## Data Models

- Use Python `dataclass` or Pydantic models for all inter-layer DTOs (e.g., `GridLevel`, `RegimeInfo`, `OrderRecord`).
- `MarketRegime` and `OrderStatus` are `Enum` types defined in their package `__init__.py`.

## Logging Standards

- Use `structlog` (or Python `logging` with structured output).
- Every log entry must include context fields: order IDs, prices, regime state.
- Avoid free-text-only log messages — always attach the relevant data.

## Testing Rules

- `pytest` + `pytest-asyncio` for all tests.
- Mock all exchange calls with `pytest-mock` — never touch live APIs in tests.
- Test file structure mirrors `src/` under `tests/` (e.g., `tests/test_exchange_client.py`).
- Async tests require `@pytest.mark.asyncio` or the `asyncio_mode = "auto"` setting in `conftest.py`.

## Agent Roles (Orchestrator Mode)

| Agent | Role |
|-------|------|
| Planner | Research codebase + docs → output ordered steps (no code) |
| Coder | Implement per Planner's steps; reference `ccxt`, `pydantic`, `ta` docs before writing |

The Orchestrator never writes code. It calls Planner first, then delegates implementation to Coder(s). Parallel Coder calls are safe only when tasks touch different files.

## Phase Execution Order

Phases have hard layer-gate dependencies:

1. Config files (`settings.py`, `grid_config.yaml`)
2. Exchange client (`exchange_client.py`)
3. Price feed (`price_feed.py`)
4. Strategy modules (`regime_detector.py`, `grid_calculator.py`)
5. OMS (`order_manager.py`, `fill_handler.py`)
6. Risk (`risk_manager.py`)
7. Persistence (`state_store.py`)
8. Monitoring (`alerting.py`)
9. Backtest (`grid_backtester.py`, `backtest_report.py`)
10. Entry point (`main.py`) wires all layers

Never start a phase until all its dependencies are complete.

## Key File Locations

| Purpose | Path |
|---------|------|
| Settings class | `config/settings.py` |
| Strategy YAML | `config/grid_config.yaml` |
| Exchange client | `src/exchange/exchange_client.py` |
| Price feed | `src/data/price_feed.py` |
| Regime detector | `src/strategy/regime_detector.py` |
| Grid calculator | `src/strategy/grid_calculator.py` |
| Grid orchestrator | `bot_v2/grid/orchestrator.py` |
| Order manager | `src/oms/order_manager.py` |
| Fill handler | `src/oms/fill_handler.py` |
| Risk manager | `src/risk/risk_manager.py` |
| State store | `src/persistence/state_store.py` |
| Alerting | `src/monitoring/alerting.py` |
| Backtester | `src/backtest/grid_backtester.py` |
| Entry point | `main.py` |
| Phase plans | `plan/feature-grid-bot-phase{N}-1.md` |
