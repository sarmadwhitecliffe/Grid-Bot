---
description: 'Template for invoking Async Sheriff agent to validate async/await patterns, rate limiting, and retry logic'
applies-to: 'Grid Bot async module validation'
---

# Async Pattern Validation Workflow Prompt

Use this template to invoke the Async Sheriff agent to validate async/await patterns in Grid Bot modules.

## When to Use

- **After implementing new async module** — Immediate validation after creation
- **After modifying exchange client** — Any changes to exchange interaction code
- **After adding new ccxt calls** — Validate proper async patterns
- **Pre-phase completion** — Validate all async patterns before marking phase done
- **When debugging async issues** — Identify deadlocks, missing awaits, race conditions

## Prompt Template: Single Module Validation

```
@async-sheriff

Validate async patterns in [MODULE NAME] for Grid Bot compliance.

Module to audit: [FILE PATH]

Check for:
1. ✅ All ccxt imports use `ccxt.async_support`
2. ✅ `enableRateLimit=True` on all exchange instances
3. ✅ All async exchange methods are properly `await`-ed
4. ✅ Retry logic follows Grid Bot pattern: exponential backoff [1, 2, 5] seconds, max 3 attempts
5. ✅ Exception handling catches only `NetworkError` and `RequestTimeout`
6. ✅ No blocking I/O operations (no `open()`, `time.sleep()`, `requests.*`)
7. ✅ Proper async context managers (`async with`)
8. ✅ Timeouts on all network calls

Report any anti-patterns with file:line references.
```

## Prompt Template: Full Phase Async Audit

```
@async-sheriff

Comprehensive async pattern audit for Grid Bot Phase [N] before completion.

Modules to audit:
- [LIST ALL ASYNC MODULES IN PHASE]

**Grid Bot Async Requirements:**
- Import: `import ccxt.async_support as ccxt`
- Rate limiting: `enableRateLimit=True` (mandatory)
- Retry pattern: Exponential backoff [1, 2, 5] seconds, catch only NetworkError/RequestTimeout
- Async I/O: All file operations use `aiofiles` or atomic sync writes
- Context managers: `async with` for exchange connections
- Timeouts: `asyncio.wait_for()` on all network calls

**Scan for anti-patterns:**
- ❌ Synchronous ccxt imports
- ❌ Missing await on async functions
- ❌ Broad exception catches (`except Exception`)
- ❌ Fixed retry delays (no exponential backoff)
- ❌ Blocking I/O in async functions
- ❌ Missing rate limiting
- ❌ No timeout enforcement

Provide detailed report with pass/fail for each module.
```

## Prompt Template: Exchange Client Validation

```
@async-sheriff

Critical async pattern validation for Grid Bot exchange client.

**This is the CORE async module — strict validation required.**

File: src/exchange/exchange_client.py

**Mandatory patterns:**
1. ✅ Import: `import ccxt.async_support as ccxt`
2. ✅ Constructor includes: `'enableRateLimit': True`
3. ✅ All methods that call exchange are `async def`
4. ✅ All exchange calls wrapped in retry logic with exponential backoff
5. ✅ Try/except catches ONLY `ccxt.NetworkError` and `ccxt.RequestTimeout`
6. ✅ Retry delays: [1, 2, 5] seconds, max 3 attempts
7. ✅ Structured logging on retry attempts (log attempt number, delay, error)
8. ✅ Proper cleanup: `await exchange.close()` in finally block or async context manager

**Check method signatures:**
- `load_markets()` → async
- `fetch_ticker()` → async
- `fetch_ohlcv()` → async
- `create_limit_order()` → async
- `cancel_order()` → async
- `fetch_open_orders()` → async
- `fetch_order()` → async

**Verify retry logic example:**
```python
delays = [1, 2, 5]
for attempt, delay in enumerate(delays, start=1):
    try:
        result = await exchange.some_method(...)
        return result
    except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
        if attempt == len(delays):
            raise
        log.warning(f"Retry {attempt}/{len(delays)} after {delay}s", error=str(e))
        await asyncio.sleep(delay)
```

**CRITICAL GATE:** Exchange client must pass ALL checks before phase completion.
```

## Prompt Template: Debugging Async Issues

```
@async-sheriff

Debug async issue in Grid Bot [MODULE NAME].

**Symptom:** [DESCRIBE ISSUE: deadlock, timeout, missing await, etc.]

File: [FILE PATH]
Function: [FUNCTION NAME if known]

**Investigate:**
1. Check for missing `await` on async calls
2. Identify blocking operations in async context
3. Check for improper exception handling
4. Verify proper cleanup (no leaked connections)
5. Look for race conditions on shared state
6. Check signal handler async safety

**Context:**
[PASTE RELEVANT CODE SECTION OR STACK TRACE]

Provide analysis with specific recommendations to fix the issue.
```

## Example Usage

### Example 1: Validate new exchange client
```
@async-sheriff

Validate async patterns in newly implemented exchange client.

File: src/exchange/exchange_client.py

This is Phase 1 critical module. Strict validation required.

Check all mandatory patterns:
- ccxt.async_support import
- enableRateLimit=True
- Exponential backoff retry [1,2,5]
- Specific exception handling
- All methods properly async

Fail audit if any pattern violation found.
```

### Example 2: Phase 1 async audit
```
@async-sheriff

Full async audit for Phase 1 completion.

Modules:
- src/exchange/exchange_client.py
- src/data/price_feed.py

Verify:
1. All ccxt calls are async
2. Rate limiting everywhere
3. Retry logic correct
4. No blocking operations
5. Proper error handling

Phase gate — must pass to proceed.
```

### Example 3: Debug timeout issue
```
@async-sheriff

Debug timeout in price feed OHLCV fetch.

File: src/data/price_feed.py
Method: get_ohlcv_dataframe()

Symptom: Intermittent timeouts when fetching historical data.

Check for:
- Missing timeout on fetch_ohlcv
- Missing retry logic
- Improper exception handling

Code section:
```python
async def get_ohlcv_dataframe(self):
    ohlcv = await self._exchange.fetch_ohlcv(...)
    return pd.DataFrame(ohlcv)
```

Recommend fix with proper async pattern.
```

---

## Async Pattern Checklist

Use this checklist with Async Sheriff:

### Import & Setup
- [ ] Import uses `ccxt.async_support`
- [ ] Exchange instance has `enableRateLimit=True`
- [ ] All async dependencies imported correctly

### Async/Await Usage
- [ ] All exchange methods use `async def`
- [ ] All exchange calls have `await`
- [ ] No mixing of sync/async code
- [ ] Proper async context managers (`async with`)

### Retry Logic (Grid Bot Pattern)
- [ ] Exponential backoff: [1, 2, 5] seconds
- [ ] Max 3 retry attempts
- [ ] Catches only NetworkError and RequestTimeout
- [ ] Logs retry attempts with context

### Error Handling
- [ ] No bare `except:` clauses
- [ ] No catching `Exception` (too broad)
- [ ] Specific exception types only
- [ ] Proper error propagation

### I/O Operations
- [ ] No blocking file operations (`open()`)
- [ ] Use `aiofiles` for async file I/O
- [ ] No `time.sleep()` (use `asyncio.sleep()`)
- [ ] No synchronous HTTP (`requests` → use `aiohttp`)

### Network Safety
- [ ] All network calls wrapped in `asyncio.wait_for(timeout=...)`
- [ ] Proper connection cleanup
- [ ] No leaked exchange connections
- [ ] Signal handlers are async-safe

### Concurrency
- [ ] Limited concurrent requests (use Semaphore if needed)
- [ ] No race conditions on shared state
- [ ] Proper use of asyncio.Lock for critical sections
- [ ] Queue operations are non-blocking

---

**Remember:** Async bugs are notoriously hard to debug. Validate patterns early and often to prevent production incidents.
