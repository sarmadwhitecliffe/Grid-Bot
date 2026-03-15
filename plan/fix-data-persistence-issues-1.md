---
goal: Fix Grid Bot Data Persistence Issues - Capital State Not Persisting and Session PnL Not Banked
version: 1.0
date_created: 2025-03-16
last_updated: 2025-03-16
owner: Grid Bot Team
status: 'In progress'
tags: ['bug', 'persistence', 'capital', 'grid-trading', 'session-pnl']
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan addresses critical data persistence issues discovered during log analysis where:
1. **Capital state not persisting**: `symbol_capitals.json` shows stale state ($100.00) compared to log output ($100.60 for BCH/USDT)
2. **Session PnL not banked**: `session_realized_pnl_quote` in `grid_exposure.json` shows unrealized PnL that was never added to capital
3. **Performance metrics incomplete**: `symbol_performance.json` only has entries for 2 symbols despite 9 symbols trading

## Root Cause Analysis

### Issue 1: Capital State Not Persisting to Disk

**Symptoms:**
- Log shows: `BCH/USDT capital: $100.55 → $100.60` at 2026-03-15 09:18:22
- File shows: `"capital": "100.1277801421957..."` for BCH/USDT
- File shows: `"tier_entry_time": "2026-03-14T18:17:46"` for ALL symbols

**Root Cause:**
The `CapitalManager._save()` method (line 137-162 in `capital_manager.py`) writes to `symbol_capitals.json` atomically but:
1. Logs at DEBUG level only (not visible in normal logs)
2. No verification that write succeeded
3. State files may have been copied/synced before final saves completed

**Evidence from code analysis:**
```python
# capital_manager.py:160
logger.debug(f"Saved capitals to {self.capitals_file} (atomic)")  # DEBUG level - not logged!
```

### Issue 2: Session PnL Never Banked to Capital

**Symptoms:**
- `grid_exposure.json` shows: `session_realized_pnl_quote: "-9.998"` for BTC/USDT
- `symbol_capitals.json` shows: `capital: "100.0"` for BTC/USDT (should be $90.002)

**Root Cause:**
The `session_realized_pnl_quote` accumulator tracks running PnL but:
1. Only reset on `_bank_and_reinvest()` (line 610 in `orchestrator.py`)
2. Never transferred to `CapitalManager.update_capital()` when session stops
3. Only closed grid trades update capital via `_on_grid_trade_closed()` callback

**Code flow:**
```
orchestrator.py:1007-1012  → session_realized_pnl_quote accumulates fill PnL
orchestrator.py:610        → session_realized_pnl_quote = 0 (reset on bank/reinvest)
bot.py:2980                → capital updates ONLY via _on_grid_trade_closed()
```

**The problem:** When a grid stops for regime shift, max DD, or other reasons with unmatched inventory, the accumulated `session_realized_pnl_quote` is lost when reset.

### Issue 3: Performance Metrics Incomplete

**Symptoms:**
- `symbol_performance.json` only has BCH/USDT (22 trades) and ADA/USDT (8 trades)
- Missing 7 other symbols that had trading activity

**Root Cause:**
`_update_performance_metrics()` (bot.py:4065-4128) is only called after `_on_grid_trade_closed()` (line 2981), which only fires for completed closed trades. Symbols with unmatched inventory at session stop never get performance metrics recorded.

## 1. Requirements & Constraints

- **REQ-001**: All capital changes MUST be persisted to disk immediately after update
- **REQ-002**: Session PnL loss MUST be prevented when grids stop with unmatched inventory
- **REQ-003**: Performance metrics MUST be calculated for all active symbols, not just closed trades
- **REQ-004**: Capital save operations MUST log at INFO level for debugging
- **REQ-005**: Grid session PnL MUST be banked to capital when session ends
- **SEC-001**: Atomic file writes must preserve file integrity on crash
- **CON-001**: Must work with both simulation and live exchange modes
- **CON-002**: Must handle concurrent access from async code
- **CON-003**: Must not break existing state recovery on restart
- **GUD-001**: Use existing atomic write pattern (temp file, fsync, replace)

## 2. Implementation Steps

### Implementation Phase 1: Fix Capital Persistence Logging

- GOAL-001: Add INFO-level logging to capital saves for debugging

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Modify `capital_manager.py:_save()` to log at INFO level instead of DEBUG | ✅ | 2025-03-16 |
| TASK-002 | Add success/failure logging in `_save()` method | ✅ | 2025-03-16 |
| TASK-003 | Add periodic capital state summary logging during persist | ✅ | 2025-03-16 |

### Implementation Phase 2: Bank Session PnL on Grid Stop

- GOAL-002: Ensure accumulated session PnL is banked to capital when grid session stops

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Add `session_realized_pnl_quote` banking in `orchestrator.py:stop()` method | ✅ | 2025-03-16 |
| TASK-005 | Create `_bank_session_pnl()` method in orchestrator to handle PnL banking | ✅ | 2025-03-16 |
| TASK-006 | Update `_bank_and_reinvest()` to call `_bank_session_pnl()` before reset | ✅ | 2025-03-16 |
| TASK-007 | Add callback in stop() to call on_session_end callback with PnL data | ✅ | 2025-03-16 |

**Code changes for orchestrator.py:**
```python
# In stop() method, before clearing orders:
async def stop(self, reason: str = "Manual Stop"):
    """Gracefully stop the grid and cancel orders."""
    logger.info(f"[{self.symbol}] Stopping Grid session: {reason}")
    
    # Bank any remaining session PnL before stopping
    if self.session_realized_pnl_quote != 0 and self.on_session_pnl_bank:
        try:
            await self.on_session_pnl_bank(self.symbol, self.session_realized_pnl_quote)
        except Exception as e:
            logger.error(f"[{self.symbol}] Failed to bank session PnL: {e}")
    
    # ... rest of stop() implementation
```

### Implementation Phase 3: Add Session PnL Banking Callback

- GOAL-003: Implement the callback mechanism for session PnL banking in bot.py

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Add `on_session_pnl_bank` callback parameter to GridOrchestrator | ✅ | 2025-03-16 |
| TASK-009 | Implement `_on_session_pnl_bank()` in bot.py to update capital | ✅ | 2025-03-16 |
| TASK-010 | Update capital manager with session PnL amount | ✅ | 2025-03-16 |
| TASK-011 | Log session PnL banking at INFO level with symbol and amount | ✅ | 2025-03-16 |

### Implementation Phase 4: Add Final State Persistence on Shutdown

- GOAL-004: Ensure all state is persisted during graceful shutdown

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-012 | Add explicit capital persistence in `bot.py:shutdown()` | ✅ | 2025-03-16 |
| TASK-013 | Add performance metrics persistence in shutdown | ✅ | 2025-03-16 |
| TASK-014 | Add grid exposure final snapshot before shutdown | ✅ | 2025-03-16 |
| TASK-015 | Add final state logging showing what was persisted | ✅ | 2025-03-16 |

### Implementation Phase 5: Fix Performance Metrics for All Symbols

- GOAL-005: Calculate performance metrics for all trading symbols, not just closed trades

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-016 | Add periodic performance metrics update for all active symbols | ✅ | 2025-03-16 |
| TASK-017 | Update performance on grid fill events, not just closed trades | ✅ | 2025-03-16 |
| TASK-018 | Calculate unrealized PnL for open positions in metrics | ✅ | 2025-03-16 |

## 3. Alternatives

- **ALT-001**: Store session PnL separately and reconcile on restart - Rejected: Adds complexity and risk of state mismatch
- **ALT-002**: Only bank session PnL on grid re-deploy - Rejected: Would lose PnL on crashed/stopped sessions
- **ALT-003**: Periodically persist capital changes to WAL - Rejected: Current atomic writes are sufficient; the issue is visibility and session PnL banking
- **ALT-004**: Use database instead of JSON files - Rejected: Out of scope, would require significant refactoring

## 4. Dependencies

- **DEP-001**: `bot_v2/risk/capital_manager.py` - Capital persistence
- **DEP-002**: `bot_v2/grid/orchestrator.py` - Grid session management
- **DEP-003**: `bot_v2/bot.py` - Main bot orchestration and callbacks
- **DEP-004**: `bot_v2/persistence/state_manager.py` - State persistence layer

## 5. Files

| File | Changes Required |
|------|------------------|
| `bot_v2/risk/capital_manager.py` | Add INFO logging in `_save()`, add `update_capital_batch()` for bulk updates |
| `bot_v2/grid/orchestrator.py` | Add `_bank_session_pnl()`, modify `stop()`, add session PnL banking callback |
| `bot_v2/bot.py` | Add `_on_session_pnl_bank()` callback, modify `shutdown()`, add periodic performance updates |
| `bot_v2/persistence/state_manager.py` | Add explicit capital snapshot method |

## 6. Testing

- **TEST-001**: Verify capital is persisted after each `_save()` call (check file exists and content matches)
- **TEST-002**: Verify session PnL is banked when grid stops for regime shift
- **TEST-003**: Verify session PnL is banked when grid stops for max DD
- **TEST-004**: Verify session PnL is banked when grid stops for manual stop
- **TEST-005**: Verify capital remains $100 if no trades occurred
- **TEST-006**: Verify capital increases/decreases correctly after profitable/unprofitable session
- **TEST-007**: Verify performance metrics are calculated for symbols with unmatched inventory
- **TEST-008**: Verify graceful shutdown persists all state (check files after simulated SIGTERM)
- **TEST-009**: Verify restart recalculates state from persisted files correctly
- **TEST-010**: Integration test: Run grid for 1 hour, stop, verify all data persisted

## 7. Risks & Assumptions

- **RISK-001**: Concurrent saves during banking could cause race conditions - Mitigation: Use existing async lock in CapitalManager
- **RISK-002**: Banking PnL twice on restart could duplicate profits - Mitigation: Track banking state in session, clear on bank
- **RISK-003**: Performance impact of logging at INFO level for every capital save - Acceptable: Capital updates are infrequent
- **ASSUMPTION-001**: The `_save()` atomic write pattern works correctly (proven by existing implementation)
- **ASSUMPTION-002**: `session_realized_pnl_quote` accurately tracks session PnL (verified in code analysis)
- **ASSUMPTION-003**: Grid stops are always called through `stop()` method (architecture review confirms)

## 8. Related Specifications / Further Reading

- [Capital Manager Implementation](../bot_v2/risk/capital_manager.py)
- [Grid Orchestrator Session Management](../bot_v2/grid/orchestrator.py:569-638)
- [Bot Shutdown Flow](../bot_v2/bot.py:1052-1093)
- [State Persistence Flow](../bot_v2/persistence/state_manager.py:585-657)