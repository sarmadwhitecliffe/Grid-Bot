---
goal: Prevent duplicate counter-orders from being placed when the same fill is detected multiple times
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, high, order-manager, fill-handler, idempotency]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

This plan addresses DEFECT-2 from the QA assessment: If the same fill is detected twice (due to network retry, slow API response, or restart mid-detection), multiple counter-orders can be placed at the same grid level, leading to unintended position exposure.

## Implementation Notes

**Status**: COMPLETED (2026-03-16)

**Implementation Notes**: 
- Primary implementation in src/ (`src/oms/fill_handler.py` - fill deduplication)
- bot_v2 has OrderStateManager that tracks order status

## 1. Requirements & Constraints

- **REQ-001**: Each fill must result in exactly one counter-order placement
- **REQ-002**: Processed fills must be tracked persistently to survive restarts
- **REQ-003**: Counter-order placement must be idempotent - safe to retry
- **REQ-004**: Deduplication check must occur BEFORE any exchange API call
- **REQ-005**: Must handle race conditions between concurrent fill processing
- **SEC-001**: Deduplication state must be persisted to WAL
- **CON-001**: Deduplication check must complete in <10ms to not delay trading
- **CON-002**: Must work with both initial orders and counter-orders
- **GUD-001**: Use `processed_fills: Set[str]` with time-based expiry for memory management
- **PAT-001**: Check-then-act pattern with atomic persistence

## 2. Implementation Steps

### Implementation Phase 1: Track Processed Fills

- GOAL-001: Maintain in-memory and persistent set of processed fill IDs

| Task | Description | Completed | Date |
|------|-------------|-----------|------|| TASK-001 | Add `_processed_fills: Set[str]` to `FillHandler` in `src/oms/fill_handler.py:24-38` | | |
| TASK-002 | Create method `_is_fill_processed(order_id: str) -> bool` that checks in-memory set AND persisted WAL | | |
| TASK-003 | Create method `_mark_fill_processed(order_id: str)` that adds to set AND writes to WAL | | |

### Implementation Phase 2: Integrate Deduplication in Fill Handler

- GOAL-002: Check for duplicate fills before processing

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Modify `FillHandler._handle_fill()` in `src/oms/fill_handler.py:78-116` to call `_is_fill_processed()` first | | |
| TASK-005 | Add early return if fill already processed, log warning with order_id | | |
| TASK-006 | Call `_mark_fill_processed()` immediately after successful counter-order placement | | |
| TASK-007 | Ensure `_mark_fill_processed()` is called AFTER WAL write succeeds | | |

### Implementation Phase 3: Persist Processed Fills on Startup

- GOAL-003: Restore processed fills set from WAL on bot startup

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Add WAL operation type `MARK_FILL_PROCESSED` to `src/persistence/wal.py:WALOperationType` | | |
| TASK-009 | Modify `FillHandler.__init__()` to replay processed fills from WAL since last checkpoint | | |
| TASK-010 | Add `_load_processed_fills()` private method called during initialization | | |

### Implementation Phase 4: Add Counter-Order Price Deduplication

- GOAL-004: Prevent counter-orders at same price level even without fill ID

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Add `_pending_counter_prices: Dict[str, float]` mapping grid_level_key to target counter price | | |
| TASK-012 | Before `_place_counter_order()`, check if target price already has pending order in `_grid_map` | | |
| TASK-013 | Log duplicate counter-order attempt with price, level, and reason | | |
| TASK-014 | Clean up `_pending_counter_prices` entry after counter-order fills or is canceled | | |

### Implementation Phase 5: Time-Based Expiry for Processed Fills

- GOAL-005: Prevent unbounded growth of processed fills set

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-015 | Add `_processed_fills_timestamps: Dict[str, datetime]` to track when fills were processed | | |
| TASK-016 | Implement `_cleanup_old_fills()` method that removes entries older than configurable TTL (default: 24 hours) | | |
| TASK-017 | Call `_cleanup_old_fills()` during `_persist_state()` in main trading loop | | |

## 3. Alternatives

- **ALT-001**: Use database with unique constraint on order_id - Rejected: Adds complexity, set in memory is sufficient with WAL backup
- **ALT-002**: Check exchange for existing orders at target price - Rejected: Too slow, adds API dependency
- **ALT-003**: Use Redis with SET_IF_NOT_EXISTS - Rejected: Overkill for single-process bot

## 4. Dependencies

- **DEP-001**: `src/persistence/wal.py` - Must support new operation type
- **DEP-002**: `src/oms/fill_handler.py` - Core modification site
- **DEP-003**: `src/oms/order_manager.py` - `_grid_map` access for price-level check

## 5. Files

- **FILE-001**: `src/oms/fill_handler.py` - Add processed fills tracking and deduplication logic
- **FILE-002**: `src/persistence/wal.py` - Add `MARK_FILL_PROCESSED` operation type
- **FILE-003**: `tests/test_fill_handler.py` - Add deduplication tests
- **FILE-004**: `tests/test_integration_duplicate_fill.py` - New integration test file

## 6. Testing

- **TEST-001**: Unit test - `_is_fill_processed()` returns correct boolean
- **TEST-002**: Unit test - `_mark_fill_processed()` persists to WAL
- **TEST-003**: Unit test - `_load_processed_fills()` restores from WAL on startup
- **TEST-004**: Integration test - Same fill detected twice results in only one counter-order
- **TEST-005**: Integration test - Network timeout and retry doesn't create duplicate
- **TEST-006**: Edge case - Bot restart mid-fill-processing doesn't create duplicate
- **TEST-007**: Edge case - Concurrent fill processing (multi-threaded) handles race condition
- **TEST-008**: Performance test - Deduplication check completes in <10ms for 10,000 processed fills

## 7. Risks & Assumptions

- **RISK-001**: Memory growth if fills processed faster than cleanup - Mitigate with TTL expiry
- **RISK-002**: WAL file corruption could lose processed fill records - Mitigate with checksum validation (already implemented)
- **RISK-003**: Clock skew affects TTL expiry - Use monotonic time for expiry checks
- **ASSUMPTION-001**: Order IDs are unique across exchanges
- **ASSUMPTION-002**: Fill processing is single-threaded per symbol

## 8. Related Specifications / Further Reading

- [fix-fill-persistence-1.md](./fix-fill-persistence-1.md) - Fill persistence (prerequisite)
- [fix-exchange-reconciliation-1.md](./fix-exchange-reconciliation-1.md) - Startup reconciliation
- `src/oms/fill_handler.py:78-116` - Core fill handling logic
- `src/persistence/wal.py:WALOperationType` - WAL operation enum