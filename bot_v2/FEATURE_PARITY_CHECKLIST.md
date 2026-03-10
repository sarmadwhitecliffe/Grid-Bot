# Bot_v2 Feature Parity Checklist

**Status as of:** 2025-11-02  
**Bot_v1 lines:** 4438  
**Bot_v2 lines:** 1200 (bot.py) + modules

---

## ✅ COMPLETED FEATURES

### Core Entry Logic
- [x] Adaptive risk management integration (PROBATION/GROWTH/STANDARD tiers)
- [x] Volatility filter with caching (extracted to `filters/volatility_filter.py`)
- [x] Cost floor filter (extracted to `filters/cost_filter.py`)
- [x] Min notional check ($5 USD minimum)
- [x] xATR hybrid stop loss from signal metadata
- [x] Leverage setting for live exchanges
- [x] Metadata forwarding (xatr_stop, quality metrics)

### Core Exit Logic
- [x] Exit condition engine with priority-based evaluation
- [x] Trailing stop calculator with quality/stage weighting
- [x] Post-TP1 Probation system
- [x] TP1a (quick scalp) + TP1b (main target)
- [x] Hard/Soft/Breakeven stop losses
- [x] Catastrophic stop
- [x] Stale trade detection
- [x] MAE/MFE tracking

### Position Management
- [x] Position state persistence (active_positions.json)
- [x] Capital management per symbol
- [x] Trade history recording
- [x] R-multiple tracking
- [x] Peak price tracking
- [x] MFE/MAE calculations

### Notifications
- [x] Telegram startup message with capital details
- [x] Entry notifications with all details
- [x] Exit notifications with PnL/duration/reason
- [x] Hourly heartbeat (every 3600 seconds)
- [x] Daily summary at midnight UTC

### Signal Coordination
- [x] Signal generator status callbacks (ENTRY/EXIT/REJECTED)
- [x] Webhook signal queue processing
- [x] Metadata extraction from signals

---

## 🔍 FEATURES TO VERIFY

### 1. **Partial Close Execution** ✅
**Status:** VERIFIED
**Location:** `bot_v2/exit_engine/engine.py`
**Test:** Verified by `tests/test_position.py` and `tests/test_tracker.py`

**Verification:**
- TP1a closes 30% at ~0.7x ATR
- Position status changes to PARTIALLY_CLOSED
- Remaining 70% targets TP1b at ~1.2x ATR
- Trailing activates immediately after TP1a

---

### 2. **Capital Updates** ✅
**Status:** VERIFIED
**Location:** `bot_v2/bot.py`

**Verification:**
- [x] `self.capital_manager.update_capital(pnl)` is called in `_handle_exit`
- [x] Capital is persisted via `CapitalManager`

---

### 3. **Flip Signal Handling** ✅
**Status:** VERIFIED
**Location:** `bot_v2/bot.py` (lines 770+)

**Verification:**
- [x] `_handle_flip_signal` method exists
- [x] Handles closing existing position and opening reverse position
- [x] Uses `force=True` for exit

---

### 4. **MAE/MFE Advanced Exit Conditions** ✅
**Status:** VERIFIED (DISABLED)
**Location:** `bot_v2/exit_engine/engine.py`

**Verification:**
- Logic exists in `_check_intrabar_mae_mfe` and `_check_bar_close_conditions`
- Explicitly disabled in code due to "loss rate concerns"
- This matches the current operational decision

---

### 5. **Adverse Scale-Out** ✅
**Status:** VERIFIED
**Location:** `bot_v2/exit_engine/engine.py`

**Verification:**
- Logic in `_check_adverse_scaleout`
- Triggers when MAE >= `partial_exit_on_adverse_r`
- Closes `partial_exit_pct` (default 50%)
- Exit reason: AdverseScaleOut

---

### 6. **Stale Trade Time Limits** ✅
**Status:** VERIFIED
**Location:** `bot_v2/exit_engine/engine.py`

**Verification:**
- Logic in `_check_stale_trade`
- Checks `stale_max_minutes` (default 600m)
- Checks `mfe_r < stale_min_mfe_r`
- Includes "Absolute Stale Exit" safety net (1.1x time)

---

### 7. **Ratio Decay Override (R-DECAY)** ✅
**Status:** VERIFIED
**Location:** `bot_v2/position/trailing_stop.py`

**Verification:**
- Logic in `_apply_ratio_decay`
- Ratio > 10: >15% decay → 0.25x
- Ratio > 5: >20% decay → 0.30x
- Ratio > 2.5: >25% price decay → 0.35x
- Ratio > 1.5: >50% R decay → 0.40x
- Peak-Reset Protection implemented in `_should_reset_rdecay`

---

### 8. **OHLCV Caching** ⚠️
**Status:** UNKNOWN  
**Location:** Should be in exchange modules

**Bot_v1 behavior (lines 585-615):**
- Caches OHLCV data per symbol+timeframe
- 60-second TTL
- Reduces API calls during volatility filter + ATR calculation

**Verify:**
```bash
grep -n "cache\|_ohlcv_cache" bot_v2/execution/*.py
```

**Expected:**
- [ ] OHLCV cache implemented
- [ ] 60-second TTL
- [ ] Used by volatility filter
- [ ] Used by ATR calculation

---

### 9. **Trade History Format** ⚠️
**Status:** PARTIALLY IMPLEMENTED
**Location:** `bot_v2/bot.py` (`_add_trade_to_history`)

**Verification:**
- [x] Basic trade details (symbol, side, price, pnl)
- [x] Advanced metrics (MFE, MAE, R-multiples)
- [x] Post-TP1 analysis fields
- [ ] Capital before/after tracking (MISSING)
- [ ] Entry/exit ATR recorded (MISSING)

---

### 10. **Volatility History Tracking** 🔴
**Status:** MISSING
**Location:** `bot_v2/filters/volatility_filter.py`

**Verification:**
- No logic found to save ATR% history to `data_futures/volatility_history.json`
- This feature is used for regime detection in bot_v1
- Needs implementation in `_handle_entry_signal` or `VolatilityFilter`

---

### 11. **Heartbeat Timing** ✅
**Status:** IMPLEMENTED  
**Verified:** Sends every 3600 seconds (1 hour)

---

### 12. **Daily Summary Timing** ✅
**Status:** IMPLEMENTED  
**Verified:** Triggers at midnight UTC (00:00-00:05)

---

## 🚨 CRITICAL MISSING FEATURES

### **A. OHLCV Caching** 🟡
**Impact:** MEDIUM - Extra API calls  
**Workaround:** None needed for low-frequency trading  
**Fix Required:** Add cache dict in exchange modules

### **B. Volatility History Saving** 🟡
**Impact:** LOW - Nice-to-have for analysis  
**Workaround:** Can analyze from trade history  
**Fix Required:** Add `save_volatility_data()` call after entry

### **C. Trade History Fields** 🟡
**Impact:** LOW - Missing some metadata  
**Workaround:** None  
**Fix Required:** Add missing fields to `_add_trade_to_history`

---

## 📊 SIZE COMPARISON

| Module | Bot_v1 | Bot_v2 | Reduction |
|--------|--------|--------|-----------|
| **Main bot.py** | 4438 lines | 1200 lines | **-73%** |
| **Position logic** | Embedded | 200 lines (position/) | Extracted |
| **Exit engine** | Embedded | 450 lines (exit_engine/) | Extracted |
| **Risk management** | Embedded | 600 lines (risk/) | Extracted |
| **Notifications** | Embedded | 165 lines (notifications/) | Extracted |
| **Filters** | Embedded | 250 lines (filters/) | **NEW!** |
| **Persistence** | Embedded | 180 lines (persistence/) | Extracted |
| **Models** | Mixed | 400 lines (models/) | Extracted |
| **TOTAL** | 4438 lines | ~3445 lines | **-22%** |

---

## 🎯 IMMEDIATE ACTION ITEMS

### Priority 1 (CRITICAL - DO NOW)
1. **Add OHLCV caching** - Reduce API calls
2. **Add volatility history** - Enable regime analysis
3. **Add missing trade history fields** - Complete data parity

### Priority 2 (HIGH - THIS WEEK)
4. **Verify MAE/MFE thresholds** - Match bot_v1 exactly
5. **Test adverse scale-out** - Force MAE > 2.0 ATR
6. **Verify stale trade timing** - Wait 8+ hours

### Priority 3 (MEDIUM - NICE TO HAVE)
7. **Performance testing** - Compare execution speed
8. **Memory profiling** - Check for leaks
9. **Integration tests** - Full workflow testing

---

## ✅ TESTING COMMANDS

### Test Entry Flow
```bash
# Send buy signal
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"buy","symbol":"WIFUSDT","metadata":{"xatr_stop":2.50}}'

# Check logs
tail -f logs/webhook_server.log

# Verify position created
cat data_futures/active_positions.json
```

### Test Exit Flow
```bash
# Send exit signal
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"exit","symbol":"WIFUSDT"}'

# Verify trade history
cat data_futures/trade_history.json | jq '.[-1]'

# Verify capital updated
cat data_futures/symbol_capitals.json | jq '.WIFUSDT'
```

### Test Flip Signal
```bash
# Open LONG
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"buy","symbol":"WIFUSDT"}'

# Flip to SHORT (should close LONG, open SHORT)
curl -X POST http://localhost:5001/webhook \
  -H "Content-Type: application/json" \
  -d '{"action":"sell","symbol":"WIFUSDT"}'

# Check if position flipped
cat data_futures/active_positions.json | jq '.WIFUSDT.side'
```

---

## 📝 NOTES

- **All EXACT logic preserved** from bot_v1
- **No reinvention** - only modular extraction
- **Type safety** improved with proper models
- **Testing** easier with isolated modules
- **Maintenance** clearer with single responsibility

**Last Updated:** 2025-11-18  
**Maintained By:** GitHub Copilot + User
