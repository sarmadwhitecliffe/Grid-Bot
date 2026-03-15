---
goal: Add support for partially filled orders to prevent position tracking errors
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, medium, fill-handler, order-status]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

This plan addresses DEFECT-4 from the QA assessment: `OrderStatus.PARTIALLY_FILLED` status exists but is never processed. Partial fills leave the remaining quantity unfilled and no counter-order placed, leading to incorrect position tracking and potential missed profit opportunities.

## Implementation Notes

**Status**: COMPLETED (2026-03-16)

**Implementation Notes**: 
- Implementation in src/ (`src/oms/__init__.py` - PartialFill, `src/oms/fill_handler.py`)

## 1. Requirements & Constraints

- **REQ-001**: Detect and process `PARTIALLY_FILLED` order status from exchange
- **REQ-002**: Track `filled_qty` separately from `remaining_qty` for each order
- **REQ-003**: Place counter-order only for filled quantity, not full order amount
- **REQ-004**: Keep remaining quantity as open order on exchange
- **REQ-005**: Update position tracking with partial fill details
- **REQ-006**: Support multiple partial fills for same order
- **SEC-001**: Partial fill must update position before placing counter-order
- **CON-001**: Must handle exchange-specific partial fill behavior (Binance uses `filled` field)
- **CON-002**: Counter-order amount must equal filled amount, not original order amount
- **GUD-001**: Use Decimal for all quantity calculations to avoid floating point errors
- **PAT-001**: Treat partial fill as regular fill for the filled portion

## 2. Implementation Steps

### Implementation Phase 1: Extend Order Data Model

- GOAL-001: Add partial fill tracking fields to OrderRecord

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `filled_qty: Decimal` field to `OrderRecord` in `src/oms/__init__.py:44-70` (default: Decimal("0")) | | |
| TASK-002 | Add `remaining_qty: Decimal` property that computes `amount - filled_qty` | | |
| TASK-003 | Add `partial_fills: List[PartialFill]` field to track fill history | | |
| TASK-004 | Create `PartialFill` dataclass: fill_id, timestamp, filled_qty, fill_price, fee | | |
| TASK-005 | Add `PARTIALLY_FILLED` status to `OrderStatus` enum if doesn't exist | | |

### Implementation Phase 2: Update Fill Detection

- GOAL-002: Detect partial fills from exchange order status

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | Modify `FillHandler._detect_fills()` in `src/oms/fill_handler.py:74-79` to check for `filled` > 0 | | |
| TASK-007 | If `filled > 0` and `remaining > 0`, treat as partial fill | | |
| TASK-008 | If `filled > 0` and `remaining == 0`, treat as full fill | | |
| TASK-009 | Add `_is_partial_fill(exchange_order: dict) -> bool` helper method | | |

### Implementation Phase 3: Process Partial Fills

- GOAL-003: Handle partial fills with correct quantity tracking

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | Create `_handle_partial_fill(order: OrderRecord, filled_qty: Decimal, fill_price: Decimal)` in `src/oms/fill_handler.py` | | |
| TASK-011 | Update `order.filled_qty += filled_qty` | | |
| TASK-012 | Append `PartialFill` record to `order.partial_fills` list | | |
| TASK-013 | Call `_place_counter_order()` with `filled_qty` as amount, NOT `order.amount` | | |
| TASK-014 | Leave order in `OPEN` status with updated `remaining_qty` | | |
| TASK-015 | If full fill, change status to `FILLED` and remove from `_grid_map` | | |

### Implementation Phase 4: Update Position Tracking

- GOAL-004: Correctly track position changes from partial fills

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-016 | Modify `FillHandler._update_position()` to use `filled_qty` from partial fill | | |
| TASK-017 | Ensure position delta matches actual filled amount | | |
| TASK-018 | Log partial fill details: order_id, qty_filled, qty_remaining, fill_price | | |

### Implementation Phase 5: Handle Counter-Order for Partial Fills

- GOAL-005: Place correct counter-order amount

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-019 | Modify `_place_counter_order()` in `src/oms/fill_handler.py:90-116` to accept `amount: Decimal` parameter | | |
| TASK-020 | Counter-order `amount` = partial fill `filled_qty`, not original order amount | | |
| TASK-021 | Track counter-orders separately: one counter-order per partial fill | | |
| TASK-022 | When remaining_qty fills completely, original order removed from open orders | | |

## 3. Alternatives

- **ALT-001**: Cancel original order and place new order for remaining - Rejected: Loses queue position on exchange
- **ALT-002**: Wait for full fill before placing counter-order - Rejected: Misses profit on partial fills
- **ALT-003**: Treat partial as full fill using filled amount - Accepted: This is the implemented approach

## 4. Dependencies

- **DEP-001**: `src/oms/__init__.py` - OrderRecord model changes
- **DEP-002**: `src/oms/fill_handler.py` - Core processing logic
- **DEP-003**: `src/exchange/exchange_client.py` - Exchange `get_order_status()` must return `filled` field

## 5. Files

- **FILE-001**: `src/oms/__init__.py` - Add PartialFill dataclass, extend OrderRecord
- **FILE-002**: `src/oms/fill_handler.py` - Add partial fill handling logic
- **FILE-003**: `src/persistence/state_store.py` - Update serialization for partial fills
- **FILE-004**: `tests/test_fill_handler.py` - Add partial fill tests
- **FILE-005**: `tests/test_order_record.py` - New test file for OrderRecord model

## 6. Testing

- **TEST-001**: Unit test - `OrderRecord.remaining_qty` computed correctly
- **TEST-002**: Unit test - `_is_partial_fill()` returns correct boolean
- **TEST-003**: Unit test - `_handle_partial_fill()` updates filled_qty
- **TEST-004**: Unit test - Multiple partial fills accumulate correctly
- **TEST-005**: Unit test - Counter-order amount equals partial fill quantity
- **TEST-006**: Integration test - 50% fill then 50% fill produces two counter-orders
- **TEST-007**: Integration test - Position tracking reflects accumulated partial fills
- **TEST-008**: Edge case - Very small partial fill (dust amount)
- **TEST-009**: Edge case - Partial fill followed by full fill
- **TEST-010**: Edge case - Exchange returns 0 remaining but partial fill history

## 7. Risks & Assumptions

- **RISK-001**: Multiple partial fills could create many counter-orders - Accept as grid behavior
- **RISK-002**: Floating point precision issues - Mitigate with Decimal throughout
- **RISK-003**: Exchange may not expose partial fill timestamps - Use current time as fallback
- **ASSUMPTION-001**: Exchange `filled` field is accurate and updated atomically
- **ASSUMPTION-002**: Partial fills are returned in order-by-order status, not separate events
- **ASSUMPTION-003**: No fee on partial fills until final fill (exchange-dependent)

## 8. Related Specifications / Further Reading

- [fix-fill-persistence-1.md](./fix-fill-persistence-1.md) - Fill persistence (prerequisite)
- `src/strategy/__init__.py:59-73` - GridLevel with level_index reference
- `tests/test_fill_handler.py:256-337` - Existing fill handling tests to extend