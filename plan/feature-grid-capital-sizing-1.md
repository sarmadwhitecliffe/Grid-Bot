---
goal: Implement capital-aware grid order sizing that calculates realistic grid levels based on allocated capital, tier allocation, and leverage constraints
version: 1.0
date_created: 2026-03-14
last_updated: 2026-03-14
owner: Grid Bot Team
status: Planned
tags: [feature, grid, capital-management, risk, core]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan implements automatic grid level calculation based on allocated capital constraints. Currently, grids deploy all configured levels regardless of capital, causing exchange rejections and inconsistent state. This feature ensures gridsrespect tier allocation, leverage, and minimum notional requirements.

## Problem Statement

| Current Behavior | Issue |
|------------------|-------|
| 50 levels × $30/order = $1,500 exposure | Exceeds $30 allocated capital |
| No capital validation before deployment | Exchange rejects with `InsufficientFunds` |
| Grid state becomes inconsistent | Some orders placed, others fail |

## Proposed Solution

Calculate maximum viable grid levels from:
- `allocated_margin = initial_capital × tier_allocation`
- `margin_per_level = min_notional / leverage`
- `max_levels = allocated_margin / margin_per_level`

Deploy only `min(max_levels, configured_levels)` with order size = `min_notional`.

---

## 1. Requirements & Constraints

- **REQ-001**: Grid order size must respect allocated capital (initial_capital × tier_allocation)
- **REQ-002**: Each order must meet minimum notional requirement ($5 default)
- **REQ-003**: Leverage must be applied when calculating margin requirements
- **REQ-004**: Grid levels must be automatically calculated or capped based on available margin
- **REQ-005**: Counter-orders must match fill amount (not recalculated)
- **REQ-006**: System must log when configured levels exceed capital constraints
- **REQ-007**: Grid deployment must fail gracefully with clear error message if capital insufficient
- **REQ-008**: Grid must scale automatically as capital/tier improves

- **SEC-001**: No grid orders should exceed allocated capital under any circumstance
- **SEC-002**: All financial calculations must use Decimal precision

- **CON-001**: Minimum notional is $5 (configurable via `BOT_MIN_NOTIONAL_USD` env var)
- **CON-002**: Leverage comes from tier configuration (PROBATION: 1-2x)
- **CON-003**: Grid configuration may request more levels than capital allows

- **GUD-001**: Use existing `Decimal` for all monetary calculations
- **GUD-002**: Log all capital calculations at INFO level for debugging
- **GUD-003**: Maintain existing grid behavior when capital is sufficient

- **PAT-001**: Follow existing error handling pattern in `deploy_grid()`
- **PAT-002**: Use async methods for capital manager calls

---

## 2. Implementation Steps

### Implementation Phase 1: Core Calculation Logic

- GOAL-001: Implement grid parameter calculation based on allocated capital

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `InsufficientGridCapital` exception to `bot_v2/models/exceptions.py` | | |
| TASK-002 | Add `grid_capital_constraint: bool = True` to `StrategyConfig` in `bot_v2/models/strategy_config.py` | | |
| TASK-003 | Add `grid_leverage: Optional[int] = None` to `StrategyConfig` for optional override | | |

### Implementation Phase 2: Grid Orchestrator Updates

- GOAL-002: Add capital-aware parameter calculation to GridOrchestrator

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Add `capital_manager` parameter to `GridOrchestrator.__init__()` in `bot_v2/grid/orchestrator.py` | | |
| TASK-005 | Implement `_calculate_grid_parameters()` async method in `bot_v2/grid/orchestrator.py` | | |
| TASK-006 | Replace `_get_risk_adjusted_order_size()` with `_get_grid_order_size()` in `bot_v2/grid/orchestrator.py` | | |
| TASK-007 | Update `deploy_grid()` to use calculated parameters with validation in `bot_v2/grid/orchestrator.py` | | |

### Implementation Phase 3: Bot Integration

- GOAL-003: Pass capital_manager to grid orchestrators

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Update `_init_grid_orchestrator()` in `bot_v2/bot.py` to pass `capital_manager` | | |
| TASK-009 | Update `_run_grid_orchestrators_tick()` in `bot_v2/bot.py` to handle capital changes | | |

### Implementation Phase 4: Error Handling & Logging

- GOAL-004: Ensure graceful failure with clear messages

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | Add detailed error logging in `_calculate_grid_parameters()` when capital insufficient | | |
| TASK-011 | Add warning log when configured levels > calculated max levels | | |
| TASK-012 | Update `handle_fill()` to log when counter-order skipped due to capital | | |

---

## 3. Detailed Implementation Specifications

### TASK-005: `_calculate_grid_parameters()` Implementation

**Location**: `bot_v2/grid/orchestrator.py`

```python
async def _calculate_grid_parameters(self) -> Tuple[int, int, Decimal]:
    """
    Calculate realistic grid parameters based on allocated capital.
    
    Formula:
        allocated_margin = capital × tier_allocation
        margin_per_level = min_notional / leverage
        max_levels = allocated_margin / margin_per_level
    
    Returns:
        Tuple[int, int, Decimal]: (levels_up, levels_down, order_size)
    
    Raises:
        InsufficientGridCapital: If capital cannot support minimum grid (2 levels)
    """
    import os
    from decimal import Decimal
    
    # Get allocated capital
    if not self.capital_manager:
        # Fallback: use config value without capital constraint
        order_size = Decimal(str(getattr(self.config, "grid_order_size_quote", 100)))
        num_up = int(getattr(self.config, "grid_num_grids_up", 25))
        num_down = int(getattr(self.config, "grid_num_grids_down", 25))
        return (num_up, num_down, order_size)
    
    # Get current capital and tier allocation
    capital = await self.capital_manager.get_capital(self.symbol)
    
    if capital <= Decimal("0"):
        raise InsufficientGridCapital(
            f"[{self.symbol}] Capital is ${capital:.2f} - cannot deploy grid"
        )
    
    # Get tier allocation
    tier_info = self.risk_manager.get_tier_info(self.symbol)
    allocation_pct = Decimal(str(tier_info.get("capital_allocation", 1.0)))
    
    # Get leverage (use min_leverage as conservative default)
    leverage = Decimal(str(tier_info.get("min_leverage", 1)))
    if getattr(self.config, "grid_leverage", None):
        leverage = Decimal(str(self.config.grid_leverage))
    
    # Calculate allocated margin
    allocated_margin = capital * allocation_pct
    
    # Get minimum notional
    min_notional = Decimal(os.getenv("BOT_MIN_NOTIONAL_USD", "5.0"))
    
    # Calculate margin per level (conservative: use min leverage)
    margin_per_level = min_notional / leverage
    
    # Calculate maximum levels supported by capital
    max_levels = int(allocated_margin / margin_per_level)
    
    # Minimum viable grid: 2 levels (1 up + 1 down)
    if max_levels < 2:
        required_capital = (2 * margin_per_level) / allocation_pct
        raise InsufficientGridCapital(
            f"[{self.symbol}] Insufficient capital for grid deployment. "
            f"Available: ${capital:.2f}, Allocated: ${allocated_margin:.2f}, "
            f"Min notional: ${min_notional:.2f}, Leverage: {leverage}x. "
            f"Need minimum ${required_capital:.2f} capital for 2-level grid."
        )
    
    # Get configured levels
    config_up = int(getattr(self.config, "grid_num_grids_up", 25))
    config_down = int(getattr(self.config, "grid_num_grids_down", 25))
    config_total = config_up + config_down
    
    # Calculate actual levels (cap at max_levels)
    if max_levels < config_total:
        # Split evenly, favoring buys in case of odd number
        actual_up = max_levels // 2 + (max_levels % 2)
        actual_down = max_levels // 2
        logger.warning(
            f"[{self.symbol}] Grid levels reduced from {config_up}+{config_down} to "
            f"{actual_up}+{actual_down} due to capital constraints. "
            f"Capital: ${capital:.2f}, Allocation: {allocation_pct*100:.0f}%, "
            f"Margin available: ${allocated_margin:.2f}, Max levels: {max_levels}"
        )
        levels_up, levels_down = actual_up, actual_down
    else:
        levels_up, levels_down = config_up, config_down
    
    # Order size is min_notional (ensures exchange acceptance)
    order_size = min_notional
    
    logger.info(
        f"[{self.symbol}] Grid parameters: {levels_up} up + {levels_down} down @ ${order_size:.2f}/order. "
        f"Capital: ${capital:.2f}, Allocation: {allocation_pct*100:.0f}%, Leverage: {leverage}x, "
        f"Margin per level: ${margin_per_level:.2f}, Max levels: {max_levels}"
    )
    
    return (levels_up, levels_down, order_size)
```

### TASK-007: `deploy_grid()` Updates

**Location**: `bot_v2/grid/orchestrator.py`

```python
async def deploy_grid(self, centre: Decimal):
    """Calculate and place all limit orders."""
    logger.info(f"[{self.symbol}] Deploying grid around {centre}")
    self.centre_price = centre

    # Calculate grid parameters based on available capital
    try:
        if getattr(self.config, "grid_capital_constraint", True):
            levels_up, levels_down, order_size = await self._calculate_grid_parameters()
            self.calculator.order_size_quote = order_size
        else:
            # Legacy behavior (not recommended)
            order_size = self._get_risk_adjusted_order_size()
            self.calculator.order_size_quote = order_size
            levels_up = int(getattr(self.config, "grid_num_grids_up", 25))
            levels_down = int(getattr(self.config, "grid_num_grids_down", 25))
    except InsufficientGridCapital as e:
        logger.error(str(e))
        self.is_active = False
        return
    
    # Update calculator with calculated order size and levels
    self.calculator.num_grids_up = levels_up
    self.calculator.num_grids_down = levels_down
    
    # Calculate levels
    levels = self.calculator.calculate(centre)
    
    # ... rest of deployment logic unchanged ...
```

---

## 4. Alternatives

- **ALT-001**: Dynamic level deployment - Deploy levels as price moves. Rejected: Too complex, edge cases with order management.
- **ALT-002**: Increase initial capital - Require higher capital per symbol. Rejected: Doesn't solve scaling issue, ignores tier system.
- **ALT-003**: Use `max_position_size_usd` cap - Apply tier cap to grid orders. Rejected: Doesn't address level count issue.
- **ALT-004**: Separate grid allocation - Independent allocation for grids. Rejected: Adds complexity, user confusion about capital splits.

---

## 5. Dependencies

- **DEP-001**: `capital_manager` must be properly initialized with `get_capital()` method
- **DEP-002**: `risk_manager` must have `get_tier_info()` returning `capital_allocation` and `min_leverage`
- **DEP-003**: `BOT_MIN_NOTIONAL_USD` environment variable (defaults to "5.0")
- **DEP-004**: `GridCalculator` must support dynamic `num_grids_up` and `num_grids_down`

---

## 6. Files

| File | Changes |
|------|---------|
| `bot_v2/models/exceptions.py` | Add `InsufficientGridCapital` exception |
| `bot_v2/models/strategy_config.py` | Add `grid_capital_constraint`, `grid_leverage` fields |
| `bot_v2/grid/orchestrator.py` | Add `capital_manager` param, implement `_calculate_grid_parameters()`, update `deploy_grid()` |
| `bot_v2/grid/calculator.py` | Verify `num_grids_up/down` can be updated dynamically |
| `bot_v2/bot.py` | Pass `capital_manager` to `GridOrchestrator` |

---

## 7. Testing

### Unit Tests

- **TEST-001**: `_calculate_grid_parameters()` with sufficient capital returns configured levels
- **TEST-002**: `_calculate_grid_parameters()` with limited capital returns reduced levels
- **TEST-003**: `_calculate_grid_parameters()` with minimal capital returns 2 levels
- **TEST-004**: `_calculate_grid_parameters()` with insufficient capital raises `InsufficientGridCapital`
- **TEST-005**: Order size equals `min_notional` when capital constrained
- **TEST-006**: Leverage is applied correctly in margin calculation

### Integration Tests

- **TEST-007**: Grid deployment with $100 capital, PROBATION tier (30% allocation, 2x leverage)
- **TEST-008**: Grid deployment skips deployment when capital = $0
- **TEST-009**: Grid deployment logs warning when levels reduced
- **TEST-010**: Counter-order uses fill amount (not recalculated)
- **TEST-011**: Grid scales up when capital increases and tier improves

### Edge Cases

- **TEST-012**: Capital exactly equals minimum for 2-level grid
- **TEST-013**: Tier allocation = 100% (CHAMPION tier)
- **TEST-014**: Leverage = 1x vs 10x
- **TEST-015**: `grid_capital_constraint = False` uses legacy behavior

---

## 8. Risks & Assumptions

- **RISK-001**: Exchange minimum notional may vary by symbol. Mitigation: Use configurable `BOT_MIN_NOTIONAL_USD` with symbol-specific overrides in future.
- **RISK-002**: Fills may occur faster than capital updates. Mitigation: Counter-orders use fill amount, capital updates async.
- **RISK-003**: Leverage changes mid-session. Mitigation: Use leverage at deployment time, log warning if changes detected.

- **ASSUMPTION-001**: Futures margin requires `notional / leverage` per order
- **ASSUMPTION-002**: All grid orders are placed simultaneously at deployment
- **ASSUMPTION-003**: `min_notional` is consistent across symbols (acceptable for MVP)
- **ASSUMPTION-004**: Capital updates happen via `_on_grid_trade_closed()` callback

---

## 9. Related Specifications / Further Reading

- `/bot_v2/risk/adaptive_risk_manager.py` - Tier allocation and leverage logic
- `/bot_v2/risk/capital_manager.py` - Capital tracking per symbol
- `/config/adaptive_risk_tiers.json` - Tier definitions (capital_allocation_pct, leverage)
- `/config/settings.py` - `ORDER_SIZE_QUOTE`, `NUM_GRIDS_UP`, `NUM_GRIDS_DOWN`