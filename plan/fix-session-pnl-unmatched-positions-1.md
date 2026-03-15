---
goal: Fix Session PnL Banking with Unmatched Open Positions
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: Grid Bot Team
status: 'Completed'
tags: ['bug', 'persistence', 'grid-trading', 'session-pnl', 'open-positions']
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

This plan fixes a critical bug where session PnL is banked to capital even when there are unmatched open positions (LONG or SHORT lots) that haven't been closed. This creates "phantom profits" that inflate capital incorrectly.

## Implementation Notes

**Status**: COMPLETED

**Implementation**: 
- `bot_v2/grid/orchestrator.py` has `_bank_session_pnl()` which checks `_has_unmatched_positions()` before banking
- Session PnL is deferred if open positions exist

## Problem Description

When a grid stops due to Session TP, Max DD, or Cooldown with unmatched positions:

1. **SELL fills** → opens SHORT position → `session_realized_pnl_quote += sell_notional`
2. Counter BUY order placed but not filled
3. Grid stops → counter orders cancelled
4. `_bank_session_pnl()` banks `session_realized_pnl_quote` as profit
5. Grid resets → `_open_short_lots.clear()` → position info lost
6. **Capital increased but SHORT liability is forgotten**

### Evidence from Analysis

| Symbol | Sell Qty | Buy Qty | Net Position | Session PnL | Problem |
|--------|-----------|---------|--------------|-------------|---------|
| OP/USDT | 209.43 | 0 | SHORT 209.43 | +$26.65 banked | Owes asset but kept "profit" |
| NEAR/USDT | 4.93 | 0 | SHORT 4.93 | +$6.64 banked | Owes asset but kept "profit" |
| APT/USDT | 11.87 | 0 | SHORT 11.87 | +$11.00 banked | Owes asset but kept "profit" |

Symmetrically, if there are unmatched LONG lots (BUY without SELL), the session PnL is understated (we own the asset but it looks like we lost money).

## 1. Requirements & Constraints

- **REQ-001**: Session PnL must NOT be banked if there are unmatched open positions
- **REQ-002**: Grid must track open position state before banking
- **REQ-003**: Banking must be deferred until positions are closed or grid redeploys cleanly
- **REQ-004**: Must preserve capital accuracy across grid restarts
- **CON-001**: Must work with both simulation and live exchanges
- **CON-002**: Must not break existing closed trade PnL tracking
- **CON-003**: Must handle both LONG and SHORT open positions
- **GUD-001**: Use existing `_open_long_lots` and `_open_short_lots` tracking

## 2. Implementation Steps

### Implementation Phase 1: Add Position Check Before Banking

- GOAL-001: Prevent banking session PnL when there are unmatched open positions

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `_has_unmatched_positions()` helper method to check for open lots | ✅ | 2025-03-16 |
| TASK-002 | Modify `_bank_session_pnl()` to check positions before banking | ✅ | 2025-03-16 |
| TASK-003 | Log warning when banking is deferred due to unmatched positions | ✅ | 2025-03-16 |
| TASK-004 | Store unmatched PnL for later reconciliation | ✅ | 2025-03-16 |

**Code changes for orchestrator.py:**

```python
def _has_unmatched_positions(self) -> bool:
    """Check if there are unmatched LONG or SHORT positions."""
    return len(self._open_long_lots) > 0 or len(self._open_short_lots) > 0

async def _bank_session_pnl(self, reason: str = "Session End"):
    """Bank the accumulated session PnL to capital, only if positions are closed."""
    if self.session_realized_pnl_quote == Decimal("0"):
        logger.debug(f"[{self.symbol}] No session PnL to bank (0)")
        return

    # Check for unmatched positions - don't bank if positions are still open
    if self._has_unmatched_positions():
        open_long_value = sum(lot["amount"] * lot["entry_price"] for lot in self._open_long_lots)
        open_short_value = sum(lot["amount"] * lot["entry_price"] for lot in self._open_short_lots)
        logger.warning(
            f"[{self.symbol}] Cannot bank session PnL ${self.session_realized_pnl_quote:.2f} - "
            f"unmatched positions detected: LONG=${open_long_value:.2f}, SHORT=${open_short_value:.2f}. "
            f"Deferring until positions close."
        )
        return

    pnl_to_bank = self.session_realized_pnl_quote
    # ... rest of banking logic
```

### Implementation Phase 2: Bank on Position Close

- GOAL-002: Ensure deferred PnL is banked when positions eventually close

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Track deferred PnL in session state | ✅ | 2025-03-16 |
| TASK-006 | Check deferred PnL in `_pair_fill_into_closed_trades()` | ✅ | 2025-03-16 |
| TASK-007 | Bank deferred PnL when last open lot is closed | ✅ | 2025-03-16 |

**Code changes for orchestrator.py in `_pair_fill_into_closed_trades()`:**

```python
def _pair_fill_into_closed_trades(self, fill_price, amount, side, filled_order_id):
    # ... existing pairing logic ...
    
    closed_trades = []  # from existing logic
    
    # After pairing, check if all positions are now closed and bank deferred PnL
    if not self._has_unmatched_positions() and self.session_realized_pnl_quote != 0:
        # All positions closed, safe to bank
        # This will be handled by the callback in next tick
        pass
    
    return closed_trades
```

### Implementation Phase 3: Handle Grid Stop with Open Positions

- GOAL-003: Gracefully handle grid stop when positions remain open

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Log detailed position state when grid stops with open positions | ✅ | 2025-03-16 |
| TASK-009 | Persist open lots state in grid_states.json for recovery | ✅ | 2025-03-16 |
| TASK-010 | On grid restart, restore open lots and reconcile PnL | ✅ | 2025-03-16 |

### Implementation Phase 4: Update grid_exposure.json Tracking

- GOAL-004: Track unmatched position value in grid_exposure for visibility

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Add `open_long_value` to grid_exposure.json | ✅ | 2025-03-16 |
| TASK-012 | Add `open_short_value` to grid_exposure.json | ✅ | 2025-03-16 |
| TASK-013 | Add `unmatched_pnl_impact` to show potential PnL adjustment | ✅ | 2025-03-16 |

**Updated grid_exposure.json format:**

```json
{
  "OP/USDT": {
    "session_buy_qty": "0",
    "session_sell_qty": "209.43",
    "session_realized_pnl_quote": "26.65",
    "open_long_value": "0",
    "open_short_value": "26.65",
    "unmatched_pnl_impact": "-26.65",
    "has_unmatched_positions": true
  }
}
```

## 3. Alternatives

- **ALT-001**: Adjust PnL by unrealized position value - Rejected: More complex, requires price estimation
- **ALT-002**: Force close positions on stop - Rejected: May cause slippage and partial fills
- **ALT-003**: Never bank session PnL, only closed trade PnL - Rejected: Would lose legitimate session profits

## 4. Dependencies

- **DEP-001**: `bot_v2/grid/orchestrator.py` - Grid session management
- **DEP-002**: `bot_v2/models/grid_state.py` - Grid state persistence (if adding new fields)
- **DEP-003**: Previous fix `fix-data-persistence-issues-1.md` - Session PnL banking must exist first

## 5. Files

| File | Changes Required |
|------|------------------|
| `bot_v2/grid/orchestrator.py` | Add position check in `_bank_session_pnl()`, add `_has_unmatched_positions()` |
| `bot_v2/bot.py` | Update `_persist_state()` to include open position values |
| `bot_v2/models/grid_state.py` | Add optional fields for open position tracking |

## 6. Testing

- **TEST-001**: Verify banking is blocked when open_long_lots has entries
- **TEST-002**: Verify banking is blocked when open_short_lots has entries
- **TEST-003**: Verify banking proceeds when no open lots exist
- **TEST-004**: Verify deferred PnL is banked after positions close
- **TEST-005**: Verify grid restart reconciles open positions correctly
- **TEST-006**: Test with simulation: grid stops mid-position, verify PnL not banked
- **TEST-007**: Test: multiple SELL fills without BUY, verify SHORT position detected
- **TEST-008**: Test: multiple BUY fills without SELL, verify LONG position detected
- **TEST-009**: Verify grid_exposure.json includes unmatched position data

## 7. Risks & Assumptions

- **RISK-001**: Session restart may have stale state - Mitigation: Clear lots on redeploy
- **RISK-002**: Live exchange may have different fill timing - Mitigation: Works with any exchange
- **ASSUMPTION-001**: `_open_long_lots` and `_open_short_lots` accurately track positions
- **ASSUMPTION-002**: Counter-orders may be partially filled - Accepted: Partial fills handled by pairing logic
- **ASSUMPTION-003**: Grid grid spacing ensures counter-orders eventually fill - Not guaranteed in trending markets

## 8. Related Specifications / Further Reading

- [Previous Fix: Data Persistence Issues](./fix-data-persistence-issues-1.md)
- [Grid Orchestrator Source](../bot_v2/grid/orchestrator.py)
- [Grid State Model](../bot_v2/models/grid_state.py)