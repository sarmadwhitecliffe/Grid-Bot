---
goal: 'Strategy Core — Self-contained regime detection and grid level calculation logic'
version: '2.0'
date_created: '2026-02-22'
last_updated: '2026-02-22'
owner: 'Antigravity'
status: 'Complete'
tags: ['feature', 'strategy', 'logic', 'standalone']
---

# Phase 2 — Strategy Core

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

Phase 2 implements the **"brains" of the bot** as self-contained strategy modules:

1. **Regime Detector** — Classifies market conditions as `RANGING` or `TRENDING` using ADX and Bollinger Band width indicators computed directly from the `ta` library.
2. **Grid Calculator** — Calculates the exact price levels for limit orders, supporting both Arithmetic and Geometric spacing.

> **All indicator logic is implemented from scratch within this project using the `ta` pip library. No external project imports.**

---

## 1. Requirements & Constraints

| ID | Requirement |
|----|-------------|
| **REQ-2.1** | Regime Detector must use **ADX(14)** (`ta.trend.ADXIndicator`) to detect trending conditions. |
| **REQ-2.2** | Regime Detector must use **Bollinger Band Width** (`ta.volatility.BollingerBands`) for range compression. |
| **REQ-2.3** | Grid is only permitted to deploy when regime is `RANGING`. Cancel & pause if switches to `TRENDING`. |
| **REQ-2.4** | Grid Calculator must support **Arithmetic** (fixed $ gap) and **Geometric** (fixed % gap) spacing. |
| **REQ-2.5** | Grid Calculator must handle **asymmetric grids** (different levels up vs. down). |
| **REQ-2.6** | All logic must be **stateless** — outputs are pure functions of inputs. |
| **REQ-2.7** | Grid prices must be quantized to exchange `price_step` to avoid rejection. |

---

## 2. Implementation Tasks

| Task | Description | Sprint | Has Tests | Done | Date |
|------|-------------|--------|-----------|------|------|
| TASK-201 | Write `src/strategy/__init__.py` with data classes | Sprint 1 | ❌ | ✅ | 2026-02-22 |
| TASK-202 | Write `src/strategy/regime_detector.py` | Sprint 1 | ✅ | ✅ | 2026-02-22 |
| TASK-203 | Write `src/strategy/grid_calculator.py` | Sprint 1 | ✅ | ✅ | 2026-02-22 |

---

## 3. Detailed Specifications

### 3.1 Data Structures — `src/strategy/__init__.py`

```python
from dataclasses import dataclass
from enum import Enum

class MarketRegime(Enum):
    RANGING = "ranging"
    TRENDING = "trending"
    UNKNOWN = "unknown"

class GridType(Enum):
    ARITHMETIC = "arithmetic"
    GEOMETRIC = "geometric"

@dataclass
class RegimeInfo:
    regime: MarketRegime
    adx: float
    bb_width: float
    adx_threshold: int
    bb_width_threshold: float
    reason: str

    @property
    def is_ranging(self) -> bool:
        return self.regime == MarketRegime.RANGING

@dataclass
class GridLevel:
    price: float
    side: str               # 'buy' or 'sell'
    level_index: int        # 1 = closest to centre
    order_size_quote: float # USDT value for this level
```

---

### 3.2 Regime Detector — `src/strategy/regime_detector.py`

**Algorithm:**
- Compute **ADX(14)** on OHLCV DataFrame.
- Compute **BB Width** = `(upper - lower) / middle` with window=20.
- `RANGING` if `ADX < threshold` AND `BB_width < bb_width_threshold`.
- Any condition fails → `TRENDING`.

```python
from ta.trend import ADXIndicator
from ta.volatility import BollingerBands
import pandas as pd
from src.strategy import RegimeInfo, MarketRegime

class RegimeDetector:
    def __init__(
        self,
        adx_threshold: int = 25,
        bb_width_threshold: float = 0.04,
        adx_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ):
        self.adx_threshold = adx_threshold
        self.bb_width_threshold = bb_width_threshold
        self.adx_period = adx_period
        self.bb_period = bb_period
        self.bb_std = bb_std

    def detect(self, ohlcv_df: pd.DataFrame) -> RegimeInfo:
        """
        Args:
            ohlcv_df: columns [timestamp, open, high, low, close, volume]
                      Must have >= max(adx_period, bb_period) + 5 rows.
        Returns: RegimeInfo with regime, indicators, and human-readable reason.
        """
        min_rows = max(self.adx_period, self.bb_period) + 5
        if len(ohlcv_df) < min_rows:
            return RegimeInfo(
                regime=MarketRegime.UNKNOWN, adx=0.0, bb_width=0.0,
                adx_threshold=self.adx_threshold,
                bb_width_threshold=self.bb_width_threshold,
                reason="Insufficient candle data"
            )

        adx_val = float(ADXIndicator(
            high=ohlcv_df["high"], low=ohlcv_df["low"],
            close=ohlcv_df["close"], window=self.adx_period
        ).adx().iloc[-1])

        bb = BollingerBands(
            close=ohlcv_df["close"], window=self.bb_period, window_dev=self.bb_std
        )
        mid = float(bb.bollinger_mavg().iloc[-1])
        bb_width = (
            float(bb.bollinger_hband().iloc[-1]) - float(bb.bollinger_lband().iloc[-1])
        ) / mid if mid > 0 else 0.0

        ranging = adx_val < self.adx_threshold and bb_width < self.bb_width_threshold
        regime = MarketRegime.RANGING if ranging else MarketRegime.TRENDING
        reason = (
            f"ADX={adx_val:.2f} < {self.adx_threshold} AND BB_w={bb_width:.4f} < {self.bb_width_threshold}"
            if ranging
            else f"ADX={adx_val:.2f} or BB_w={bb_width:.4f} exceeds threshold"
        )
        return RegimeInfo(
            regime=regime, adx=adx_val, bb_width=bb_width,
            adx_threshold=self.adx_threshold,
            bb_width_threshold=self.bb_width_threshold,
            reason=reason
        )
```

---

### 3.3 Grid Calculator — `src/strategy/grid_calculator.py`

**Arithmetic:** `level_price = centre ± (i × spacing_abs)`

**Geometric:** `level_price = centre × (1 ± spacing_pct)^i`

**Quantization:** `round(price / price_step) * price_step`

```python
from src.strategy import GridLevel, GridType

class GridCalculator:
    def __init__(
        self,
        grid_type: GridType = GridType.GEOMETRIC,
        spacing_pct: float = 0.01,
        spacing_abs: float = 50.0,
        num_grids_up: int = 10,
        num_grids_down: int = 10,
        order_size_quote: float = 100.0,
        price_step: float = 0.01,
        lower_bound: float | None = None,
        upper_bound: float | None = None,
    ):
        self.grid_type = grid_type
        self.spacing_pct = spacing_pct
        self.spacing_abs = spacing_abs
        self.num_grids_up = num_grids_up
        self.num_grids_down = num_grids_down
        self.order_size_quote = order_size_quote
        self.price_step = price_step
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound

    def calculate(self, centre_price: float) -> list[GridLevel]:
        """Returns sorted list of GridLevel for all buy + sell grid levels."""
        buys = [
            GridLevel(self._price(centre_price, i, "down"), "buy", i, self.order_size_quote)
            for i in range(1, self.num_grids_down + 1)
            if (p := self._price(centre_price, i, "down")) > 0
            and (self.lower_bound is None or p >= self.lower_bound)
        ]
        sells = [
            GridLevel(self._price(centre_price, i, "up"), "sell", i, self.order_size_quote)
            for i in range(1, self.num_grids_up + 1)
            if (self.upper_bound is None or self._price(centre_price, i, "up") <= self.upper_bound)
        ]
        return sorted(buys + sells, key=lambda x: x.price)

    def _price(self, centre: float, i: int, direction: str) -> float:
        if self.grid_type == GridType.ARITHMETIC:
            raw = centre + i * self.spacing_abs if direction == "up" else centre - i * self.spacing_abs
        else:
            raw = centre * (1 + self.spacing_pct)**i if direction == "up" \
                  else centre / (1 + self.spacing_pct)**i
        return self._quantize(raw)

    def _quantize(self, price: float) -> float:
        if self.price_step <= 0:
            return price
        return round(round(price / self.price_step) * self.price_step, 10)

    def order_amount(self, price: float) -> float:
        """Convert USDT order size to base-currency amount."""
        return self.order_size_quote / price
```

---

## 4. Alternatives Considered

| ID | Alternative | Decision |
|----|-------------|----------|
| **ALT-201** | ADX-only regime detection | *Rejected: BB width catches early range compression before ADX fully confirms.* |
| **ALT-202** | ATR instead of BB width | *Considered: BB width is inherently normalized (ratio) making thresholds stable across assets.* |
| **ALT-203** | Import regime logic from external library | *Rejected: Violates standalone requirement — all indicators from `ta` pip package.* |

---

## 5. Dependencies

```
ta>=0.11.0
pandas>=2.0.0
numpy>=1.26.0
```

No imports from any other project directory.

---

## 6. Files Produced

| File | Purpose |
|------|---------|
| `src/strategy/__init__.py` | `GridLevel`, `RegimeInfo`, `MarketRegime`, `GridType` |
| `src/strategy/regime_detector.py` | ADX + BB width regime classification |
| `src/strategy/grid_calculator.py` | Arithmetic + Geometric grid generation |

---

## 7. Testing

| Test ID | Description | File |
|---------|-------------|------|
| **TEST-201** | Geometric: `level[1].price == centre * 1.01` | `tests/test_grid_calculator.py` |
| **TEST-202** | Arithmetic: `level[1].price == centre + spacing_abs` | `tests/test_grid_calculator.py` |
| **TEST-203** | Upper bound filters sell levels above it | `tests/test_grid_calculator.py` |
| **TEST-204** | Price quantization snaps to `price_step` | `tests/test_grid_calculator.py` |
| **TEST-205** | Low ADX + narrow BB → `RANGING` | `tests/test_regime_detector.py` |
| **TEST-206** | ADX > 25 → `TRENDING` | `tests/test_regime_detector.py` |
| **TEST-207** | < min rows → `UNKNOWN` | `tests/test_regime_detector.py` |

---

## 8. Risks & Assumptions

| ID | Detail |
|----|--------|
| **RISK-2.1** | Sudden trend breakout. *Mitigation: ADX circuit breaker in RiskManager (Phase 3) immediately cancels orders.* |
| **RISK-2.2** | `ta` ADX lags by ~1 candle. *Acceptable for structural regime changes.* |
| **ASSUMPTION-2.1** | `price_step` is available from `ccxt` market metadata loaded in Phase 1. |

---

## 9. Related Documents

- [Master Plan](./feature-grid-bot-master-1.md)
- [Phase 1 — Foundation](./feature-grid-bot-phase1-1.md)
- [Phase 3 — Execution Engine](./feature-grid-bot-phase3-1.md)
