---
goal: Populate fill_price and filled_at fields in OrderRecord for audit trail
version: 1.0
date_created: 2026-03-16
last_updated: 2026-03-16
owner: QA Team
status: 'Completed'
tags: [bug, medium, order-record, audit, fill-handler]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan addresses DEFECT-5 from the QA assessment: `OrderRecord` has `filled_price` and `filled_at` fields but they are never populated. This results in no fill price audit trail, making it impossible to verify execution prices or calculate accurate PnL per trade.

## 1. Requirements & Constraints

- **REQ-001**: `filled_price` must be set to actual exchange fill price, not order limit price
- **REQ-002**: `filled_at` must be set to exchange fill timestamp or current time if unavailable
- **REQ-003**: Fill price must be fetched from exchange via `get_order_status()`
- **REQ-004**: Fill price and timestamp must be persisted to `orders_state.json`
- **REQ-005**: Fill price must be written to `fill_log.jsonl` for audit trail
- **SEC-001**: Fill price must not be estimated or approximated
- **CON-001**: Exchange API for fill price is rate-limited - batch if possible
- **CON-002**: Must handle exchange API failure gracefully (use order price as fallback with warning)
- **CON-003**: Fill price is usually the limit price for limit orders, but must verify
- **GUD-001**: Use Decimal for fill price to maintain precision
- **PAT-001**: Fetch fill details immediately after fill detection

## 2. Implementation Steps

### Implementation Phase 1: Fetch Fill Price from Exchange

- GOAL-001: Retrieve actual fill price from exchange order status

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Verify `ExchangeClient.get_order_status()` returns `average_price` or `fill_price` field | | |
| TASK-002 | Create `_fetch_fill_details(order_id: str) -> FillDetails` in `src/oms/fill_handler.py` | | |
| TASK-003 | Call exchange `get_order_status(order_id)` to get fill details | | |
| TASK-004 | Extract `fill_price`, `filled_at` from exchange response | | |
| TASK-005 | Handle case where fill details unavailable - use order price with warning log | | |

### Implementation Phase 2: Update OrderRecord on Fill

- GOAL-002: Populate fill price and timestamp in OrderRecord

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | In `_handle_fill()`, after detecting fill, call `_fetch_fill_details()` | | |
| TASK-007 | Set `order.filled_price = fill_price` (Decimal from exchange) | | |
| TASK-008 | Set `order.filled_at = filled_at` (datetime from exchange or now()) | | |
| TASK-009 | Handle partial fills: `filled_price` should be average of partial fill prices | | |
| TASK-010 | Set `status = OrderStatus.FILLED` only after fields populated | | |

### Implementation Phase 3: Persist Fill Price to State File

- GOAL-003: Ensure fill price survives restart

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Verify `OrderRecord.to_dict()` includes `filled_price` and `filled_at` in `src/oms/__init__.py` | | |
| TASK-012 | Verify `OrderRecord.from_dict()` correctly deserializes `filled_price` and `filled_at` | | |
| TASK-013 | Test: Save state after fill, restart, verify fill price persisted | | |

### Implementation Phase 4: Write to Fill Log

- GOAL-004: Record fill price in persistent fill log

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-014 | Modify `StateStore.log_trade()` in `src/persistence/state_store.py:107-124` to accept `fill_price` | | |
| TASK-015 | Ensure `fill_log.jsonl` entry includes: `fill_price`, `fill_timestamp`, `order_limit_price` | | |
| TASK-016 | Call `log_trade()` after successfully populating `OrderRecord.filled_price` | | |

### Implementation Phase 5: Add Fill Price Validation

- GOAL-005: Validate fill price is reasonable

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | Add `_validate_fill_price(fill_price: Decimal, order_price: Decimal) -> bool` | | |
| TASK-018 | Check fill price is within reasonable slippage of order price (configurable, default: 1%) | | |
| TASK-019 | Log warning if fill price deviates more than slippage threshold | | |
| TASK-020 | Add `FILL_SLIPPAGE_THRESHOLD_PCT` setting in `config/settings.py` | | |

## 3. Alternatives

- **ALT-001**: Use limit price as fill price - Rejected: Inaccurate, defeats purpose
- **ALT-002**: Fetch fill price from WebSocket stream - Rejected: Requires persistent connection, more complex
- **ALT-003**: Calculate from position change - Rejected: Circular dependency with position tracking

## 4. Dependencies

- **DEP-001**: `src/exchange/exchange_client.py` - Must return fill price in order status
- **DEP-002**: `src/oms/__init__.py` - OrderRecord fields and serialization
- **DEP-003**: `src/persistence/state_store.py` - log_trade() method
- **DEP-004**: `src/oms/fill_handler.py` - Fill processing logic

## 5. Files

- **FILE-001**: `src/oms/fill_handler.py` - Add _fetch_fill_details(), update _handle_fill()
- **FILE-002**: `src/oms/__init__.py` - Verify serialization of filled_price/filled_at
- **FILE-003**: `src/persistence/state_store.py` - Update log_trade() signature
- **FILE-004**: `config/settings.py` - Add FILL_SLIPPAGE_THRESHOLD_PCT
- **FILE-005**: `tests/test_fill_handler.py` - Add fill price tests
- **FILE-006**: `tests/test_order_record.py` - Add serialization tests

## 6. Testing

- **TEST-001**: Unit test - `_fetch_fill_details()` extracts correct fill price from exchange response
- **TEST-002**: Unit test - `OrderRecord.filled_price` populated after `_handle_fill()` completes
- **TEST-003**: Unit test - `OrderRecord.filled_at` populated with valid timestamp
- **TEST-004**: Unit test - `_validate_fill_price()` correctly validates slippage
- **TEST-005**: Integration test - Fill price persisted to `orders_state.json`
- **TEST-006**: Integration test - Fill price written to `fill_log.jsonl`
- **TEST-007**: Edge case - Exchange returns no fill price (error handling)
- **TEST-008**: Edge case - Partial fill with multiple prices (average calculation)
- **TEST-009**: Edge case - Fill price deviates more than slippage threshold (warning logged)

## 7. Risks & Assumptions

- **RISK-001**: Exchange API for fill price may be slow - Will add latency to fill processing
- **RISK-002**: Exchange may not return fill timestamp - Use current time as fallback
- **RISK-003**: Multiple partial fills have different prices - Must compute average fill price
- **ASSUMPTION-001**: Exchange returns `averagePrice` or equivalent field in order status
- **ASSUMPTION-002**: Exchange API returns reliable fill price immediately after fill
- **ASSUMPTION-003**: Fill price for limit orders equals limit price in most cases

## 8. Related Specifications / Further Reading

- [fix-fill-persistence-1.md](./fix-fill-persistence-1.md) - Fill persistence (related)
- [fix-partial-fill-handling-1.md](./fix-partial-fill-handling-1.md) - Partial fills (related)
- `src/exchange/exchange_client.py` - Exchange API interface
- Binance API docs - Order status response format