---
goal: Add grid level tracking to OrderRecord for traceability
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, medium, order-record, grid-tracker, audit]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan addresses DEFECT-6 from the QA assessment: `OrderRecord` has no `grid_level_id` field. `GridLevel` has `level_index` but it's not preserved in order tracking, making it impossible to trace which grid level an order belongs to or understand the grid structure from order history.

## 1. Requirements & Constraints

- **REQ-001**: Every order must be associated with a grid level identifier
- **REQ-002**: `grid_level_id` must be an integer (0-based level index)
- **REQ-003**: Level ID must be inherited from `GridLevel.level_index` when order is placed
- **REQ-004**: Level ID must be persisted to `orders_state.json`
- **REQ-005**: Level ID must be included in `fill_log.jsonl` entries
- **REQ-006**: Level ID enables queries like "all orders for level5"
- **SEC-001**: Level ID must be immutable after order creation
- **CON-001**: Existing orders without level ID must be handled (default to -1 or None)
- **CON-002**: Level ID is relative to grid center price at deployment time
- **GUD-001**: Use same indexing as `GridLevel.level_index` (negative for below center, positive for above)
- **PAT-001**: Pass level_index from GridLevel to OrderRecord at deployment

## 2. Implementation Steps

### Implementation Phase 1: Add Grid Level ID to OrderRecord

- GOAL-001: Extend OrderRecord with grid level identifier

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `grid_level_id: Optional[int]` field to `OrderRecord` in `src/oms/__init__.py:44-70` (default: None) | | |
| TASK-002 | Add `grid_level_id: int` parameter to `OrderRecord.__init__()` | | |
| TASK-003 | Update `OrderRecord.to_dict()` to include `grid_level_id` | | |
| TASK-004 | Update `OrderRecord.from_dict()` to deserialize `grid_level_id` | | |
| TASK-005 | Handle legacy orders without `grid_level_id` - default to None with warning log | | |

### Implementation Phase 2: Pass Level Index at Order Creation

- GOAL-002: Ensure grid level is captured when order is placed

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | Modify `OrderManager.deploy_grid()` to pass `level.level_index` to order creation | | |
| TASK-007 | Infill_handler._place_counter_order()` - must determine level_index for counter-order | | |
| TASK-008 | Counter-order level = original level + 1 (for buy->sell) or -1 (for sell->buy) | | |
| TASK-009 | Log level ID in order placement: "Placing order at level {grid_level_id}" | | |

### Implementation Phase 3: Update GridLevel Dataclass

- GOAL-003: Ensure GridLevel provides level_index

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | Verify `GridLevel` in `src/strategy/__init__.py:59-73` has `level_index: int` field | | |
| TASK-011 | Verify `GridCalculator.calculate()` returns GridLevel with populated `level_index` | | |
| TASK-012 | Add unit test verifying level_index is sequential: [-N, ..., -1, 1, ..., N] (no 0) | | |

### Implementation Phase 4: Persist Level ID

- GOAL-004: Ensure level ID is saved and restored

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-013 | Verify `StateStore.save()` includes `grid_level_id` in order serialization | | |
| TASK-014 | Verify `StateStore.load()` restores `grid_level_id` to OrderRecord | | |
| TASK-015 | Add migration for existing `orders_state.json` files without level ID | | |

### Implementation Phase 5: Include in Fill Log

- GOAL-005: Record level ID in trade log for analysis

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-016 | Modify `StateStore.log_trade()` to include `grid_level_id` in fill log entry | | |
| TASK-017 | Update fill log schema: `{order_id, side, price, amount, grid_level_id, timestamp, ...}` | | |
| TASK-018 | Add query method `get_orders_by_level(level_id: int) -> List[OrderRecord]` | | |

## 3. Alternatives

- **ALT-001**: Use grid_price as level identifier - Rejected: Price changes when grid recenters, level ID is stable
- **ALT-002**: Store level ID as string with symbol prefix - Rejected: Integer is simpler for queries
- **ALT-003**: Derive level from price and grid spacing - Rejected: Fails when grid recenters

## 4. Dependencies

- **DEP-001**: `src/oms/__init__.py` - OrderRecord
- **DEP-002**: `src/strategy/__init__.py` - GridLevel
- **DEP-003**: `src/strategy/grid_calculator.py` - GridCalculator.calculate()
- **DEP-004**: `src/oms/order_manager.py` - deploy_grid()
- **DEP-005**: `src/oms/fill_handler.py` - _place_counter_order()

## 5. Files

- **FILE-001**: `src/oms/__init__.py` - Addgrid level_id field to OrderRecord
- **FILE-002**: `src/oms/order_manager.py` - Pass level_index at order creation
- **FILE-003**: `src/oms/fill_handler.py` - Derive level for counter-orders
- **FILE-004**: `src/persistence/state_store.py` - Add get_orders_by_level() query
- **FILE-005**: `tests/test_order_record.py` - Add grid level tests
- **FILE-006**: `tests/test_fill_handler.py` - Add counter-order level tests

## 6. Testing

- **TEST-001**: Unit test - `OrderRecord.grid_level_id` populated from `GridLevel.level_index`
- **TEST-002**: Unit test - `OrderRecord.to_dict()` includes `grid_level_id`
- **TEST-003**: Unit test - `OrderRecord.from_dict()` restores `grid_level_id`
- **TEST-004**: Unit test - `GridCalculator.calculate()` returns sequential level_index values
- **TEST-005**: Unit test - Counter-order level is original level +1 (buy->sell) or -1 (sell->buy)
- **TEST-006**: Integration test - Level ID persisted to `orders_state.json`
- **TEST-007**: Integration test - Level ID appears in `fill_log.jsonl`
- **TEST-008**: Edge case - Legacy order without level ID handled gracefully
- **TEST-009**: Edge case - Multiple grids for same symbol have independent level IDs
- **TEST-010**: Query test - `get_orders_by_level(5)` returns all orders at level 5

## 7. Risks & Assumptions

- **RISK-001**: Grid recentering changes which prices correspond to which levels - Accept as design, level ID tracks original placement
- **RISK-002**: Multiple grids for same symbol could have overlapping level IDs - Disambiguate with parent_grid_id if needed
- **RISK-003**: Counter-order level calculation could be wrong - Thorough testing required
- **ASSUMPTION-001**: Grid levels are stable for lifetime of an order
- **ASSUMPTION-002**: Level indexing is consistent across bot restarts
- **ASSUMPTION-003**: Single active grid per symbol at a time

## 8. Related Specifications / Further Reading

- [fix-fill-persistence-1.md](./fix-fill-persistence-1.md) - Fill persistence
- [fix-counter-order-dedup-1.md](./fix-counter-order-dedup-1.md) - Counter-order handling
- `src/strategy/__init__.py:59-73` - GridLevel definition
- `src/strategy/grid_calculator.py:109-126` - Level calculation logic