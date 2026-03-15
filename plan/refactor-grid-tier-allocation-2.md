---
goal: Refactor tier-based capital allocation from percentage model to grid-density model using level_allocation_ratio
version: 2.0
date_created: 2026-03-15
last_updated: 2026-03-15
owner: Grid Bot Team
status: In progress
tags: [refactor, grid, capital-management, risk, tier-system]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan refactors the tier-based capital allocation system to support grid bot architecture. The current `capital_allocation_pct` model was designed for single-position UT Bot trades and is incompatible with multi-level grid systems. The new `level_allocation_ratio` model controls grid density (number of active levels) rather than capital percentage.

## Problem Statement

| Current Model | Issue |
|---------------|-------|
| `capital_allocation_pct: 30%` | Designed for single-position trades |
| Applies percentage to total capital | With $100 and 100 levels: $30/100 levels = $0.30/level (below $5 minimum) |
| `grid_order_size_quote = $100` | Fixed per-level size ignores capital constraints |
| Conflicting config values | strategy_configs.json has $100/level but only $100 capital |

## Proposed Solution

Replace `capital_allocation_pct` with `level_allocation_ratio` (0.50-1.00):
- Tier controls **percentage of configured grid levels to activate**
- `order_size_quote` calculated dynamically: `notional_capital / active_levels`
- Leverage multiplies effective capital: `notional = capital × effective_leverage`
- Higher tiers unlock more grid levels, same capital deployed more efficiently

## Example Calculation

```
Input: $100 capital, 10x leverage, 100 configured levels, STANDARD tier (0.80 ratio)
Calculation:
  effective_leverage = 10x × 1.0 = 10x
  notional_capital = $100 × 10 = $1000
  desired_levels = 100 × 0.80 = 80
  max_levels = floor($1000 / $5 minimum) = 200
  active_levels = min(80, 200) = 80
  order_size_quote = $1000 / 80 = $12.50/order
Output: 80 levels @ $12.50 each, 10x leverage
```

---

## 1. Requirements & Constraints

- **REQ-001**: Replace `capital_allocation_pct` with `level_allocation_ratio` in tier configuration
- **REQ-002**: Remove `grid_order_size_quote` and `capital_usage_percent` from strategy configs
- **REQ-003**: Calculate `order_size_quote` dynamically based on capital, leverage, and active levels
- **REQ-004**: Ensure `order_size_quote` never falls below exchange minimum ($5 USDT)
- **REQ-004B**: Ensure `order_size_quote` never exceeds exchange maximum cap ($100 USDT)
- **REQ-004C**: Support symbol-specific minimum notional overrides (Phase 1)
- **REQ-005**: Tier progression increases grid density (more active levels)
- **REQ-006**: Leverage multiplier applies to effective capital for grid sizing
- **REQ-007**: Support tier migration mid-session (grid adjustment on re-centre only)

- **SEC-001**: All financial calculations must use Decimal precision
- **SEC-002**: No grid orders should exceed exchange minimum notional
- **SEC-003**: Margin per level = order_size_quote / leverage must never exceed available margin

- **CON-001**: Minimum notional defaults to $5 USDT (configurable)
- **CON-001B**: Maximum notional defaults to $100 USDT (configurable)
- **CON-001C**: Symbol-specific minimum notional overrides supported via config
- **CON-002**: Each symbol starts with $100 initial capital
- **CON-003**: Capital grows/shrinks based on symbol performance
- **CON-004**: Configured grid levels vary by symbol (17-100 levels from optimization)

- **GUD-001**: Tier ratios: PROBATION=0.50, CONSERVATIVE=0.65, STANDARD=0.80, AGGRESSIVE=0.90, CHAMPION=1.00
- **GUD-002**: Round active_levels to even number for symmetric up/down split
- **GUD-003**: Log all parameter calculations at INFO level
- **GUD-004**: Minimum 10 grid levels regardless of tier
- **GUD-005**: Maximum $100 per order size (capped)
- **GUD-006**: Tier changes take effect at next re-centre (not immediate)

- **PAT-001**: Follow existing RiskTier dataclass pattern
- **PAT-002**: Use Decimal for all monetary values
- **PAT-003**: Maintain backward compatibility with existing tests

---

## 2. Implementation Steps

### Implementation Phase 1: Configuration Files

- GOAL-001: Update tier and strategy configuration files to new model

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Update `config/adaptive_risk_tiers.json`: remove `capital_allocation_pct`, `max_position_size_usd`; add `level_allocation_ratio` | | |
| TASK-002 | Update `config/strategy_configs.json`: remove `grid_order_size_quote`, `capital_usage_percent` from all symbols | | |
| TASK-003 | Add `MIN_ORDER_SIZE_USD`, `MAX_ORDER_SIZE_USD`, `MIN_GRID_LEVELS` to `config/settings.py` | | |
| TASK-030 | Add `InsufficientCapitalError` exception to `bot_v2/models/exceptions.py` | | |

### Implementation Phase 2: Core Data Models

- GOAL-002: Update RiskTier dataclass and related models

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Update `RiskTier` dataclass in `bot_v2/risk/adaptive_risk_manager.py`: replace `capital_allocation` with `level_allocation_ratio`; remove `max_position_size_usd` | | |
| TASK-006 | Update `_load_tier_from_config()` in `bot_v2/risk/adaptive_risk_manager.py` to load new fields | | |
| TASK-007 | Update validation in `_validate_tier_config()` to validate `level_allocation_ratio` range (0.0-1.0) | | |
| TASK-008 | Update `Position` model in `bot_v2/models/position.py`: replace `capital_allocation_pct` with `level_allocation_ratio`, `order_size_quote`, `effective_leverage` | | |
| TASK-031 | Round active_levels to even number for symmetric split in calculation logic | | |
| TASK-033 | Cap order_size_quote at MAX_ORDER_SIZE_USD in calculate_grid_params() | | |

### Implementation Phase 3: Calculation Logic

- GOAL-003: Implement grid parameter calculation

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | Implement `calculate_grid_params()` in `bot_v2/risk/adaptive_risk_manager.py` `PositionSizer` class | | |
| TASK-010 | Add `get_grid_parameters()` async method to `AdaptiveRiskManager` class | | |
| TASK-011 | Update `get_tier_info()` to return `level_allocation_ratio` instead of `capital_allocation` | | |

### Implementation Phase 4: Bot Integration

- GOAL-004: Integrate parameter calculation into grid deployment

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-012 | Update `bot_v2/bot.py` grid initialization to use `calculate_grid_params()` | | |
| TASK-013 | Update `bot_v2/grid/orchestrator.py` to receive calculated `order_size_quote` and `active_levels` | | |
| TASK-014 | Update `bot_v2/grid/orchestrator.py`: remove `allocation_pct` logic (lines 243, 293) | | |
| TASK-015 | Update `bot_v2/risk/adaptive_integration.py`: change `allocation_pct` to `level_allocation_ratio` | | |

### Implementation Phase 5: Backtest & Scripts

- GOAL-005: Update backtest and utility scripts

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-016 | Update `src/backtest/grid_backtester.py` to accept calculated `order_size_quote` parameter | | |
| TASK-017 | Update `scripts/optimize_params.py`: remove `ORDER_SIZE_QUOTE` from optimization params | | |
| TASK-018 | Update `scripts/run_backtest.py` to use dynamic calculation | | |
| TASK-019 | Update `main.py` grid initialization flow | | |

### Implementation Phase 6: Tests

- GOAL-006: Update all affected tests

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-020 | Update `test_adaptive_risk_manager_*.py` test fixtures with new tier fields | | |
| TASK-021 | Update `test_bot.py` references to `capital_allocation_pct` | | |
| TASK-022 | Update `test_grid_orchestrator.py` allocation logic tests | | |
| TASK-023 | Update `test_integration_smoke.py` ORDER_SIZE_QUOTE references | | |
| TASK-024 | Update `test_fill_handler.py` order size expectations | | |
| TASK-025 | Update `test_risk_manager_clarity.py` RiskTier fixtures | | |
| TASK-026 | Update `test_kill_switch_*.py` tier fixtures | | |
| TASK-027 | Update `test_adaptive_integration.py` tier info assertions | | |
| TASK-028 | Update `test_adaptive_risk_manager_hysteresis.py` tier configs | | |
| TASK-029 | Update `test_adaptive_risk_manager_boundaries.py` RiskTier fixtures | | |

---

## 3. Detailed Implementation Specifications

### TASK-005: RiskTier Dataclass Update

**Location**: `bot_v2/risk/adaptive_risk_manager.py`:47-51

**Before**:
```python
@dataclass
class RiskTier:
    name: str
    min_trades: int
    max_trades: Optional[int]
    profit_factor_min: float
    profit_factor_max: Optional[float]
    sharpe_ratio_min: float
    win_rate_min: Optional[float]
    max_drawdown_max: Optional[float]
    consecutive_losses_max: Optional[int]
    capital_allocation: float  # REMOVE
    leverage_multiplier: float
    max_leverage_cap: int
    max_position_size_usd: Optional[int]  # REMOVE
    description: str
```

**After**:
```python
@dataclass
class RiskTier:
    name: str
    min_trades: int
    max_trades: Optional[int]
    profit_factor_min: float
    profit_factor_max: Optional[float]
    sharpe_ratio_min: float
    win_rate_min: Optional[float]
    max_drawdown_max: Optional[float]
    consecutive_losses_max: Optional[int]
    level_allocation_ratio: float  # NEW: 0.0-1.0 (fraction of configured levels to activate)
    leverage_multiplier: float
    max_leverage_cap: int
    description: str
```

### TASK-009: PositionSizer.calculate_grid_params() Implementation

**Location**: `bot_v2/risk/adaptive_risk_manager.py`

```python
@staticmethod
def calculate_grid_params(
    capital: float,
    tier: "RiskTier",
    configured_up: int,
    configured_down: int,
    base_leverage: float,
    min_order_size_usd: float = 5.0,
    max_order_size_usd: float = 100.0,
    min_grid_levels: int = 10,
) -> Dict[str, Any]:
    """
    Calculate grid parameters based on capital, tier, and leverage.
    
    Formula:
        effective_leverage = min(base_leverage × tier.multiplier, tier.max_cap)
        notional_capital = capital × effective_leverage
        active_levels = round_to_even(configured × tier.ratio)
        order_size = min(notional_capital / active_levels, max_order_size)
    
    Args:
        capital: Available margin capital (USDT)
        tier: RiskTier with level_allocation_ratio and leverage settings
        configured_up: Number of grid levels above centre
        configured_down: Number of grid levels below centre
        base_leverage: Base leverage from strategy config
        min_order_size_usd: Minimum notional per order (default $5)
        max_order_size_usd: Maximum notional per order (default $100)
        min_grid_levels: Minimum active levels to maintain (default 10)
    
    Returns:
        Dict with: levels_up, levels_down, active_levels, order_size_quote,
                   effective_leverage, notional_capital, level_allocation_ratio
    """
    import math
    
    # Calculate effective leverage
    effective_leverage = base_leverage * tier.leverage_multiplier
    effective_leverage = min(effective_leverage, float(tier.max_leverage_cap))
    effective_leverage = max(1.0, effective_leverage)
    
    # Calculate notional capital
    notional_capital = capital * effective_leverage
    
    # Check minimum capital requirement
    min_required_capital = min_grid_levels * min_order_size_usd / effective_leverage
    if capital < min_required_capital:
        raise InsufficientCapitalError(
            f"Capital ${capital:.2f} insufficient. "
            f"Minimum required: ${min_required_capital:.2f} "
            f"({min_grid_levels} levels @ ${min_order_size_usd}/level, {effective_leverage:.1f}x leverage)"
        )
    
    # Calculate total configured levels
    total_configured = configured_up + configured_down
    
    # Calculate desired total based on tier ratio
    desired_total = math.floor(total_configured * tier.level_allocation_ratio)
    
    # Round down to even for symmetric grid
    active_total = desired_total - (desired_total % 2)
    active_total = max(2, active_total)  # Minimum 2 (1 up + 1 down)
    
    # Cap by capital and configured levels
    max_by_capital = math.floor(notional_capital / min_order_size_usd)
    active_total = min(active_total, max_by_capital)
    active_total = max(min_grid_levels, active_total)
    active_total = min(active_total, total_configured)
    
    # Symmetric split (even number guarantees equal up/down)
    levels_up = active_total // 2
    levels_down = active_total // 2
    
    # Calculate order size with max cap
    order_size_quote = notional_capital / active_total
    order_size_quote = min(order_size_quote, max_order_size_usd)
    order_size_quote = max(order_size_quote, min_order_size_usd)
    
    return {
        "tier": tier.name,
        "levels_up": levels_up,
        "levels_down": levels_down,
        "active_levels": active_total,
        "order_size_quote": round(order_size_quote, 2),
        "effective_leverage": round(effective_leverage, 1),
        "notional_capital": round(notional_capital, 2),
        "level_allocation_ratio": tier.level_allocation_ratio,
    }
```

### TASK-001: adaptive_risk_tiers.json Update

**Location**: `config/adaptive_risk_tiers.json`

**Change Summary**:

| Field | Old Value | New Value |
|-------|-----------|-----------|
| `capital_allocation_pct` | 30-80 | **REMOVED** |
| `max_position_size_usd` | 2000-12000 | **REMOVED** |
| `level_allocation_ratio` | (new) | 0.50-1.00 by tier |

**Tier Values**:

| Tier | level_allocation_ratio |
|------|----------------------|
| PROBATION | 0.50 |
| CONSERVATIVE | 0.65 |
| STANDARD | 0.80 |
| AGGRESSIVE | 0.90 |
| CHAMPION | 1.00 |

**Example Tier**:
```json
{
  "name": "STANDARD",
  "description": "Default tier for proven consistent symbols",
  "min_trades": 90,
  "max_trades": null,
  "min_profit_factor": 1.5,
  "max_profit_factor": null,
  "min_sharpe_ratio": 0.5,
  "min_win_rate": 0.40,
  "max_drawdown": 0.25,
  "max_consecutive_losses": 8,
  "level_allocation_ratio": 0.80,
  "leverage_multiplier": 1.0,
  "max_leverage_cap": 20
}
```

### TASK-002: strategy_configs.json Update

**Location**: `config/strategy_configs.json`

**Remove from all symbols**:
```json
"grid_order_size_quote": "100.0",
"capital_usage_percent": "100",
```

**No replacement needed** - these are calculated dynamically at runtime.

---

## 4. Alternatives

- **ALT-001**: Keep `capital_allocation_pct` but interpret as reserve percentage. Rejected: Still conceptually wrong for grids - reserve doesn't change level count.
- **ALT-002**: Fixed `grid_order_size_quote` per tier. Rejected: Doesn't scale with capital growth, requires constant recalibration.
- **ALT-003**: Dynamic order size only, no level control. Rejected: Lower tiers would have same grid density as higher tiers, no progressive risk management.
- **ALT-004**: Asymmetric level ratios (more buy levels for lower tiers). Rejected: Adds complexity without clear benefit; can be added later if needed.

---

## 5. Dependencies

- **DEP-001**: `RiskTier` dataclass must support `level_allocation_ratio` field
- **DEP-002**: `Position` model must store `level_allocation_ratio`, `order_size_quote`, `effective_leverage`
- **DEP-003**: `GridOrchestrator` must accept calculated parameters instead of static config
- **DEP-004**: Exchange minimum notional ($5 USDT) must be configurable
- **DEP-005**: All tier configs must have valid `level_allocation_ratio` (0.0-1.0)
- **DEP-006**: Strategy configs must have `grid_num_grids_up` and `grid_num_grids_down`

---

## 6. Files

| File | Category | Changes |
|------|----------|---------|
| `config/adaptive_risk_tiers.json` | Config | Replace `capital_allocation_pct`, `max_position_size_usd` with `level_allocation_ratio` |
| `config/strategy_configs.json` | Config | Remove `grid_order_size_quote`, `capital_usage_percent` from all symbols |
| `config/settings.py` | Config | Add `MIN_ORDER_SIZE_USD`, `MIN_GRID_LEVELS` constants |
| `bot_v2/risk/adaptive_risk_manager.py` | Core | Update `RiskTier` dataclass, add `calculate_grid_params()`, update `PositionSizer` |
| `bot_v2/models/position.py` | Model | Replace `capital_allocation_pct` with `level_allocation_ratio`, `order_size_quote`, `effective_leverage` |
| `bot_v2/bot.py` | Core | Update grid initialization to use `calculate_grid_params()` |
| `bot_v2/grid/orchestrator.py` | Core | Remove allocation logic, accept calculated params |
| `bot_v2/risk/adaptive_integration.py` | Core | Update allocation field references |
| `src/backtest/grid_backtester.py` | Script | Accept calculated `order_size_quote` |
| `scripts/optimize_params.py` | Script | Remove `ORDER_SIZE_QUOTE` from params |
| `scripts/run_backtest.py` | Script | Use dynamic calculation |
| `main.py` | Entry | Update grid initialization |
| `tests/test_adaptive_risk_manager_*.py` | Test | Update tier fixtures and assertions |
| `tests/test_bot.py` | Test | Update `capital_allocation_pct` references |
| `tests/test_grid_orchestrator.py` | Test | Update allocation tests |
| `tests/test_fill_handler.py` | Test | Update order size assertions |
| `tests/test_integration_smoke.py` | Test | Update `ORDER_SIZE_QUOTE` refs |

---

## 7. Testing

### Unit Tests

- **TEST-001**: `calculate_grid_params()` with $100 capital, 10x leverage, STANDARD tier (0.80) returns correct params
- **TEST-002**: `calculate_grid_params()` enforces minimum $5 order size
- **TEST-003**: `calculate_grid_params()` enforces minimum 10 grid levels
- **TEST-004**: `calculate_grid_params()` respects tier leverage cap
- **TEST-005**: `calculate_grid_params()` with low leverage caps levels appropriately
- **TEST-006**: RiskTier validation rejects `level_allocation_ratio` outside 0.0-1.0
- **TEST-007**: JSON config loader correctly parses `level_allocation_ratio`
- **TEST-008**: Position model stores new fields correctly

### Integration Tests

- **TEST-009**: Grid deployment with PROBATION tier uses 50% of configured levels
- **TEST-010**: Grid deployment with CHAMPION tier uses 100% of configured levels
- **TEST-011**: Order size calculated correctly: `notional_capital / active_levels`
- **TEST-012**: Tier promotion increases active levels
- **TEST-013**: Tier demotion decreases active levels
- **TEST-014**: Capital growth increases order size proportionally
- **TEST-015**: Capital loss decreases order size proportionally

### Edge Cases

- **TEST-016**: Capital exactly equals minimum for 10 levels
- **TEST-017**: Configured levels lower than tier ratio would allow
- **TEST-018**: Leverage = 1x with minimum capital
- **TEST-019**: Leverage = 30x with large capital
- **TEST-020**: 17 configured levels (from SOL/USDT config) rounds to 16 even levels

### Additional Edge Cases (v2)

- **TEST-024**: Capital < min_grid_levels × min_order_size raises InsufficientCapitalError
- **TEST-025**: Odd active_levels (17) rounds down to even (16) and splits symmetrically (8 up + 8 down)
- **TEST-026**: Tier change mid-grid: passive behavior - wait until next re-centre (document)
- **TEST-027**: configured_levels < min_grid_levels uses configured limit
- **TEST-028**: $0 capital raises InsufficientCapitalError
- **TEST-029**: Capital growth caps order_size_quote at MAX_ORDER_SIZE_USD ($100)
- **TEST-030**: Order size caps when effective capital exceeds max_order_size × active_levels

### Regression Tests

- **TEST-021**: All existing tests pass after refactor
- **TEST-022**: Grid behavior unchanged when capital exceeds requirements
- **TEST-023**: Kill switch still functions with new calculation model

---

## 8. Risks & Assumptions

- **RISK-001**: Exchange minimum notional may vary by symbol. Mitigation: Use configurable `MIN_ORDER_SIZE_USD` with symbol-specific overrides (TASK-034).
- **RISK-002**: Tier changes mid-session - grid parameters recalculated at next re-centre only. Mitigation: Document behavior, log warning on tier change.
- **RISK-003**: Odd level count - always round down to even. Mitigation: Symmetric split 50/50.
- **RISK-004**: Large price swings may make order size too small after tier demotion. Mitigation: Enforce minimum order size, cap levels if needed.
- **RISK-005**: Capital insufficient for minimum grid. Mitigation: Raise `InsufficientCapitalError` before deployment.

- **ASSUMPTION-001**: Futures margin = notional / leverage (isolated margin model)
- **ASSUMPTION-002**: All configured levels (up + down) share the same order size
- **ASSUMPTION-003**: $5 minimum / $100 maximum notional is sufficient for all supported symbols
- **ASSUMPTION-004**: Tier `level_allocation_ratio` values are manually tuned and stable
- **ASSUMPTION-005**: Capital updates happen via profit/loss callbacks, no async race conditions
- **ASSUMPTION-006**: Tier changes take effect at next re-centre (not immediate)

---

## 9. Related Specifications / Further Reading

- `/config/adaptive_risk_tiers.json` - Current tier configuration (v1.4)
- `/config/strategy_configs.json` - Per-symbol grid configurations
- `/bot_v2/risk/adaptive_risk_manager.py` - RiskTier and PositionSizer
- `/plan/feature-grid-capital-sizing-1.md` - Previous iteration (deprecated)
- `/AGENTS.md` - Project conventions and build commands

---

## 10. Migration Path

| Phase | Action | Risk Level |
|-------|--------|------------|
| 1 | Add new fields to config files | Low |
| 2 | Update RiskTier dataclass | Medium |
| 3 | Implement `calculate_grid_params()` | Low |
| 4 | Update bot integration | High |
| 5 | Remove old config fields | Medium |
| 6 | Update all tests | Medium |
| 7 | Validate with integration tests | Medium |

**Rollback Plan**: Keep `capital_allocation_pct` in config but ignore it; add `use_legacy_allocation` flag if needed.

---

## 11. Success Criteria

- All 23 unit tests pass
- All 15 integration tests pass
- Grid deploys successfully on PROBATION tier with $100 capital
- Order size never falls below `$5` minimum
- Active levels never exceed configured levels
- Tier promotion increases active levels
- Tier demotion decreases active levels
- No regression in existing functionality