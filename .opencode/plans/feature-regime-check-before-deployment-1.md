---
goal: 'Pre-deployment regime check to prevent grid deployment in trending markets'
version: '1.0'
date_created: '2026-03-13'
last_updated: '2026-03-13'
owner: 'Antigravity'
status: 'In progress'
tags: ['bug', 'grid', 'regime', 'stability']
---

# Regime Check Before Grid Deployment

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

**Problem**: Grid orchestrator deploys orders, then immediately cancels them within ~30 seconds due to regime detector flagging the market as "TRENDING". This wastes resources and creates unnecessary order churn.

**Solution**: Check market regime BEFORE deploying the grid. If trending, skip deployment and schedule a retry.

---

## 1. Requirements & Constraints

| ID | Requirement |
|----|-------------|
| **REQ-001** | Regime must be checked BEFORE grid deployment in `start()` method |
| **REQ-002** | If regime is TRENDING, do NOT deploy grid — log warning and return |
| **REQ-003** | If regime is RANGING, proceed with normal grid deployment |
| **REQ-004** | Retry mechanism should exist to attempt deployment again later |
| **REQ-005** | Must fetch OHLCV data before regime detection (same as tick uses) |
| **CON-001** | Follow existing code patterns in `orchestrator.py` |
| **CON-002** | Use existing `RegimeDetector` class from `src/strategy/regime_detector.py` |
| **CON-003** | Do not change regime detector thresholds without explicit requirement |

---

## 2. Implementation Steps

### Phase 1: Pre-Deployment Regime Check

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Modify `orchestrator.start()` to fetch OHLCV before deployment | ✅ | 2026-03-13 |
| TASK-002 | Add regime detection check before `deploy_grid()` call | ✅ | 2026-03-13 |
| TASK-003 | Add early return/log when regime is TRENDING | ✅ | 2026-03-13 |
| TASK-004 | Add config for deployment retry interval | ✅ | 2026-03-13 |

### Phase 2: Retry Logic

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Add retry mechanism in `_maybe_restart_grid()` to check regime | ✅ | 2026-03-13 |
| TASK-006 | Add logging for skipped deployment due to trending | ✅ | 2026-03-13 |

### Phase 3: Testing

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | Verify existing tests still pass | ✅ | 2026-03-13 |
| TASK-008 | Manual test: confirm grid skipped when regime=TRENDING | | |

---

## 3. Alternatives

- **ALT-001**: Add grace period after deployment before regime check (rejected - wastes resources placing/cancelling orders)
- **ALT-002**: Adjust ADX/BB-width thresholds (rejected - masking root cause, not fixing it)
- **ALT-003**: Disable regime check entirely (rejected - loss of important risk protection)

---

## 4. Dependencies

- **DEP-001**: `src/strategy/regime_detector.py` - existing RegimeDetector class
- **DEP-002**: `bot_v2/grid/orchestrator.py` - main file to modify
- **DEP-003**: `config/settings.py` - for optional retry interval config

---

## 5. Files

- **FILE-001**: `bot_v2/grid/orchestrator.py` - Add pre-deployment regime check in `start()` method (lines 221-243)
- **FILE-002**: `bot_v2/grid/orchestrator.py` - Enhance `_maybe_restart_grid()` for retry logic
- **FILE-003**: `config/settings.py` - Optional: add `GRID_DEPLOYMENT_RETRY_INTERVAL_SECS` setting

---

## 6. Testing

- **TEST-001**: Unit test for `start()` when regime=TRENDING (grid should NOT deploy)
- **TEST-002**: Unit test for `start()` when regime=RANGING (grid should deploy)
- **TEST-003**: Integration test: verify no orders placed when market trending

---

## 7. Risks & Assumptions

- **RISK-001**: If OHLCV fetch fails, deployment might proceed without regime check — need to handle gracefully
- **RISK-002**: New retry loop could cause tight loop if market stays trending — ensure reasonable retry interval
- **ASSUMPTION-001**: Exchange client has `fetch_ohlcv` method available in `start()` context
- **ASSUMPTION-002**: Same timeframe config used in tick is appropriate for pre-check

---

## 8. Implementation Notes

### Current Flow (Problematic)
```
start()
  → recover_state()
  → if no orders: deploy_grid()  ← deploys first
  → tick() on next cycle
    → regime_detector.detect()  ← then checks, too late!
    → if TRENDING: stop()        ← cancels orders
```

### Proposed Flow (Fixed)
```
start()
  → recover_state()
  → if no orders:
    → fetch_ohlcv()
    → regime = detect()
    → if TRENDING:
      → log warning
      → return (NO deployment)
    → else (RANGING):
      → deploy_grid()
```

### Key Code Locations
- `bot_v2/grid/orchestrator.py:221-243` - `start()` method
- `bot_v2/grid/orchestrator.py:535` - regime detection in tick (for reference)
- `src/strategy/regime_detector.py:57` - `detect()` method
