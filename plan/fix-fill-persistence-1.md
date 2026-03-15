---
goal: Implement persistent fill logging to prevent data loss on bot restart
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, critical, persistence, fill-handler]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan addresses DEFECT-1 from the QA assessment: Fills are detected in-memory and immediately mutated to FILLED status, but no persistent record is written. On crash or restart, filled orders appear as OPEN in restored state, leading to duplicate positions when counter-orders are placed.

## 1. Requirements & Constraints

- **REQ-001**: Every fill must be written to persistent storage BEFORE any state mutation
- **REQ-002**: Fill records must include: order_id, exchange_order_id, fill_price, fill_qty, timestamp, side, grid_level, parent_order_id
- **REQ-003**: Fill logging must use WAL (Write-Ahead Logging) for crash recovery
- **REQ-004**: Fills must be queryable by order_id for restart reconciliation
- **REQ-005**: Fill file must support streaming appends (JSONL format)
- **SEC-001**: No sensitive API credentials in fill logs
- **CON-001**: Must not block the trading loop - use async writes where possible
- **CON-002**: Must integrate with existing `StateStore` and `WALManager`
- **GUD-001**: Follow existing patterns in `state_store.py` for file I/O
- **PAT-001**: Use `log_trade()` method that already exists but is never called

## 2. Implementation Steps

### Implementation Phase 1: Extend Fill Data Model

- GOAL-001: Add fill metadata fields to OrderRecord and create FillRecord class

| Task | Description | Completed | Date |
|------|-------------|-----------|------|| TASK-001 | Add `filled_price`, `filled_at`, `fill_qty`, `parent_order_id` to `OrderRecord` in `src/oms/__init__.py:44-70` | | || TASK-002 | Create `FillRecord` dataclass in `src/oms/__init__.py` with fields: fill_id, order_id, exchange_order_id, side, price, qty, timestamp, grid_level, parent_order_id | | |
| TASK-003 | Update `OrderRecord.to_dict()` and `from_dict()` to serialize new fields | | |

### Implementation Phase 2: Integrate WAL for Fill Logging

- GOAL-002: Ensure all fills are written to WAL before state mutation

| Task | Description | Completed | Date |
|------|-------------|-----------|------|| TASK-004 | Modify `FillHandler._handle_fill()` in `src/oms/fill_handler.py:78-86` to call `WALManager.log_fill()` BEFORE changing order status | | |
| TASK-005 | Create `FillLogger` class in `src/persistence/fill_logger.py` that wraps WAL and append-only fill log | | |
| TASK-006 | Add `log_fill_async()` method to `FillLogger` for non-blocking writes | | |

### Implementation Phase 3: Implement Fill Replay on Startup

- GOAL-003: Replay fills from WAL during state restoration

| Task | Description | Completed | Date |
|------|-------------|-----------|------|| TASK-007 | Add `replay_pending_fills()` method to `WALManager` that processes fills since last checkpoint | | |
| TASK-008 | Modify `main.py:_restore_state()` to call `replay_pending_fills()` after loading `StateStore` | | |
| TASK-009 | Add fill replay integration test in `tests/test_fill_handler.py` | | |

### Implementation Phase 4: Update StateStore Trade Logging

- GOAL-004: Connect existing `log_trade()` to fill handler

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | Modify `StateStore.log_trade()` in `src/persistence/state_store.py:107-124` to use `FillLogger` internally | | |
| TASK-011 | Add `get_fills_by_order(order_id)` method to `StateStore` for reconciliation queries | | |
| TASK-012 | Ensure `fill_log.jsonl` is created with proper schema | | |

## 3. Alternatives

- **ALT-001**: Store fills in SQLite database instead of JSONL - Rejected: Adds complexity, JSONL is sufficient for sequential append-only writes
- **ALT-002**: Use Redis for fill caching - Rejected: Adds infrastructure dependency, not needed for single-process bot
- **ALT-003**: Write fills directly to `orders_state.json` - Rejected: Requires full rewrite of orders file on each fill, potential for corruption

## 4. Dependencies

- **DEP-001**: `src/persistence/wal.py` - WALManager must be initialized before FillHandler
- **DEP-002**: `src/persistence/state_store.py` - StateStore must support fill queries
- **DEP-003**: `src/oms/__init__.py` - OrderRecord model changes

## 5. Files

- **FILE-001**: `src/oms/__init__.py` - Add FillRecord dataclass and extend OrderRecord
- **FILE-002**: `src/oms/fill_handler.py` - Integrate WAL logging before fill processing
- **FILE-003**: `src/persistence/fill_logger.py` - New file for fill logging logic
- **FILE-004**: `src/persistence/state_store.py` - Connect log_trade() to FillLogger
- **FILE-005**: `src/persistence/wal.py` - Add fill replay support
- **FILE-006**: `main.py` - Add fill replay on startup
- **FILE-007**: `tests/test_fill_handler.py` - Add integration tests
- **FILE-008**: `tests/test_persistence_wal.py` - Add fill replay tests

## 6. Testing

- **TEST-001**: Unit test - `FillLogger.log_fill()` writes to WAL and fill_log.jsonl
- **TEST-002**: Unit test - `WALManager.replay_pending_fills()` correctly restores fills
- **TEST-003**: Integration test - Simulate crash after fill, verify replay recovers state
- **TEST-004**: Concurrency test - Multiple fills logged simultaneously don't corrupt file
- **TEST-005**: Edge case - Empty WAL on first startup doesn't cause errors
- **TEST-006**: Edge case - Corrupt fill entry in WAL is handled gracefully

## 7. Risks & Assumptions

- **RISK-001**: Disk I/O latency could slow trading loop - Mitigate with async writes
- **RISK-002**: WAL files grow unbounded - Mitigate with checkpoint rotation (already implemented)
- **RISK-003**: Fill replay on startup adds latency - Acceptable for grid bot use case
- **ASSUMPTION-001**: Fills are processed sequentially, no concurrent fill detection for same order
- **ASSUMPTION-002**: Exchange provides reliable order status via `get_order_status()`

## 8. Related Specifications / Further Reading

- [data-persistence-integrity-1.md](./data-persistence-integrity-1.md) - Related persistence improvements
- [fix-data-persistence-issues-1.md](./fix-data-persistence-issues-1.md) - Earlier fix attempt
- `src/persistence/wal.py` - WAL implementation reference
- `tests/test_persistence_wal.py` - WAL test patterns to follow