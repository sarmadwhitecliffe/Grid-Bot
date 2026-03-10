---
goal: Optimize bot_v2 performance and resource usage
version: 1.0
date_created: 2025-11-17
last_updated: 2025-12-03
status: Completed
tags: [optimization, performance, refactor]
---

# bot_v2 Optimization Plan

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Scope: Performance and resource-use improvements focused exclusively on the `bot_v2` runtime (no changes to signal generator code). This plan is behavior-preserving and staged to minimize risk. Each item lists exact modules/functions to touch, expected impact, complexity, acceptance criteria, and rollout notes.

## Objectives

- Reduce CPU and I/O in the main trading loop without altering exit behavior.
- Minimize network calls to the exchange and disk writes.
- Keep logging useful while lowering formatting overhead in hot paths.
- Preserve precision and safety guarantees (Decimal usage) while avoiding redundant work.

## Baseline hotspots (bot_v2 only)

- `bot_v2/bot.py`
  - `_monitor_positions`: sequential per-position I/O and computations every loop.
  - `_get_current_atr`: fetches OHLCV + computes ATR each pass.
  - Notifications and status sends potentially chatty under many positions.
- `bot_v2/exit_engine/engine_v1.py`
  - Repeated Decimal conversions and timestamp retrievals per evaluation; verbose logging paths.
- `bot_v2/models/position_v1.py` and `bot_v2/models/position.py`
  - Frequent datetime and Decimal operations in trailing/ratio logic; debug logging in hot path.
- `bot_v2/persistence/state_manager.py`
  - JSON I/O in the loop (positions/history) can be batched.
- `bot_v2/execution/*` and `order_manager.py`
  - Leverage setting, price fetching, and order calls should avoid redundant requests.

## Phase 1 – Low-risk, high-ROI (recommended first)

1) ATR caching per symbol/timeframe (bar-aware)
- Files/funcs: `bot_v2/bot.py` (`_get_current_atr`, `_monitor_positions`)
- What: Cache ATR keyed by (symbol, timeframe, last_bar_ts). Only recompute on new bar; otherwise reuse last ATR.
- How: Maintain in-memory dict { (symbol,timeframe): {"last_bar_ts": int, "atr": Decimal} } updated when OHLCV last timestamp changes.
- Impact: Large reduction in HTTP/CPU per loop.
- Complexity: Low.
- Acceptance: For an idle loop with N open positions, ATR recomputation count per minute drops to ~0 until a new bar; all tests pass.

2) Parallelize per-symbol data fetch
- Files/funcs: `bot_v2/bot.py` (`_monitor_positions`, `_get_current_price`, `_get_current_atr`)
- What: Use `asyncio.gather` to fetch current prices (and ATR only when cache expired) concurrently for all positions in the loop.
- Impact: Lower end-to-end loop latency with many symbols; better CPU idle time.
- Complexity: Low/Medium.
- Acceptance: Per-loop wall time scales sublinearly with number of positions; parity tests pass.

3) Logging guards and lazy formatting in hot paths
- Files/funcs:
  - `bot_v2/exit_engine/engine_v1.py` (all logger.debug/info in `_check_trailing_stop`, `_is_minimum_hold_time_met`, etc.)
  - `bot_v2/models/position_v1.py` (`update_trailing_stop`, `get_quality_adjusted_multiplier`, etc.)
  - `bot_v2/bot.py` (main loop info logs)
- What: Wrap with `if logger.isEnabledFor(logging.DEBUG): ...` and use parameterized logging (`logger.debug("ratio=%s", ratio)`) to avoid f-string work when disabled.
- Impact: CPU reduction when log level > DEBUG; smaller log volume.
- Complexity: Low.
- Acceptance: No string formatting performed when level disabled; logs still appear when enabled.

4) Cache strategy constants and avoid redundant Decimal conversions
- Files/funcs: `bot_v2/exit_engine/engine_v1.py`
- What: StrategyConfig already stores Decimals. Remove `_to_decimal` use for those fields or fast-path when `isinstance(value, Decimal)`.
- Impact: Small-to-moderate CPU savings across many exit checks.
- Complexity: Low.
- Acceptance: No change to values; unit tests for exit engine remain green.

5) Reuse current time within a loop tick
- Files/funcs: `engine_v1.py` + `position_v1.py`
- What: Pass a cached `now`/`now_ms` into helpers instead of repeated `datetime.now()`/`time.time()` calls.
- Impact: Small CPU savings; consistent timing.
- Complexity: Low.
- Acceptance: Timestamps used within a single evaluation are consistent; tests pass.

6) Adaptive loop sleep when idle
- Files/funcs: `bot_v2/bot.py` (main loop)
- What: Slightly increase `await asyncio.sleep()` when there are zero positions and signal queue is empty (e.g., 1.0s → 1.5–2.0s), configurable.
- Impact: CPU reduction at idle; no behavior change.
- Complexity: Low.
- Acceptance: No missed signals; configurable via env.

7) Batch and coalesce external notifications
- Files/funcs: `bot_v2/bot.py` (`_send_status_to_generator`, heartbeats, Telegram notifications)
- What: Debounce/suppress duplicate status sends within a short window (e.g., 2–5s) and send in parallel to multiple endpoints using existing `gather`.
- Impact: Fewer network calls under bursty events.
- Complexity: Low/Medium.
- Acceptance: Same final messages observed; fewer calls on bursts.

## Phase 2 – Medium-risk, measurable gains

8) Price caching with sub-second TTL
- Files/funcs: `bot_v2/bot.py` (`_get_current_price`)
- What: Cache last price per symbol with a tiny TTL (e.g., 300–500ms) to avoid duplicate requests when the loop calls price multiple times.
- Impact: Reduced HTTP traffic under tight loops.
- Complexity: Low/Medium.
- Acceptance: ≤1 price request per symbol per TTL window during continuous loops.

9) Reuse ExitConditionEngine or extract pure helpers
- Files/funcs: `bot_v2/exit_engine/engine_v1.py`; `bot_v2/bot.py` (construction site)
- What: Keep engine stateless but pre-bind invariant fields (strategy constants) or reuse an engine instance per symbol with update method. Ensure no hidden state bugs.
- Impact: Moderate CPU reduction.
- Complexity: Medium (requires careful review to keep semantics).
- Acceptance: Parity tests confirm identical exit decisions across the suite.

10) Reduce frequency of state persistence and history writes
- Files/funcs: `bot_v2/bot.py` (`_persist_state`, `_add_trade_to_history`), `persistence/state_manager.py`
- What: `_persist_state` already saves on position count changes; extend to save at a minimum interval or when positions actually changed content (hash/diff). Buffer history appends and flush in batches or on shutdown.
- Impact: Lower disk I/O.
- Complexity: Medium.
- Acceptance: No lost data under normal shutdown; crash-safe with periodic flush.

11) Avoid redundant leverage settings
- Files/funcs: `bot_v2/bot.py` (`_handle_entry_signal`), `execution/live_exchange.py`
- What: Set leverage only if different from current; maintain a small in-memory cache of last set leverage per symbol.
- Impact: Fewer exchange calls on entries.
- Complexity: Low/Medium.
- Acceptance: No failures in live/paper; unit tests updated to account for condition.

12) Minimize precision work until order submission
- Files/funcs: `engine_v1._check_tp1`
- What: Defer `quantize(1e-8)` until order creation; keep high-precision Decimals internally.
- Impact: Small CPU savings in hot path.
- Complexity: Low.
- Acceptance: Order payloads still meet exchange precision rules.

## Phase 3 – Advanced/optional

13) Batch price fetch if exchange supports it
- Files/funcs: `execution/exchange_interface.py`, `execution/live_exchange.py`, `execution/simulated_exchange.py`, `bot.py`
- What: Add optional `get_market_prices([symbols])` and use it in `_monitor_positions` to fetch all prices at once.
- Impact: Large latency/HTTP reduction when supported.
- Complexity: Medium/High (API dependent).
- Acceptance: Fallback to per-symbol if batch unsupported; parity maintained.

14) Structured performance telemetry
- Files/funcs: `bot_v2/bot.py`
- What: Track loop duration, per-symbol monitor timings, HTTP calls/min, and cache hit rates. Emit periodic summary logs at INFO.
- Impact: Better visibility; targeted tuning.
- Complexity: Low.
- Acceptance: Telemetry behind a feature flag; negligible overhead when disabled.

## Rollout & risk management

- Feature flags: Gate new caches and batching with env vars (e.g., `BOTV2_ATR_CACHE=1`, `BOTV2_PRICE_TTL_MS=300`, `BOTV2_PARALLEL_FETCH=1`).
- Canary: Enable per-symbol or subset of symbols first.
- Backout: Single env flip returns to previous behavior.
- Tests: Run the existing `bot_v2/tests/*` plus your parity tests (`tests/test_aggressive_peak_exit_fix.py`, `test_exit_engine.py`, and integration tests) after each phase.

## KPIs and acceptance criteria

- Loop latency (p50/p95) with N positions before vs after.
- Exchange requests/min (prices, OHLCV) decreases significantly.
- CPU usage drops at idle and under load.
- No change in exit decision traces across the parity suite.

## Work items by file (checklist)

- bot_v2/bot.py
  - [x] Add ATR cache (per symbol/timeframe) and use bar timestamp to invalidate.
  - [x] Parallelize data fetch in `_monitor_positions` via `asyncio.gather`.
  - [x] Add price TTL cache (configurable) in `_get_current_price`.
  - [x] Debounce `_send_status_to_generator`; batch notifications.
  - [x] Adaptive idle sleep with env flag.
  - [x] Optional: batch `get_market_prices` support.
- bot_v2/exit_engine/engine_v1.py
  - [x] Replace `_to_decimal` calls for StrategyConfig fields with direct usage; guard logs.
  - [x] Accept optional `now` timestamp to reuse across checks; avoid repeated `datetime.now`.
  - [x] Defer quantize until order layer.
- bot_v2/models/position_v1.py
  - [x] Guard debug logs in `update_trailing_stop` and helpers.
  - [x] Accept injected `now` where feasible.
- bot_v2/persistence/state_manager.py
  - [x] Batch history writes; maintain periodic flush.
  - [x] Keep positions saves minimal (already count-based); extend with content-change detection (hash/diff) if needed.
- bot_v2/execution/live_exchange.py
  - [x] Optional: add `get_market_prices` and leverage-set caching.

## Non-goals

- No changes to `signal_generator/*` code.
- No changes to backup/legacy modules outside `bot_v2/*`.

## Validation plan

1) Functional parity:
- Run full `bot_v2/tests` and your custom parity suite on exits (TP1a, TP1b, APE, trailing, SLs, stale, adverse scale-out).
- Confirm identical exit condition sequences on deterministic simulations.

2) Performance checks:
- Measure loop duration and requests/min before/after enabling ATR cache and parallel fetch.
- Verify no increase in error rates or missed heartbeats.

3) Observability:
- Confirm telemetry logs show cache hit rates, and logs are quiet at INFO (DEBUG-only verbosity guarded).

---

This plan can be implemented incrementally. Recommended first PRs: (1) ATR cache + parallel fetch; (2) logging guards; (3) price TTL cache and debounced notifications. Each is self-contained and easy to roll back.
