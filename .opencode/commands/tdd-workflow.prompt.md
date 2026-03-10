---
description: "Invoke TDD agents for Red-Green-Refactor development cycle"
agent: "@tdd-red"
tools: ["read", "write", "edit", "bash", "grep", "glob"]
applies-to: "Grid Bot feature and bug fix development with test-driven approach"
---

# TDD Workflow for Grid Bot

Initiate Test-Driven Development cycle for Grid Bot features and bug fixes.

## TDD Phase Selection

**TDD Phase**: `${input:TDDPhase|Red - Write Failing Test,Green - Make Test Pass,Refactor - Improve Quality}`

## Feature Development

For new features, provide:
- **Feature Name**: `${input:FeatureName}`
- **Phase Number**: `${input:PhaseNumber}`
- **Module Name**: `${input:ModuleName}`
- **Test File**: `${input:TestFile}`
- **Expected Behavior**: `${input:ExpectedBehavior}`

## Bug Fixes

For bug reproduction and fixes, provide:
- **Bug Description**: `${input:BugDescription}`
- **Module With Bug**: `${input:ModuleWithBug}`
- **Expected vs Actual Behavior**: `${input:ExpectedVsActual}`

## TDD Cycle Process

```
TDD Red → TDD Green → TDD Refactor → (repeat for next feature)
   ↓           ↓              ↓
Write test  Make it pass  Improve quality
(fails)     (minimal)     (keep green)
```

**Grid Bot Test Conventions:**
- Use pytest + pytest-asyncio
- Mock all ccxt exchange calls with pytest-mock  
- Use fixtures from conftest.py (base_settings, mock_exchange, sample_ohlcv)
- Follow AAA pattern (Arrange, Act, Assert)
- Test file mirrors src/ structure

**Remember:** Follow Red-Green-Refactor religiously for predictable, test-covered code.

---

## Phase 1: TDD Red — Write Failing Test

### Prompt Template: New Feature Test

```
@tdd-red

Write failing tests for [FEATURE NAME] based on Grid Bot Phase [N] requirements.

**GitHub Issue Context:**
Issue: [ISSUE_NUMBER or N/A if from phase plan]
Feature: [FEATURE DESCRIPTION]

**Requirements from phase plan:**
[PASTE RELEVANT REQUIREMENTS FROM plan/feature-grid-bot-phase*.md]

**Expected behavior:**
1. [DESCRIBE FIRST BEHAVIOR TO TEST]
2. [DESCRIBE SECOND BEHAVIOR TO TEST]
3. [DESCRIBE EDGE CASES]

**Test file:** tests/test_[module_name].py
**Module under test:** src/[layer]/[module_name].py

**Grid Bot test conventions:**
- Use pytest + pytest-asyncio
- Mock all ccxt exchange calls with pytest-mock
- Use fixtures from conftest.py (base_settings, mock_exchange, sample_ohlcv)
- Follow AAA pattern (Arrange, Act, Assert)
- Test file mirrors src/ structure

Write ONE failing test for the first requirement. Verify it fails for the right reason.
```

### Prompt Template: Bug Reproduction Test

```
@tdd-red

Write failing test to reproduce bug in Grid Bot [MODULE NAME].

**Bug description:**
[DESCRIBE THE BUG]

**Expected behavior:**
[WHAT SHOULD HAPPEN]

**Actual behavior:**
[WHAT CURRENTLY HAPPENS]

**Steps to reproduce:**
1. [STEP 1]
2. [STEP 2]
3. [OBSERVE BUG]

**Test file:** tests/test_[module].py
**Module with bug:** src/[layer]/[module].py

Write a test that:
1. Reproduces the bug (test should fail initially)
2. Clearly shows expected vs actual behavior
3. Uses Grid Bot test conventions (pytest-asyncio, mocking)

Verify the test fails before proceeding to TDD Green phase.
```

### Example: TDD Red for Grid Calculator

```
@tdd-red

Write failing test for geometric grid level generation in Grid Bot.

Feature: Calculate grid levels for geometric spacing strategy.

Requirements (from Phase 2 plan):
- Calculate N levels above and below center price
- Use GRID_SPACING_PCT for percentage gaps
- Quantize prices to exchange price_step
- Alternate buy/sell sides
- Filter levels outside LOWER_BOUND and UPPER_BOUND

Test file: tests/test_grid_calculator.py
Module: src/strategy/grid_calculator.py

Write test for: "Calculate 5 levels above center with 1% spacing"

Expected:
- Center: 30000
- Spacing: 1% (0.01)
- Levels: [30000, 30300, 30603, 30909.03, 31218.12]
- All levels quantized to price_step=0.01

Use Grid Bot test pattern:
- Fixture for GridCalculator with test settings
- AAA structure
- Assert exact level values
- Assert buy/sell alternation

Write ONLY this test. Verify it fails.
```

---

## Phase 2: TDD Green — Make Test Pass

### Prompt Template: Minimal Implementation

```
@tdd-green

Implement minimal code to make [TEST NAME] pass in Grid Bot.

**Test file:** tests/test_[module].py
**Test that's failing:** test_[specific_test_name]

**Implementation file:** src/[layer]/[module].py

**Grid Bot mandatory conventions:**
- Async-first (if exchange I/O involved)
- No hardcoded values (use settings)
- Pydantic/dataclass DTOs for data transfer
- Structured logging at layer boundaries

**Instructions:**
1. Read the failing test to understand exactly what's needed
2. Implement ONLY enough code to make THIS test pass
3. Don't over-engineer or anticipate future requirements
4. Keep implementation simple and direct
5. Add structured logging if crossing layer boundaries
6. Run test to verify it passes
7. Don't modify the test itself

Focus on: **Green bar quickly, code quality comes in Refactor phase.**
```

### Prompt Template: Bug Fix Implementation

```
@tdd-green

Fix bug to make reproduction test pass in Grid Bot [MODULE].

**Test file:** tests/test_[module].py
**Failing test:** test_[bug_name]

**Bug location:** src/[layer]/[module].py
**Bug description:** [DESCRIBE BUG]

**Fix constraints:**
- Fix ONLY this bug (don't refactor other code yet)
- Minimal change to make test pass
- Must not break existing tests
- Follow Grid Bot conventions (async, retry logic, etc.)

Implement fix, then:
1. Verify bug reproduction test now passes
2. Run full test suite to ensure no regressions
3. Report test results

Code quality improvements will happen in TDD Refactor phase.
```

### Example: TDD Green for Grid Calculator

```
@tdd-green

Implement geometric level calculation to pass test_geometric_levels_above_center.

Test: tests/test_grid_calculator.py::test_geometric_levels_above_center
Module: src/strategy/grid_calculator.py

Test expects:
- Method: calculate_levels(center_price=30000)
- Return: List[GridLevel] with 5 levels above center
- Spacing: 1% geometric (multiply by 1.01)
- Quantization: round to price_step=0.01

Minimal implementation:
1. Create GridLevel dataclass (price, side, size)
2. Implement calculate_levels() method
3. Generate geometric levels: price * (1 + spacing)^n
4. Quantize to price_step
5. Return list of GridLevel objects

Don't add:
- Full config loading (use test settings)
- Complex validation (comes in Refactor)
- Boundary filtering (separate test)

Just make THIS test pass.
```

---

## Phase 3: TDD Refactor — Improve Quality

### Prompt Template: Code Quality Improvement

```
@tdd-refactor

Improve code quality in [MODULE NAME] while keeping all tests green.

**Module:** src/[layer]/[module].py
**Tests:** tests/test_[module].py

**All tests must stay green during refactoring.**

**Grid Bot refactoring priorities:**
1. **Security hardening:**
   - No hardcoded secrets (use settings)
   - Proper error handling (specific exceptions)
   - Input validation on public methods
   - Secure logging (mask sensitive data)

2. **Grid Bot conventions:**
   - Async/await patterns correct
   - Exponential backoff retry [1,2,5] on network calls
   - Rate limiting (enableRateLimit=True)
   - Structured logging with context
   - Pydantic/dataclass DTOs

3. **Code quality:**
   - Remove duplication
   - Extract complex logic to private methods
   - Improve variable/method names
   - Add docstrings (PEP 257)
   - Type hints on all functions

4. **Performance:**
   - Use async patterns efficiently
   - Cache where appropriate
   - Avoid unnecessary computation

**Instructions:**
1. Make ONE improvement at a time
2. Run tests after EACH change
3. If tests break, revert and try different approach
4. Continue until code is clean and all best practices applied

Refactor incrementally. Keep tests green.
```

### Prompt Template: Security Refactoring

```
@tdd-refactor

Apply security best practices to [MODULE NAME] while maintaining test coverage.

**Module:** src/[layer]/[module].py
**Security focus:** [API key safety / Order placement guards / State persistence security]

**Security checklist for Grid Bot:**
- [ ] No hardcoded API keys or secrets
- [ ] All secrets loaded from environment variables
- [ ] Input validation on all public methods
- [ ] Specific exception handling (no bare except)
- [ ] No sensitive data in log messages
- [ ] Order size validation before placement
- [ ] Rate limiting enforced
- [ ] Retry logic catches only NetworkError/RequestTimeout
- [ ] Atomic state writes (temp-then-rename)
- [ ] Proper error messages (no info disclosure)

**Instructions:**
1. Run security check before refactoring
2. Apply fixes one at a time
3. Run tests after each security improvement
4. Add security-focused unit tests if gaps found
5. Verify all tests remain green

**Remember:** This is a trading bot handling real funds. Security is critical.
```

### Example: TDD Refactor for Grid Calculator

```
@tdd-refactor

Refactor grid calculator after passing initial tests.

Module: src/strategy/grid_calculator.py
Tests: tests/test_grid_calculator.py

Current state: Tests pass but code needs improvement.

**Refactoring goals:**
1. Extract level generation logic to private methods
2. Add comprehensive docstrings
3. Add type hints on all methods
4. Improve variable names (e.g., `n` → `num_levels`)
5. Add input validation (spacing > 0, num_grids > 0)
6. Optimize quantization logic
7. Add structured logging for level calculation

**Grid Bot specific:**
- Ensure function remains stateless (no instance state)
- All params from settings, not hardcoded
- Log level count and price range
- Add comment explaining geometric formula

Apply refactorings incrementally. Run tests after each change.

All tests must remain green throughout refactoring.
```

---

## Full TDD Workflow Example

### Complete Example: Implement Regime Detector

```markdown
## Step 1: TDD Red

@tdd-red

Write failing test for ADX regime detection in Grid Bot.

Feature: Detect market regime (RANGING vs TRENDING) using ADX indicator.

Requirements (Phase 2):
- Calculate ADX(14) from OHLCV DataFrame
- Regime is RANGING if ADX < ADX_THRESHOLD (default 25)
- Regime is TRENDING if ADX >= ADX_THRESHOLD
- Return RegimeInfo dataclass with regime type and ADX value

Test: tests/test_regime_detector.py::test_adx_below_threshold_is_ranging
Module: src/strategy/regime_detector.py

Expected:
- Input: sample OHLCV with low volatility (ADX=20)
- ADX_THRESHOLD: 25
- Output: RegimeInfo(regime=MarketRegime.RANGING, adx_value=20.0)

Use ta-lib ADXIndicator. Mock OHLCV from conftest fixture.

Write test. Verify it fails.

---

## Step 2: TDD Green

@tdd-green

Implement ADX regime detection to pass test_adx_below_threshold_is_ranging.

Test: tests/test_regime_detector.py::test_adx_below_threshold_is_ranging
Module: src/strategy/regime_detector.py

Minimal implementation:
1. Create RegimeInfo dataclass
2. Create detect_regime(ohlcv, adx_threshold) function
3. Calculate ADX using ta.trend.ADXIndicator
4. Compare with threshold
5. Return RegimeInfo

Don't add:
- Bollinger Band logic (separate test)
- Complex validation (comes later)
- Caching (not needed yet)

Just pass THIS test with simplest code.

---

## Step 3: TDD Refactor

@tdd-refactor

Refactor regime detector after basic ADX test passes.

Module: src/strategy/regime_detector.py

Improvements needed:
1. Add docstring explaining ADX logic
2. Add type hints
3. Add input validation (ohlcv not empty)
4. Extract ADX calculation to _calculate_adx() helper
5. Add structured logging (regime switched, ADX value)
6. Improve error handling for invalid data

Keep all tests green. Refactor incrementally.
```

---

## TDD Anti-Patterns to Avoid

### ❌ DON'T: Write multiple tests at once
```
# Wrong: Writing all tests upfront
test_geometric_levels()
test_arithmetic_levels()
test_boundary_filtering()
test_price_quantization()
# Then implementing all at once
```

### ✅ DO: One test at a time (Red-Green-Refactor cycle)
```
# Correct: Iterative TDD
1. Write test_geometric_levels() → RED
2. Implement geometric logic → GREEN
3. Refactor geometric code → REFACTOR
4. Write test_arithmetic_levels() → RED
5. Implement arithmetic logic → GREEN
6. Refactor arithmetic code → REFACTOR
(repeat...)
```

### ❌ DON'T: Over-engineer in Green phase
```python
# Wrong: Adding complexity in Green phase
def calculate_levels(center):
    # Added caching, validation, logging, optimization
    # before test even passes
```

### ✅ DO: Minimal implementation in Green, improve in Refactor
```python
# Correct: Simple implementation in Green
def calculate_levels(center):
    return [center * 1.01 * i for i in range(5)]

# Then refactor for quality
def calculate_levels(center: float) -> List[GridLevel]:
    """Calculate geometric grid levels above center price."""
    return [GridLevel(center * 1.01 ** i, ...) for i in range(5)]
```

---

## TDD Workflow Checklist

### Red Phase
- [ ] Test clearly describes expected behavior
- [ ] Test uses Grid Bot conventions (pytest-asyncio, mocking)
- [ ] Test fails for the right reason (missing implementation)
- [ ] Test name describes behavior, not implementation
- [ ] No production code written yet

### Green Phase
- [ ] Minimal code to pass test
- [ ] Test now passes (green bar)
- [ ] No code beyond what's needed
- [ ] All existing tests still pass
- [ ] Test itself not modified

### Refactor Phase
- [ ] Code quality improved
- [ ] Security best practices applied
- [ ] Grid Bot conventions followed
- [ ] All tests remain green
- [ ] Refactored incrementally (test after each change)

---

**Remember:** TDD is a discipline. Follow Red-Green-Refactor religiously for predictable, test-covered code.
