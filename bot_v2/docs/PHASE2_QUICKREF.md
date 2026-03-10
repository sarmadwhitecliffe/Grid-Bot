# Phase 2 Quick Reference: Second Trade Max Leverage Override

Feature Flag: `enabled` in `config/second_trade_override.feature.json`

## Purpose
Boost leverage on the second trade of a UTC day when the first completed trade meets fast profitable criteria. Single-use, persisted, safe, optionally expirable.

## Config File (`config/second_trade_override.feature.json`)
```json
{
  "enabled": false,
  "scope": "global",                // "global" or "per_symbol"
  "max_time_minutes": 30,            // First trade must close within this many minutes
  "allowed_reasons": [               // Exit reasons that qualify
    "AggressivePeakExit",
    "TrailExit"
  ],
  "max_delay_minutes": 45,           // Optional: qualify → must open second trade within delay
  "require_min_pnl_r_multiple": 0,   // Optional R filter (0 disables)
  "target_leverage": 10,             // Optional: Specific leverage to apply (overrides tier max)
  "rule_version": "1"               // Version tag for payload/debugging
}
```

Leave `enabled=false` for dark launch. Omit optional fields if unused.

## Qualification Criteria
1. First completed trade of the UTC day (per scope).
2. Net PnL (`pnl_usd`) > 0 (after fees/funding).
3. Exit reason ∈ `allowed_reasons`.
4. `time_open_min` ≤ `max_time_minutes`.
5. Optional: `realized_r_multiple` ≥ `require_min_pnl_r_multiple` (if > 0).
6. No prior qualification for same (day, scope).

## Application Path
At position sizing (just before order creation) capital manager calls `apply_second_trade_override()`:
- Reads override (day_key + scope).
- Checks consumed / expired flags.
- If pending and in-scope → attempts atomic consume.
- Sets leverage to `target_leverage` (if configured) OR tier `max_leverage` (clamped) if higher than current.

## Override Payload (State File)
`data_futures/second_trade_override_state.json` example:
```json
{
  "20251201_UTC": {
    "GLOBAL": {
      "qualified_at": "2025-12-01T09:12:27.103428+00:00",
      "reason": "TrailExit",
      "time_open_min": 14.23,
      "pnl_usd": "11.74000000",
      "scope": "global",
      "symbol": null,
      "consumed": false,
      "rule_version": "1"
    }
  }
}
```

## Structured Log Events
| Event | When |
|-------|------|
| `leverage_override_qualified` | First trade qualifies and sets state |
| `leverage_override_consumption_attempt` | Sizing attempts consume |
| `leverage_override_consumption_failed` | Race / already consumed |
| `leverage_override_applied` | Leverage increased (or already at max) |
| `leverage_override_expired_unused` | Delay exceeded (optional expiry) |
| `leverage_override_ignored_flag_disabled` | Feature disabled when checked |

## Rollback / Manual Clear
Use script: `scripts/clear_second_trade_override.py`
```bash
python scripts/clear_second_trade_override.py          # Clear current day
python scripts/clear_second_trade_override.py --all    # Clear all days
python scripts/clear_second_trade_override.py --symbol XRPUSDT  # Clear symbol scope current day
```

## Edge Cases
- Order rejection prior to position creation: override remains pending.
- Tier change between qualification and second trade: applies new tier max.
- Feature disabled after qualification: application logs `ignored_flag_disabled` and does not consume.
- Expiry: if `max_delay_minutes` configured and exceeded → expired, no leverage change.

## Safety Guarantees
- Never exceeds tier max leverage (clamped before use).
- Single-use consumption is atomic (state updated before leverage applied).
- Day boundary reset via UTC date key (`YYYYMMDD_UTC`).

## Test Coverage (Initial)
- Application once + expiry scenarios.
Additional recommended tests: concurrency, per-symbol scope isolation, midnight reset.

## Enabling Procedure (Canary)
1. Keep `enabled=false` (observe qualification logs none).
2. Enable with narrow scope (e.g. `global` + low-volume symbol). Set `max_delay_minutes` only if needed.
3. Monitor logs for qualification accuracy and absence of spurious applies.
4. Expand scope / enable across all symbols.

## Future Extensions
- R-multiple minimum gating (already supported via `require_min_pnl_r_multiple`).
- Volatility adaptive max leverage reductions.
- Multi-qualification batching for session-based models.

---
Document version: 1.0 / Dec 01 2025## Phase 2: Systematic Incremental Updates (Issues 5-10)

### Overview
With critical architectural issues resolved, we now implement systematic incremental improvements. Focus on **logic consistency and validation** (Issues 5-8) before quality-of-life enhancements (Issues 9-10).
# Phase 2: Market Data Cache - Quick Reference

## ⚡ Quick Start

### Enable/Disable Cache
```bash
# Enable (default)
export ENABLE_MARKET_DATA_CACHE=true

# Disable (fallback to Phase 1)
export ENABLE_MARKET_DATA_CACHE=false
```

### Configure TTL
```bash
# Default: 30 seconds
export MARKET_DATA_CACHE_TTL=30

# Faster markets: 15 seconds
export MARKET_DATA_CACHE_TTL=15

# Slower markets: 60 seconds
export MARKET_DATA_CACHE_TTL=60
```

### Restart Bot
```bash
cd /home/user/NonML_Bot
./restart_tmux.sh
```

## 📊 Monitoring

### Watch Logs
```bash
# Live tail with cache stats
tail -f bot_logs/bot.log | grep -E "Cache Stats|Latency Report"

# Filter cache hits
tail -f bot_logs/bot.log | grep "Cache Stats"
```

### Expected Output
```
[INFO] 📦 Shared market data cache initialized (TTL=30s, max_size=500)
[INFO] 📦 Cache Stats - Hit Rate: 87.3%, Hits: 142, Misses: 21, Size: 45/500, Evictions: 3
```

### Check Latency Improvement
```bash
# Before (Phase 1): ~11,000ms
# After (Phase 2): ~500ms
grep "signal completed" bot_logs/bot.log | tail -10
```

## 🔍 Key Metrics

| Metric | Target | Action if Below Target |
|--------|--------|------------------------|
| **Hit Rate** | 85%+ | Wait 5-10 signals for warmup |
| **Cache Size** | 10-50 entries | Normal for 5-10 symbols |
| **Latency** | <500ms | Check network, verify cache enabled |
| **Evictions** | <10/hour | Normal with LRU, increase max_size if high |

## 🛠️ Troubleshooting

### Cache Not Working
```bash
# 1. Verify cache is enabled
grep "cache initialized" bot_logs/bot.log

# 2. Check environment variables
echo $ENABLE_MARKET_DATA_CACHE
echo $MARKET_DATA_CACHE_TTL

# 3. Verify imports
grep "market_data_cache" bot_v2/bot.py
```

### High Cache Misses
- **Cause**: Cold start or high symbol diversity
- **Solution**: Wait 5-10 signals for warmup, increase TTL if markets are slow

### Stale Prices
- **Cause**: TTL too long for volatile markets
- **Solution**: Reduce TTL to 15-20 seconds
- **Verify**: Order fills always use real-time prices (not cached)

### Memory Issues
- **Cause**: Cache size exceeding limits (rare)
- **Solution**: Reduce `max_size` in cache initialization (default: 500)

## 📁 File Locations

| File | Purpose |
|------|---------|
| `bot_v2/execution/market_data_cache.py` | Cache implementation |
| `bot_v2/execution/simulated_exchange.py` | Sim exchange with cache |
| `bot_v2/execution/live_exchange.py` | Live exchange with cache |
| `bot_v2/bot.py` | Shared cache creation |
| `bot_v2/docs/PHASE2_SUMMARY.md` | Complete documentation |
| `bot_v2/docs/PHASE2_QUICKREF.md` | This document |

## 🧪 Testing

### Send Test Signal
```bash
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"action": "buy", "symbol": "BTCUSDT"}'
```

### Check Response Time
```bash
# Watch logs for latency report
tail -f bot_logs/bot.log | grep "Latency Report"
```

### Verify Cache Hit
```bash
# Second signal should be faster (cache hit)
curl -X POST http://localhost:5000/webhook \
  -H "Content-Type: application/json" \
  -d '{"action": "buy", "symbol": "BTCUSDT"}'
```

## 🔄 Rollback

### Disable Cache (No Code Change)
```bash
export ENABLE_MARKET_DATA_CACHE=false
./restart_tmux.sh
```

### Revert to Phase 1 (Git)
```bash
git checkout HEAD~1
./restart_tmux.sh
```

## 📞 Support

### Debug Commands
```bash
# Check bot status
tmux ls
tmux attach -t trading

# Verify exchanges initialized with cache
grep "initialized.*cache" bot_logs/bot.log

# Check recent cache stats
grep "Cache Stats" bot_logs/bot.log | tail -5
```

### Common Issues

**Issue**: "Cache not initialized"
- **Fix**: Verify `ENABLE_MARKET_DATA_CACHE=true` in environment

**Issue**: "Hit rate is 0%"
- **Fix**: Wait for 5-10 signals, cache needs warmup period

**Issue**: "Latency still high (~10s)"
- **Fix**: Verify cache is enabled, check logs for errors

## 🎯 Expected Results

### Phase 1 → Phase 2 Comparison

| Metric | Phase 1 (Baseline) | Phase 2 (Cached) | Improvement |
|--------|-------------------|------------------|-------------|
| **Price Fetch** | 8,449ms | ~10ms | 99.9% |
| **OHLCV Fetch** | 1,266ms | ~50ms | 96.0% |
| **Total Latency** | 11,363ms | ~500ms | 95.6% |
| **API Calls** | Every signal | ~15% of signals | 85% reduction |
| **Cache Hit Rate** | N/A | 85%+ | N/A |

### Timeline
- **Minute 1**: Cache warming up (hit rate: 20-40%)
- **Minutes 5-10**: Cache stabilized (hit rate: 85%+)
- **Hour 1+**: Optimal performance

## 📚 Resources

- **Phase 1 Report**: `bot_v2/docs/PHASE1_SUMMARY.md`
- **Phase 2 Details**: `bot_v2/docs/PHASE2_SUMMARY.md`
- **Code**: `bot_v2/execution/market_data_cache.py`
- **Tests**: `bot_v2/tests/test_latency_tracking.py`

---
**Quick Reference Version: 1.0**  
**Last Updated: 2024-11-18**

# Performance & Concurrency Configuration

New environment variables introduced to tune bot performance, concurrency, and exchange API usage.

## Concurrency Limits
| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_SIGNAL_CONCURRENCY` | `5` | Maximum number of signals processed concurrently. Prevents event loop saturation during high-load. |

## Exchange Optimization
| Variable | Default | Description |
|----------|---------|-------------|
| `VERIFY_ORDER_IMMEDIATELY` | `true` | If `false`, skips the immediate `fetch_order` call after placement. Relies on periodic reconciliation. Reduces latency. |
| `FETCH_TRADES_FOR_FEES` | `true` | If `false`, skips fetching trades to calculate exact fees immediately. Uses estimated fees. Reduces API calls. |

## Caching & Deduplication
| Variable | Default | Description |
|----------|---------|-------------|
| `PRICE_TTL_SECONDS` | `2.0` | TTL for ticker price cache. Higher values reduce API calls but increase staleness risk. |
| `OHLCV_TTL_SECONDS` | `2.0` | TTL for OHLCV data cache. |
| `DEDUP_WINDOW_SECONDS` | `0.0` | Window to ignore duplicate signals for the same symbol/action. `0` disables deduplication. |

## Observability
- **Performance Logging**: When `LOG_PERF_DETAILS` is `false` (default), the bot now logs a summary `[PERF]` line for each processed signal, including:
  - `price_ms`: Time spent fetching price.
  - `ohlcv_ms`: Time spent fetching candles.
  - `order_ms`: Time spent placing orders.
  - `total_ms`: Total processing time.
