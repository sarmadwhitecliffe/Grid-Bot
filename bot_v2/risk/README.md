# Adaptive Risk Manager (bot_v2/risk)

This document describes the adaptive, tier-based risk system used by the bot.

## Tier definitions (from `config/adaptive_risk_tiers.json`)

- CHAMPION
  - min_trades: 35
  - min_profit_factor: 2.0
  - capital_allocation_pct: 70.0
  - max_leverage: 8

- AGGRESSIVE
  - min_trades: 25
  - min_profit_factor: 1.5
  - capital_allocation_pct: 60.0
  - max_leverage: 7

- STANDARD
  - min_trades: 15
  - min_profit_factor: 1.2
  - capital_allocation_pct: 50.0
  - max_leverage: 5

- CONSERVATIVE
  - min_trades: 11
  - min_profit_factor: 0.8
  - capital_allocation_pct: 40.0
  - max_leverage: 3

- PROBATION
  - min_trades: 0
  - min_profit_factor: 0.0
  - capital_allocation_pct: 30.0
  - max_leverage: 2

## Notes

- The canonical configuration lives in `config/adaptive_risk_tiers.json`. Tests are written to reflect that configuration unless explicitly patched in test fixtures.

- Position sizing (Kelly): For invalid or edge-case metric inputs (e.g., zero `avg_loss`, zero/one `win_rate`), the system intentionally falls back to a *safe default* of **1x leverage**, rather than using the `max_leverage` for that tier. This avoids excessive risk amplification when data is insufficient.

- If you change tier allocations or thresholds in the config file, ensure tests that assert specific allocation or threshold values are updated accordingly.

## Leverage Sizing: Composite Formula

Position sizing uses a **composite leverage formula** (not pure Kelly criterion) to determine leverage within each tier's min/max band. The composite formula is designed for strategies that capture small, frequent wins (low AvgWinR by design).

### Why Not Pure Kelly?

Pure Kelly (`WR × AvgWinR - (1-WR)`) punishes low R-multiples. The NY Breakout strategy structurally produces AvgWinR of 0.3R–1.0R via aggressive peak exits and trail exits, so Kelly would pin all symbols at minimum leverage regardless of actual edge.

### Composite Formula

Three weighted components:
- **Profit Factor (50%)**: `min(PF, 5.0) / 5.0` — capped at 5.0 to prevent outlier dominance
- **Win Rate (30%)**: `max(0, (WR - 0.5)) / 0.5` — scores 0 below 50% (no reward for coin-flip strategies)
- **Kelly (20%)**: `min(1.0, max(0.0, kelly_f))` — traditional Kelly contribution

Combined: `composite = (0.50 × PF_score) + (0.30 × WR_score) + (0.20 × Kelly_score)`
Half-sizing: `leverage = composite × 0.5 × max_leverage`
Clamped to tier's `[min_leverage, max_leverage]` band.

### Edge Cases

- WR=100% or WR=0%: Handled by edge guards (returns max or 0 respectively)
- All metrics zero: Returns 0
- Missing `profit_factor`: Backward-compatible default of 0.0

### Parameter

The `profit_factor` parameter is passed from `calculate_position_size()` via `metrics.profit_factor`. Log output shows `Composite: WR=X%, PF=X, AvgWinR=XR`.
