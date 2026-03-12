# TDD-Red Test Plan: Grid Bot Regressions

**Created:** 11 Mar 2026  
**Mode:** Test-Driven Development (Red Phase — Write Failing Tests First)  
**Goal:** Design failing tests for 4 critical regressions before implementing fixes.

---

## Executive Summary

| Issue | Root Cause | Test Target | Test Count |
|-------|-----------|------------|-----------|
| 1 — Missing `await` on order state updates | Race condition in fill pipeline | `tests/test_fill_handler.py` | 4 tests |
| 2 — Decimal/Float TypeError in GridCalculator | Type mismatch after refactor | `tests/test_grid_calculator.py` | 3 tests |
| 3 — Fill-order sequencing bug | Order closed before orchestrator.handle_fill lookup | `tests/test_fill_handler.py` + new integration | 5 tests |
| 4 — API drift in execution tests | clear_order_tracking async signature changed | `bot_v2/tests/test_execution.py` | 3 tests |

**Total New Tests:** 15 failing tests  
**Test Pattern:** xUnit (pytest) + AsyncIO + Mocking (no live API calls)

---

## REGRESSION 1: Missing `await` on OrderStateManager Updates

### Problem Summary
In `bot_v2/execution/simulated_exchange.py:360`, the `check_fills()` method calls an async function without `await`:

```python
self.order_state_manager.update_order_status(  # <- MISSING await!
    record.local_id, 
    "CLOSED",
    filled_qty=record.quantity,
    avg_price=record.avg_price
)
```

**Impact:** Order state never persists to disk. State corruption on crash recovery. Duplicate fills.

### Test File Targets
- **Primary:** `tests/test_fill_handler.py` (existing file, add new test class)
- **Secondary:** `bot_v2/tests/test_order_state_manager.py` (verify update_order_status is truly async)

### Test Class: `TestFillHandlerAwaitCompleteness`

#### Test 1: `test_await_applied_to_order_state_update_on_fill_detection`

**Purpose:** Verify that when a fill is detected, the order state update is awaited (not fire-and-forget).

**Given:**
- FillHandler with mocked OrderManager and ExchangeClient
- 3 simulated orders in OPEN status at prices [29900, 30000, 30100]
- Mock exchange reports 2 orders still open (order at 30000 is missing = filled)

**When:**
```python
filled_orders = await fill_handler.poll_and_handle(centre_price=30000.0)
```

**Then:**
- Order at 30000 status is marked as FILLED
- `order_manager.all_records[filled_order_id].status == OrderStatus.FILLED`
- **No race condition:** The status change is fully persisted before returning

**Expected Failure Mode (Before Fix):**
```
AssertionError: Expected status=FILLED but got status=OPEN
# Because update_order_status was not awaited, state never updates
```

**Code Pattern to Assert:**
```python
filled = await fill_handler.poll_and_handle(30000.0)
assert len(filled) == 1
record_id = filled[0].order_id
# After await completes, state must be updated
updated_record = fill_handler.order_manager.all_records[record_id]
assert updated_record.status == OrderStatus.FILLED
```

---

#### Test 2: `test_state_update_on_fill_does_not_race_with_next_poll_cycle`

**Purpose:** Verify that the state update from one fill detection completes before the next poll cycle begins.

**Given:**
- FillHandler with 2 orders at [29900, 30000]
- First poll detects fill at 30000
- Immediately call poll_and_handle again

**When:**
```python
filled_1 = await fill_handler.poll_and_handle(30000.0)
filled_2 = await fill_handler.poll_and_handle(30000.0)  # Second poll cycle
```

**Then:**
- First fill is fully processed (state updated to FILLED)
- Second poll sees the order as FILLED, not OPEN
- No duplicate fills detected

**Expected Failure Mode:**
```
AssertionError: Expected 0 filled orders in 2nd cycle, but got 1
# Because state update wasn't awaited, order still appears OPEN
```

---

#### Test 3: `test_counter_order_placement_uses_correct_filled_status`

**Purpose:** Verify that the counter-order trigger reads the correctly-awaited fill status.

**Given:**
- FillHandler with 1 open BUY order at 29900
- Mock OrderManager.deploy_grid to record when called
- Exchange reports no open orders (fill detected)

**When:**
```python
filled = await fill_handler.poll_and_handle(30000.0)
```

**Then:**
- Counter-order (SELL) is placed exactly once
- Counter-order price is one grid level above 29900
- Order record marked as FILLED before counter-order logic runs

**Expected Failure Mode:**
```
AssertionError: Expected 1 counter-order placed, but got 0
# Because order state update not awaited; counter-order logic sees OPEN, not FILLED
```

---

#### Test 4: `test_multiple_fills_in_same_cycle_all_awaited`

**Purpose:** Verify that when multiple fills are detected in one poll cycle, ALL state updates are awaited.

**Given:**
- FillHandler with 5 open orders at [29700, 29900, 30000, 30100, 30300]
- Exchange reports only 2 open (orders at 29900, 30100)
- 3 orders filled (at 29700, 30000, 30300)

**When:**
```python
filled = await fill_handler.poll_and_handle(30000.0)
```

**Then:**
- All 3 filled orders have status = FILLED
- **All 3 state updates fully completed** (no partial updates)
- No race conditions between concurrent state updates

**Expected Failure Mode:**
```
AssertionError: Expected 3 fills with FILLED status, but got [FILLED, OPEN, OPEN]
# Some state updates not awaited; inconsistent state
```

---

## REGRESSION 2: Decimal/Float TypeError in GridCalculator

### Problem Summary
After Decimal refactor, `GridCalculator.__init__` converts inputs to Decimal:

```python
self.spacing_pct = Decimal(str(spacing_pct))  # Now Decimal
self.price_step = Decimal(str(price_step))    # Now Decimal
```

But tests call `calculate()` with float:

```python
levels = calc.calculate(30_000.0)  # <- float, not Decimal
```

**Inside calculate():** Operations mix float and Decimal → `TypeError`.

**Impact:** Grid calculation fails at runtime. All grid deployment blocked.

### Test File Targets
- **Primary:** `tests/test_grid_calculator.py` (existing file, add new test class)

### Test Class: `TestGridCalculatorDecimalTypeConsistency`

#### Test 1: `test_calculate_accepts_decimal_centre_price`

**Purpose:** Verify calculate() accepts Decimal centre_price without TypeError.

**Given:**
```python
calc = GridCalculator(
    grid_type=GridType.GEOMETRIC,
    spacing_pct=0.01,
    spacing_abs=50.0,
    num_grids_up=5,
    num_grids_down=5,
    price_step=0.01,
)
centre_price = Decimal("30000.00")  # <- Decimal type
```

**When:**
```python
levels = calc.calculate(centre_price)
```

**Then:**
- Returns list of GridLevel objects
- All prices are Decimal (not float)
- No TypeError raised

**Expected Failure Mode:**
```
TypeError: unsupported operand type(s) for *: 'Decimal' and 'float'
# Inside _price() or _quantize(); float/Decimal mixing
```

---

#### Test 2: `test_calculate_rejects_float_centre_price_or_converts_gracefully`

**Purpose:** Verify that passing float centre_price either:
  a) Raises clear TypeError with guidance, OR
  b) Auto-converts to Decimal

**Given:**
```python
calc = GridCalculator(spacing_pct=0.01, ...)
centre_price = 30000.0  # <- float, not Decimal
```

**When:**
```python
try:
    levels = calc.calculate(centre_price)
except TypeError as e:
    error_msg = str(e)
```

**Then - Option A (Strict):**
```python
assert "Decimal" in error_msg or "float" in error_msg
# Clear error message guiding user to use Decimal
```

**Then - Option B (Auto-convert):**
```python
assert len(levels) > 0
assert all(isinstance(lvl.price, Decimal) for lvl in levels)
```

**Expected Failure Mode:**
```
TypeError: unsupported operand type(s) for /: 'Decimal' and 'float'
# No auto-conversion; ambiguous error
```

---

#### Test 3: `test_geometric_calculation_with_decimal_preserves_precision`

**Purpose:** Verify that Decimal arithmetic preserves precision better than float.

**Given:**
```python
calc_decimal = GridCalculator(
    spacing_pct=0.01,
    price_step=Decimal("0.01"),
    num_grids_up=10,
)
calc_float = GridCalculator(
    spacing_pct=0.01,
    price_step=0.01,
    num_grids_up=10,
)
centre = Decimal("30000.00")
```

**When:**
```python
levels_decimal = calc_decimal.calculate(centre)
# (calc_float test would fail or require float conversion)
```

**Then:**
- All prices in levels_decimal are Decimal
- Quantization operations round consistently
- No floating-point accumulation errors over 10 levels

**Expected Failure Mode:**
```
TypeError: cannot convert to Decimal from float directly
# Or: precision loss due to float arithmetic in intermediate steps
```

---

## REGRESSION 3: Fill-Order Sequencing Bug

### Problem Summary
In `bot_v2/bot.py:_process_grid_orchestrator_tick()`:

1. `check_fills()` closes order in OrderStateManager → removed from lookup
2. Loop tries to find order in `get_open_orders()` → **no longer there!**
3. `orchestrator.handle_fill()` never called
4. Counter-order not placed

**Root cause:** Order closed (state changed) BEFORE orchestrator reads it.

**Impact:** Grid breaks after first fill. No counter-orders placed.

### Test File Targets
- **Primary:** `tests/test_integration_smoke.py` (add new IntegrationTest class)
- **Secondary:** `tests/test_fill_handler.py` (verify loop logic with real OMS)

### Test Class: `TestFillSequencingBugRobustness`

#### Test 1: `test_orchestrator_handle_fill_called_after_fill_detected`

**Purpose:** Verify that orchestrator.handle_fill() is called for each detected fill.

**Given:**
- GridBot with mocked Orchestrator
- 1 open BUY order at 29900
- Exchange reports 0 open orders (fill detected)

**When:**
```python
await bot._process_grid_orchestrator_tick(
    symbol="BTC/USDT",
    orchestrator=mock_orchestrator,
    ohlcv=ohlcv_dataframe  # Latest close at 30000
)
```

**Then:**
- `mock_orchestrator.handle_fill()` called exactly once
- Arguments: (order_id, fill_price, fill_qty, side)
- Counter-order placed subsequently

**Expected Failure Mode:**
```
AssertionError: Expected handle_fill to be called once, but called 0 times
# Because order closed before orchestrator.handle_fill() loop
```

---

#### Test 2: `test_handle_fill_receives_open_order_record_not_closed`

**Purpose:** Verify that the order record passed to handle_fill() is still OPEN (before counter-order logic).

**Given:**
- BUY order at 29900 in OPEN state
- Exchange reports fill detected

**When:**
```python
# Capture the order_record at the time handle_fill is called
captured_record = None
async def mock_handle_fill(oid, price, qty, side):
    # At this point, order should still be queryable
    for rec in bot.order_state_manager.get_open_orders():
        if rec.exchange_order_id == oid:
            nonlocal captured_record
            captured_record = rec
            break

mock_orchestrator.handle_fill.side_effect = mock_handle_fill
await bot._process_grid_orchestrator_tick(...)
```

**Then:**
- `captured_record` is not None (order found in get_open_orders)
- `captured_record.status == OrderStatus.OPEN` (at time of call)
- Order only marked as FILLED/CLOSED after orchestrator.handle_fill() completes

**Expected Failure Mode:**
```
AssertionError: captured_record is None
# Order already closed/removed from get_open_orders before handle_fill call
```

---

#### Test 3: `test_multiple_fills_in_same_tick_all_trigger_handle_fill`

**Purpose:** Verify that if multiple fills detected in single tick, all trigger handle_fill().

**Given:**
- 3 open orders at [29900, 30000, 30100]
- Exchange reports only 1 open (at 30000)
- 2 fills detected (at 29900, 30100)

**When:**
```python
handle_fill_calls = []
async def capture_handle_fill(oid, price, qty, side):
    handle_fill_calls.append((oid, price, qty, side))

mock_orchestrator.handle_fill.side_effect = capture_handle_fill
await bot._process_grid_orchestrator_tick(...)
```

**Then:**
- `len(handle_fill_calls) == 2`
- Both order IDs in arguments
- No fills skipped

**Expected Failure Mode:**
```
AssertionError: Expected 2 handle_fill calls, but got 0 or 1
# Race condition: orders closed before orchestrator loop runs
```

---

#### Test 4: `test_counter_order_placed_after_orchestrator_handle_fill_completes`

**Purpose:** Verify sequencing: orchestrator.handle_fill() completes → counter-order placed.

**Given:**
- 1 BUY order at 29900 (fill detected)
- Mock orchestrator.handle_fill() with async delay
- Mock OrderManager.deploy_grid() to track when called

**When:**
```python
handle_fill_completed = asyncio.Event()
async def delayed_handle_fill(oid, price, qty, side):
    await asyncio.sleep(0.1)  # Simulate async work
    handle_fill_completed.set()

mock_orchestrator.handle_fill.side_effect = delayed_handle_fill
bot.order_manager.deploy_grid = AsyncMock()

await bot._process_grid_orchestrator_tick(...)

# After tick completes, orchestrator.handle_fill should be done
assert handle_fill_completed.is_set()
```

**Then:**
- `handle_fill_completed` is set
- `OrderManager.deploy_grid()` called (counter-order placed)
- OrderManager.deploy_grid called AFTER orchestrator.handle_fill completes

**Expected Failure Mode:**
```
AssertionError: deploy_grid() never called
# Or: deploy_grid called but order still in state as OPEN (not properly marked FILLED)
```

---

#### Test 5: `test_fill_detection_does_not_skip_open_orders_due_to_state_mutation`

**Purpose:** Verify that the loop detecting fills doesn't skip orders due to concurrent state changes.

**Given:**
- 5 open orders
- Exchange reports 3 still open (2 filled)
- During loop, OrderStateManager state is being mutated

**When:**
```python
detected_fills = []
for rec in bot.order_state_manager.get_open_orders():
    # Simulate concurrent state mutation
    if some_condition:
        # Order status changes mid-loop
        bot.order_state_manager.update_order_status(...)

await bot._process_grid_orchestrator_tick(...)
```

**Then:**
- All 2 fills detected (not skipped)
- No "ConcurrentModificationException" or split-brain state
- Consistent view of orders throughout tick

**Expected Failure Mode:**
```
AssertionError: Expected 2 fills detected, but got 1
# Iterator invalidated or state changes missed due to concurrent mutations
```

---

## REGRESSION 4: API Drift in Execution Tests (clear_order_tracking)

### Problem Summary
`bot_v2/execution/order_manager.py` has a `clear_order_tracking()` method. Tests expect synchronous clear, but implementation is (or should be) async.

**Mismatch:** Tests call `order_manager.clear_order_tracking()` without `await`, but method is `async def`.

**Impact:** Order state never cleared. Stale orders bleed between test cases.

### Test File Targets
- **Primary:** `bot_v2/tests/test_execution.py` (existing file, add test class)

### Test Class: `TestClearOrderTrackingAPIConsistency`

#### Test 1: `test_clear_order_tracking_is_async`

**Purpose:** Verify that clear_order_tracking() is truly async and requires await.

**Given:**
```python
order_manager = OrderManager(client=mock_exchange, settings=test_settings)
```

**When:**
```python
# Incorrect: no await
result = order_manager.clear_order_tracking()

# Check if result is a coroutine
import inspect
is_coroutine = inspect.iscoroutine(result)
```

**Then:**
```python
assert is_coroutine, "clear_order_tracking must be async"
# Clean up the coroutine
await result
```

**Expected Failure Mode:**
```
AssertionError: clear_order_tracking must be async
# But it returns None synchronously, not a coroutine
```

---

#### Test 2: `test_await_clear_order_tracking_clears_all_state`

**Purpose:** Verify that awaiting clear_order_tracking() actually clears internal state.

**Given:**
```python
order_manager = OrderManager(...)
# Deploy some orders
await order_manager.deploy_grid(levels)
assert order_manager.open_order_count > 0
```

**When:**
```python
await order_manager.clear_order_tracking()
```

**Then:**
```python
assert order_manager.open_order_count == 0
assert len(order_manager.all_records) == 0
assert len(order_manager._grid_map) == 0
```

**Expected Failure Mode:**
```
AssertionError: Expected open_order_count=0, but got 3
# Because clear_order_tracking not awaited; state never cleared
```

---

#### Test 3: `test_execution_tests_all_await_clear_order_tracking`

**Purpose:** Code review: ensure all test_execution.py tests await clear_order_tracking().

**Given:**
- Parsed test file `bot_v2/tests/test_execution.py`
- Regex search for all calls to `clear_order_tracking`

**When:**
```python
import ast
import re

with open("bot_v2/tests/test_execution.py") as f:
    content = f.read()
    
# Find all clear_order_tracking calls
calls = re.findall(
    r'(?:await\s+)?[\w.]*clear_order_tracking\s*\(',
    content
)
```

**Then:**
```python
for call in calls:
    assert "await" in call, f"Found non-awaited call: {call}"
```

**Expected Failure Mode:**
```
AssertionError: Found non-awaited call: order_manager.clear_order_tracking()
# Test code has API drift; incorrectly calls without await
```

---

## Test Execution Commands

### Run All Regressions
```bash
pytest tests/test_fill_handler.py \
       tests/test_grid_calculator.py \
       tests/test_integration_smoke.py \
       bot_v2/tests/test_execution.py \
       -v --tb=short 2>&1 | tee test_results.log
```

### Run by Regression

#### Regression 1: Missing await on OrderStateManager
```bash
# New tests only
pytest tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness -v

# Run with verbose logging to see race conditions
pytest tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness \
       -v -s --log-level=DEBUG
```

#### Regression 2: Decimal/Float TypeError
```bash
# All GridCalculator tests
pytest tests/test_grid_calculator.py -v

# New Decimal-specific tests
pytest tests/test_grid_calculator.py::TestGridCalculatorDecimalTypeConsistency -v

# Run with type checking (if using mypy plugin)
pytest tests/test_grid_calculator.py -v --mypy
```

#### Regression 3: Fill-Order Sequencing
```bash
# New integration tests
pytest tests/test_integration_smoke.py::TestFillSequencingBugRobustness -v

# Run with timing to catch race conditions
pytest tests/test_integration_smoke.py::TestFillSequencingBugRobustness \
       -v -s --durations=0
```

#### Regression 4: API Drift in Execution
```bash
# Execution tests only
pytest bot_v2/tests/test_execution.py::TestClearOrderTrackingAPIConsistency -v

# Verify all execution tests
pytest bot_v2/tests/test_execution.py -v
```

### Coverage Report
```bash
pytest tests/test_fill_handler.py \
       tests/test_grid_calculator.py \
       tests/test_integration_smoke.py \
       bot_v2/tests/test_execution.py \
       --cov=src --cov=bot_v2 --cov-report=html --cov-report=term-missing
```

---

## Test Implementation Checklist

### Setup Phase
- [ ] Create test class `TestFillHandlerAwaitCompleteness` in `tests/test_fill_handler.py`
- [ ] Create test class `TestGridCalculatorDecimalTypeConsistency` in `tests/test_grid_calculator.py`
- [ ] Create test class `TestFillSequencingBugRobustness` in `tests/test_integration_smoke.py`
- [ ] Create test class `TestClearOrderTrackingAPIConsistency` in `bot_v2/tests/test_execution.py`

### For Each Test
- [ ] Write Given/When/Then scenario in docstring
- [ ] Create minimal fixtures (don't over-mock)
- [ ] Assert specific failure mode matches expected error
- [ ] Run test in isolation: `pytest path/to/test.py::TestClass::test_method -v`
- [ ] Verify test fails for RIGHT reason (not syntax/import error)

### Verification
- [ ] All 15 tests fail with expected errors
- [ ] No tests pass before implementation
- [ ] Each test clearly describes one regression aspect
- [ ] Test names reference regression and behavior

---

## Expected Test Results (Before Fixes)

```
FAILED tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness::test_await_applied_to_order_state_update_on_fill_detection
FAILED tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness::test_state_update_on_fill_does_not_race_with_next_poll_cycle
FAILED tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness::test_counter_order_placement_uses_correct_filled_status
FAILED tests/test_fill_handler.py::TestFillHandlerAwaitCompleteness::test_multiple_fills_in_same_cycle_all_awaited

FAILED tests/test_grid_calculator.py::TestGridCalculatorDecimalTypeConsistency::test_calculate_accepts_decimal_centre_price
FAILED tests/test_grid_calculator.py::TestGridCalculatorDecimalTypeConsistency::test_calculate_rejects_float_centre_price_or_converts_gracefully
FAILED tests/test_grid_calculator.py::TestGridCalculatorDecimalTypeConsistency::test_geometric_calculation_with_decimal_preserves_precision

FAILED tests/test_integration_smoke.py::TestFillSequencingBugRobustness::test_orchestrator_handle_fill_called_after_fill_detected
FAILED tests/test_integration_smoke.py::TestFillSequencingBugRobustness::test_handle_fill_receives_open_order_record_not_closed
FAILED tests/test_integration_smoke.py::TestFillSequencingBugRobustness::test_multiple_fills_in_same_tick_all_trigger_handle_fill
FAILED tests/test_integration_smoke.py::TestFillSequencingBugRobustness::test_counter_order_placed_after_orchestrator_handle_fill_completes
FAILED tests/test_integration_smoke.py::TestFillSequencingBugRobustness::test_fill_detection_does_not_skip_open_orders_due_to_state_mutation

FAILED bot_v2/tests/test_execution.py::TestClearOrderTrackingAPIConsistency::test_clear_order_tracking_is_async
FAILED bot_v2/tests/test_execution.py::TestClearOrderTrackingAPIConsistency::test_await_clear_order_tracking_clears_all_state
FAILED bot_v2/tests/test_execution.py::TestClearOrderTrackingAPIConsistency::test_execution_tests_all_await_clear_order_tracking

==================== 15 failed in X.XXs ====================
```

---

## Next Steps (After Red Phase)

1. **GREEN Phase:** Implement minimal code to make tests pass (without fixing root causes)
2. **REFACTOR Phase:** Apply proper async/await fixes, type consistency, and sequencing corrections
3. **VERIFY Phase:** All tests pass; no new regressions introduced

---

## References

- **Session Memory:** `/memories/session/grid_bot_code_review_findings.md`
- **Bot Architecture:** `.github/copilot-instructions.md`
- **Async Patterns:** `AGENTS.md` (async-first rules)
- **Existing Tests:** `tests/conftest.py` (fixtures + mocking patterns)
