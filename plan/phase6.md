---
goal: Phase 6 — Backtest Validation & Project Completion
version: 1.1
date_created: 2026-02-22
last_updated: 2026-02-22
owner: Antigravity
status: 'In progress'
tags: ['backtest', 'optimization', 'validation', 'completion', 'bug-fix', 'futures', 'long-short']
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

Phase 6 is the final stage of Grid Bot development. It consists of two sequential gates: **(B) Backtest Validation** must achieve ≥80% win rate, ≥1.5 profit factor, and ≤15% max drawdown before proceeding to **(C) Project Completion**, which fixes remaining bugs and delivers production readiness. The market type has been upgraded from **Spot to Futures (USDT-M Perpetual)**, enabling the bot to place both **long and short grid orders** simultaneously — buying below price and selling (short) above price — to profit in both ranging and mildly trending conditions.

## 1. Requirements & Constraints

- **REQ-001**: Backtest win rate must be ≥80% to pass Phase B gate
- **REQ-002**: Backtest profit factor must be ≥1.5 to pass Phase B gate
- **REQ-003**: Maximum drawdown must be ≤15% to pass Phase B gate
- **REQ-004**: Sharpe ratio must be >1.0 (stretch target for risk-adjusted returns)
- **REQ-005**: Phase C execution is blocked until Phase B metrics are achieved
- **REQ-006**: Market type must be **Futures (USDT-M Perpetual)** — bot must place both long orders (buy-limit below price) and short orders (sell-limit above price) simultaneously
- **REQ-007**: Backtest must account for **funding rate costs** paid every 8 hours on open futures positions
- **REQ-008**: Liquidation price must never be breached during backtest simulation; if breached, the run counts as a forced loss and increments drawdown accordingly
- **CON-001**: Walk-forward validation must use 70% in-sample / 30% out-of-sample split
- **CON-002**: Parameter optimizer grid search includes 6 dimensions: grid spacing, ADX threshold, BB width, grid count, recentre trigger, **leverage** (1×–5×)
- **CON-003**: Backtest data must be 6 months of BTC/USDT 1h candles fetched from Binance **Futures** public API (`/fapi/v1/klines`)
- **CON-004**: Margin mode must be **isolated** (not cross) — each grid position uses its own margin budget to cap liquidation risk
- **CON-005**: Default leverage for backtesting must be **3×** unless overridden by optimizer output
- **GUD-001**: Follow Grid Bot's 6-layer architecture (Config → Data → Exchange → Strategy → OMS → Risk)
- **PAT-001**: Backtest runner must use async/await patterns for I/O-heavy data fetching
- **PAT-002**: Parameter optimizer must implement grid-search with walk-forward validation (not in-sample only)

## 2. Implementation Steps

### Implementation Phase 1 — Backtest Gate (Phase B)

- **GOAL-001**: Validate grid strategy meets quantitative performance benchmarks; if targets are missed, invoke parameter optimizer, iterate, and re-run backtest until thresholds are achieved.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `scripts/run_backtest.py`: fetch 6 months BTC/USDT 1h data from Binance **Futures** API (`/fapi/v1/klines`), cache to Parquet, run GridBacktester in futures/dual-side mode, print full BacktestReport summary, exit code 0 if targets met else 1 | | |
| TASK-002 | Create `scripts/optimize_params.py`: grid-search **6 parameters** (GRID_SPACING_PCT, ADX_THRESHOLD, bb_width_threshold, NUM_GRIDS, RECENTRE_TRIGGER, LEVERAGE) with walk-forward 70/30 validation; output sorted results table and write best params to `config/grid_config_optimized.yaml` | | |
| TASK-003 | Update `src/backtest/backtest_report.py`: change `TARGET_WIN_RATE: float = 0.55` to `0.80` | | |
| TASK-004 | Update `config/grid_config.yaml`: set `market_type: futures`, `margin_mode: isolated`, `leverage: 3`, and adopt winning parameters from optimizer TASK-002 | | |
| TASK-005 | Create `plan/backtest-results.md`: record actual win rate, profit factor, drawdown, Sharpe ratio, funding cost impact, and best parameter set from TASK-002 | | |
| TASK-501 | Update `src/backtest/grid_backtester.py`: add futures dual-side grid support — long grid (buy-limit orders below mid-price) + short grid (sell-limit orders above mid-price); apply leverage multiplier to position sizing; deduct funding rate (0.01% per 8h historical average) from PnL at each funding interval | | |
| TASK-502 | Update `src/exchange/exchange_client.py`: switch CCXT market type to `"future"`, set `options["defaultType"] = "future"`, add `set_leverage()` and `set_margin_mode("isolated")` calls on startup; update order placement to use `positionSide="LONG"` / `positionSide="SHORT"` for Binance Futures hedge mode | | |
| TASK-503 | Update `config/grid_config.yaml`: add fields `market_type: futures`, `margin_mode: isolated`, `leverage: 3`, `dual_side: true`, `funding_interval_hours: 8` | | |
| TASK-504 | Verify liquidation safety in backtester: after each simulated fill, compute liquidation price based on isolated margin; if candle low (for longs) or candle high (for shorts) breaches liquidation price, mark position as liquidated and record max loss | | |

### Implementation Phase 2 — Project Completion (Phase C, blocked until Phase B gate passes)

- **GOAL-002**: Fix remaining bugs identified in codebase review, upgrade dependencies, validate full test suite, deliver production-ready bot.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | Fix `main.py` bugs: (1) Line 92: rename `settings.TIMEFRAME` → `settings.OHLCV_TIMEFRAME`; (2) Line 111: fetch account balance, pass `initial_equity` parameter to RiskManager constructor | | |
| TASK-007 | Update `requirements.txt`: add `pyarrow>=14.0.0` dependency (used by TASK-001 Parquet caching) | | |
| TASK-008 | Run full test suite: `pytest tests/ -v` and verify zero failures after TASK-006 fixes | | |
| TASK-009 | Smoke test (optional): set `TESTNET=True` and `MARKET_TYPE=futures` in `.env`, run `python main.py`, allow 60 seconds runtime, verify clean shutdown, confirm both long and short grid orders are placed on the Binance Futures testnet order book | | |

## 3. Alternatives

- **ALT-001**: Manual parameter tuning vs. automated grid-search optimizer — *Chosen*: automated grid-search enables systematic exploration of 6-dimensional parameter space (up to 750 combinations including leverage) in reproducible manner
- **ALT-002**: In-sample-only backtest vs. walk-forward validation — *Chosen*: walk-forward validation (70/30 split) prevents lookahead bias and tests generalization to unseen data
- **ALT-003**: Single-objective optimization (e.g., maximize Sharpe) vs. multi-objective (win rate + profit factor + drawdown) — *Chosen*: multi-objective ensures balanced risk/reward tradeoff; report all three metrics, gate on win rate + profit factor
- **ALT-004**: Spot market vs. Futures market — *Chosen*: **Futures** allows simultaneous long and short grid orders, yielding more filled trades per period and enabling profit in both ranging and mildly directional markets; funding rate costs are accepted as a tradeoff
- **ALT-005**: Cross margin vs. Isolated margin — *Chosen*: **Isolated margin** limits liquidation exposure to each individual grid position, preventing a single adverse move from wiping the entire account
- **ALT-006**: One-way position mode vs. Hedge mode — *Chosen*: **Hedge mode** (Binance Futures dual-side) allows independent long and short positions at different grid levels without them netting against each other

## 4. Dependencies

- **DEP-001**: Binance **Futures** public REST API (`/fapi/v1/klines`) for 6-month BTC/USDT 1h OHLCV history (no API keys required; rate limit ~1200 req/min)
- **DEP-002**: `pyarrow>=14.0.0` library for Parquet caching in backtest runner (new dependency)
- **DEP-003**: `pandas>=1.5.0`, `ta-lib>=0.4.0` already in requirements; used by strategy modules and backtest engine
- **DEP-004**: Phase B completion is a hard blocker for Phase C execution — C1–C4 tasks cannot begin until TASK-001/TASK-002/TASK-005 pass metrics gate
- **DEP-005**: `GridBacktester` class from `src/backtest/grid_backtester.py` must be updated (TASK-501) to support dual-side futures grid before running TASK-001
- **DEP-006**: CCXT `>=4.0.0` required for Binance Futures hedge-mode order placement (`positionSide` parameter support)
- **DEP-007**: Binance Futures testnet credentials (API key + secret) required for TASK-009 smoke test; paper trading keys only, no real funds

## 5. Files

- **FILE-001**: `scripts/run_backtest.py` — New file (TASK-001). Fetches Binance **Futures** data, caches, runs GridBacktester in dual-side mode, prints report, returns exit code.
- **FILE-002**: `scripts/optimize_params.py` — New file (TASK-002). Grid-search optimizer (6 dims incl. leverage) with walk-forward validation; outputs best params.
- **FILE-003**: `src/backtest/backtest_report.py` — Modified (TASK-003). Update `TARGET_WIN_RATE` from 0.55 → 0.80; add funding cost line to report output.
- **FILE-004**: `config/grid_config.yaml` — Modified (TASK-004 + TASK-503). Add `market_type: futures`, `margin_mode: isolated`, `leverage: 3`, `dual_side: true`, `funding_interval_hours: 8`; replace params with optimizer output.
- **FILE-005**: `plan/backtest-results.md` — New file (TASK-005). Document results including funding cost impact and best futures parameter set.
- **FILE-006**: `main.py` — Modified (TASK-006). Fix TIMEFRAME field name and RiskManager signature.
- **FILE-007**: `requirements.txt` — Modified (TASK-007). Add `pyarrow>=14.0.0`.
- **FILE-008**: `tests/` (all test files) — Referenced (TASK-008). No modifications; validation only via `pytest tests/ -v`.
- **FILE-009**: `.env` (or `.env.example`) — Referenced (TASK-009). Set `TESTNET=True` and `MARKET_TYPE=futures` for smoke test.
- **FILE-010**: `src/backtest/grid_backtester.py` — **Modified** (TASK-501). Add dual-side futures grid logic, leverage multiplier, funding rate deduction, and liquidation price check.
- **FILE-011**: `src/exchange/exchange_client.py` — **Modified** (TASK-502). Switch CCXT to futures mode; add `set_leverage()`, `set_margin_mode("isolated")`; use `positionSide` in order calls.

## 6. Testing

- **TEST-001**: Unit backtest engine: `pytest tests/test_backtester.py -v` validates GridBacktester dual-side futures grid construction, long/short order placement, leverage sizing, funding deduction, and fill simulation.
- **TEST-002**: End-to-end backtest runner: `python scripts/run_backtest.py` invokes full pipeline (Futures data fetch → cache → dual-side backtest → metrics report). Success iff win rate ≥80%, profit factor ≥1.5, drawdown ≤15%.
- **TEST-003**: Parameter optimizer validation: `python scripts/optimize_params.py` iterates up to 750 parameter sets (6 dims including leverage 1×–5×) with walk-forward splits; outputs ranked results table with win rate, profit factor, Sharpe, and net funding cost for each set.
- **TEST-004**: Full test suite: `pytest tests/ -v --tb=short` runs all unit tests (test_settings.py, test_exchange_client.py, test_price_feed.py, etc.). Success iff zero failures.
- **TEST-005**: Smoke test (optional): Set `TESTNET=True` and `MARKET_TYPE=futures`, run `python main.py`, allow 60 seconds, verify no unhandled exceptions, confirm both LONG and SHORT grid orders appear on testnet order book, and clean shutdown.
- **TEST-006**: Import sanity check: `python -c "from main import GridBot; print('Import OK')"` confirms bot entry point is syntactically valid.
- **TEST-007**: Liquidation safety check: in backtester unit test, simulate a 10% adverse candle at 3× leverage on an isolated position; verify the backtester correctly detects and records liquidation before allowing further trades.

## 7. Risks & Assumptions

- **RISK-001**: Backtest metrics may not meet 80% win rate target with current config → triggers TASK-002 optimizer loop; optimizer runtime ~60–120 min for up to 750 grid-search combinations (6 dims)
- **RISK-002**: Binance Futures API rate limits during 6-month data fetch (TASK-001) — mitigation: implement retry logic with exponential backoff in price_feed.py (already present per memory.instructions)
- **RISK-003**: Parameter optimizer finds local optimum only, not global optimum in 6D space — mitigation: use walk-forward validation to reduce overfitting risk
- **RISK-004**: Optimization on 6 months historical data may not generalize to live trading — mitigation: walk-forward 70/30 split reduces bias; backtest results are "optimistic bound" only
- **RISK-005**: TASK-006 bugs in main.py cause runtime crashes on live bot startup — mitigation: TASK-009 smoke test validates fixes before production deployment
- **RISK-006**: High leverage (e.g., 5×) combined with wide grid spacing may cause simulated liquidations, inflating drawdown numbers — mitigation: TASK-504 liquidation check flags these runs; optimizer penalises any run with a liquidation event
- **RISK-007**: Funding rate costs (~0.01%/8h) may erode profits on legs that remain open for many days — mitigation: TASK-501 deducts funding costs in backtest; optimizer includes net-of-funding Sharpe in ranking
- **RISK-008**: Binance Futures hedge mode requires API-level enablement; if account is in one-way mode, `positionSide` orders will be rejected — mitigation: TASK-502 calls `change_position_mode(dual=True)` on startup with graceful error handling
- **ASSUMPTION-001**: 6 months of historical BTC/USDT 1h Futures data is representative and sufficient for strategy validation
- **ASSUMPTION-002**: Historical market regime (ranging vs. trending) and volatility will persist in live market (no regime shift assumption)
- **ASSUMPTION-003**: Grid parameters (spacing, ADX threshold, BB width, count, recentre, leverage) are approximately independent and do not exhibit strong interaction effects in backtest
- **ASSUMPTION-004**: Binance Futures public candle data is accurate and complete (no missing bars)
- **ASSUMPTION-005**: Fill assumptions in GridBacktester (limit orders filled at grid price, no slippage model) are reasonable approximations of live Futures fills
- **ASSUMPTION-006**: Average funding rate of 0.01% per 8h is a reasonable long-run estimate for BTC/USDT perpetual; actual funding may deviate significantly during extreme market events

## 8. Related Specifications / Further Reading

- [Phase 5 specification](plan/feature-grid-bot-phase5-1.md) — Backtest engine & report module design (prerequisite, already complete)
- [Backtest results document](plan/backtest-results.md) — Output of Phase B gate evaluation; to be created by TASK-005
- [Grid Bot architecture memory](/.github/instructions/memory.instructions.md) — 6-layer pipeline, async patterns, grid spacing rules
- [Master phase plan](plan/feature-grid-bot-master-1.md) — Roadmap and phase dependencies
- Binance API documentation: https://binance-docs.github.io/apidocs/ (public candle data endpoint)
- CCXT documentation: https://docs.ccxt.com/ (async exchange client, already integrated)
