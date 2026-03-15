---
goal: Implement exchange state reconciliation on bot startup to detect and resolve state divergence
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, high, startup, recovery, exchange]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan addresses DEFECT-3 from the QA assessment: After bot restart, restored state may diverge from exchange state. Orders marked OPEN locally may be filled/canceled on exchange, leading to missing fills and incorrect position tracking.

## 1. Requirements & Constraints

- **REQ-001**: On startup, fetch all open orders from exchange and reconcile with local state
- **REQ-002**: Detect orders in local state but missing from exchange (filled/canceled)
- **REQ-003**: Detect orders on exchange but missing from local state (orphaned)
- **REQ-004**: Automatically process missed fills before resuming trading
- **REQ-005**: Generate reconciliation report with discrepancies
- **REQ-006**: Support configurable reconciliation mode (auto/manual)
- **SEC-001**: Reconciliation must not place new orders until complete
- **SEC-002**: All discrepancies must be logged with timestamps
- **CON-001**: Reconciliation should complete within 30 seconds for normal account size
- **CON-002**: Must work with exchange API rate limits
- **CON-003**: Must handle network failures gracefully
- **GUD-001**: Follow existing `ExchangeClient` patterns for API calls
- **PAT-001**: Two-phase startup: reconcile → validate → resume

## 2. Implementation Steps

### Implementation Phase 1: Create Reconciliation Module

- GOAL-001: Build exchange state reconciler that compares local vs exchange state

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `src/oms/reconciler.py` with `ExchangeReconciler` class | | |
| TASK-002 | Implement `fetch_exchange_orders(symbol: str) -> List[ExchangeOrder]` that calls `ExchangeClient.fetch_open_orders()` | | |
| TASK-003 | Implement `compare_states(local_orders, exchange_orders) -> ReconciliationReport` | | |
| TASK-004 | Define `ReconciliationReport` dataclass with: missing_from_exchange, missing_from_local, price_mismatches, qty_mismatches | | |

### Implementation Phase 2: Detect Missed Fills

- GOAL-002: Identify orders that filled during downtime

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Implement `detect_missed_fills(local_orders: Dict, exchange_orders: List) -> List[MissedFill]` | | |
| TASK-006 | For each order in local OPEN but missing from exchange, call `ExchangeClient.get_order_status()` to get actual status | | |
| TASK-007 | If status is FILLED or PARTIALLY_FILLED, create `MissedFill` record with fill details | | |
| TASK-008 | Add `MissedFill` dataclass: order_id, side, price, filled_qty, fill_price, timestamp | | |

### Implementation Phase 3: Process Missed Fills

- GOAL-003: Simulate fill handling for orders that filled during downtime

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | Implement `process_missed_fills(missed_fills: List[MissedFill], order_manager: OrderManager)` | | |
| TASK-010 | For each missed fill, call `FillHandler._handle_fill()` with order details | | |
| TASK-011 | Pass `is_recovery=True` flag to `_handle_fill()` to skip exchange confirmations | | |
| TASK-012 | Log each recovered fill with `RECOVERY` source tag | | |

### Implementation Phase 4: Handle Orphaned Orders

- GOAL-004: Detect and handle orders on exchange that aren't in local state

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-013 | Implement `detect_orphaned_orders(local_orders: Dict, exchange_orders: List) -> List[OrphanedOrder]` | | |
| TASK-014 | Add configurable `ORPHANED_ORDER_ACTION` in settings: `CANCEL` | `KEEP` | `IMPORT` | | |
| TASK-015 | If action is `IMPORT`, add orphaned order to local `OrderManager` with `imported=True` flag | | |
| TASK-016 | If action is `CANCEL`, call `ExchangeClient.cancel_order()` and log | | |
| TASK-017 | Create `OrphanedOrder` dataclass: exchange_order_id, side, price, amount, detected_at | | |

### Implementation Phase 5: Integrate with Startup Sequence

- GOAL-005: Add reconciliation to main.py startup flow

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-018 | Modify `main.py:_restore_state()` to call `ExchangeReconciler.reconcile()` after `StateStore.load()` | | |
| TASK-019 | Add `RECONCILE_ON_STARTUP` setting (default: True) in `config/settings.py` | | |
| TASK-020 | Add `--skip-reconcile` CLI flag for emergency manual control | | |
| TASK-021 | Save reconciliation report to `data/state/reconciliation_report.json` | | |
| TASK-022 | Throw `StartupValidationError` if discrepancies exceed configurable threshold | | |

### Implementation Phase 6: Add Safety Checks

- GOAL-006: Prevent trading until reconciliation confirms clean state

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-023 | Add `is_reconciled: bool` flag to `OrderManager` | | |
| TASK-024 | Modify `OrderManager.place_order()` to check `is_reconciled` before placing new orders | | |
| TASK-025 | Set `is_reconciled = True` only after successful reconciliation | | |
| TASK-026 | Add `force_unreconciled` flag for emergency recovery mode | | |

## 3. Alternatives

- **ALT-001**: Always cancel all exchange orders on startup and redeploy grid - Rejected: Loses active positions, potentially costly
- **ALT-002**: Store order IDs in database with exchange confirmation - Rejected: Doesn't handle external fills
- **ALT-003**: Use exchange WebSocket for real-time order updates - Rejected: Requires persistent connection, still need startup reconciliation

## 4. Dependencies

- **DEP-001**: `src/exchange/exchange_client.py` - Must have `fetch_open_orders()` and `get_order_status()`
- **DEP-002**: `src/oms/order_manager.py` - Must expose `_orders` for comparison
- **DEP-003**: `src/oms/fill_handler.py` - Must support `_handle_fill()` with recovery flag
- **DEP-004**: `main.py` - Startup sequence modification point

## 5. Files

- **FILE-001**: `src/oms/reconciler.py` - New file: ExchangeReconciler class
- **FILE-002**: `src/oms/__init__.py` - Add ReconciliationReport, MissedFill, OrphanedOrder dataclasses
- **FILE-003**: `main.py` - Integrate reconciliation into startup
- **FILE-004**: `config/settings.py` - Add RECONCILE_ON_STARTUP, ORPHANED_ORDER_ACTION settings
- **FILE-005**: `tests/test_reconciler.py` - New test file
- **FILE-006**: `tests/test_main_startup.py` - Startup sequence tests

## 6. Testing

- **TEST-001**: Unit test - `compare_states()` correctly identifies missing orders
- **TEST-002**: Unit test - `detect_missed_fills()` calls `get_order_status()` for missing orders
- **TEST-003**: Unit test - `detect_orphaned_orders()` finds exchange-only orders
- **TEST-004**: Integration test - Full reconciliation flow with mock exchange
- **TEST-005**: Integration test - Missed fills trigger counter-order placement
- **TEST-006**: Integration test - Orphaned orders are handled according to config
- **TEST-007**: Edge case - Empty exchange and local state (first startup)
- **TEST-008**: Edge case - All local orders filled during downtime
- **TEST-009**: Edge case - Network failure during reconciliation
- **TEST-010**: Performance test - Reconciliation with 100 orders completes in <30 seconds

## 7. Risks & Assumptions

- **RISK-001**: Exchange API rate limits could slow reconciliation - Mitigate with batch fetching and caching
- **RISK-002**: Concurrent trading on same account could cause race conditions - Document single-instance requirement
- **RISK-003**: Partial fills during reconciliation - Track `_remaining_qty` separately
- **ASSUMPTION-001**: Exchange provides reliable order status API
- **ASSUMPTION-002**: No other process is modifying orders during reconciliation
- **ASSUMPTION-003**: Exchange uses client_order_id for order tracking

## 8. Related Specifications / Further Reading

- [fix-fill-persistence-1.md](./fix-fill-persistence-1.md) - Fill persistence (prerequisite for recovery)
- [fix-counter-order-dedup-1.md](./fix-counter-order-dedup-1.md) - Deduplication
- `src/exchange/exchange_client.py` - Exchange API interface
- `main.py:138-153` - Current startup sequence reference