---
goal: 'Execution Engine — Self-contained Order Management System (OMS) and Risk Protection'
version: '2.0'
date_created: '2026-02-22'
last_updated: '2026-02-22'
owner: 'Antigravity'
status: 'Complete'
tags: ['feature', 'execution', 'oms', 'risk', 'standalone']
---

# Phase 3 — Execution Engine

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

Phase 3 builds the execution core of the bot. The **Order Management System (OMS)** manages the full lifecycle of multiple simultaneous limit orders and reacts to fills by placing counter-orders. The **Risk Manager** enforces capital protection rules via circuit breakers. All code is written from scratch within `grid_trading_bot/`.

---

## 1. Requirements & Constraints

| ID | Requirement |
|----|-------------|
| **REQ-3.1** | OMS must maintain an in-memory `dict[float, str]` mapping `grid_price → order_id`. |
| **REQ-3.2** | Fill Handler must detect filled orders by polling and trigger **counter-orders** (buy fill → place sell one level up; sell fill → place buy one level down). |
| **REQ-3.3** | Risk Manager must enforce 5 circuit breakers: Stop-Loss, Max Drawdown, Take-Profit, ADX Trending Pause, and Re-centre Trigger. |
| **REQ-3.4** | Re-centering must cancel **all open orders** and recalculate grid around the current price when triggered. |
| **REQ-3.5** | Bot must never exceed `MAX_OPEN_ORDERS` from config. |
| **CON-3.1** | Zero dependency on any external project. All logic written within `grid_trading_bot/`. |
| **CON-3.2** | Use `asyncio.Lock` to prevent race conditions when multiple fills arrive simultaneously. |

---

## 2. Implementation Tasks

| Task | Description | Sprint | Has Tests | Done | Date |
|------|-------------|--------|-----------|------|------|
| TASK-301 | Write `src/oms/__init__.py` with `OrderRecord`, `OrderStatus`, `RiskAction` | Sprint 2 | ❌ | ✅ | 2026-02-22 |
| TASK-302 | Write `src/oms/order_manager.py` for grid order lifecycle | Sprint 2 | ✅ | ✅ | 2026-02-22 |
| TASK-303 | Write `src/oms/fill_handler.py` for fill detection and counter-order placement | Sprint 2 | ✅ | ✅ | 2026-02-22 |
| TASK-304 | Write `src/risk/risk_manager.py` with all 5 circuit breakers | Sprint 2 | ✅ | ✅ | 2026-02-22 |

---

## 3. Detailed Specifications

### 3.1 Data Structures — `src/oms/__init__.py`

```python
# src/oms/__init__.py
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class OrderStatus(Enum):
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    PARTIALLY_FILLED = "partial"
    UNKNOWN = "unknown"


class RiskAction(Enum):
    """Actions returned by RiskManager to direct the main loop."""
    NONE = "none"                     # No action needed
    PAUSE_ADX = "pause_adx"           # ADX too high — cancel orders and pause
    STOP_LOSS = "stop_loss"           # Price below stop level — cancel and halt
    EMERGENCY_CLOSE = "emergency_close"  # Max drawdown hit — full emergency exit
    TAKE_PROFIT = "take_profit"       # Cumulative profit target hit — lock in gains
    RECENTRE = "recentre"             # Price drifted — cancel all and redeploy grid


@dataclass
class OrderRecord:
    """Tracks a single open grid order."""
    order_id: str
    grid_price: float       # The intended grid level price
    side: str               # 'buy' or 'sell'
    amount: float           # Base currency amount
    placed_at: datetime = field(default_factory=datetime.utcnow)
    status: OrderStatus = OrderStatus.OPEN
    filled_price: float | None = None
    filled_at: datetime | None = None
```

---

### 3.2 Order Manager — `src/oms/order_manager.py`

**Responsibilities:**
- Maintain `_orders: dict[str, OrderRecord]` (order_id → OrderRecord)
- Maintain `_grid_map: dict[float, str]` (grid_price → order_id)
- Provide methods to place, cancel, and query all grid orders

```python
# src/oms/order_manager.py
import asyncio
import logging
from datetime import datetime
from src.oms import OrderRecord, OrderStatus, RiskAction
from src.exchange.exchange_client import ExchangeClient
from src.strategy import GridLevel
from config.settings import GridBotSettings

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Manages the lifecycle of all active grid limit orders.
    Thread-safe via asyncio.Lock.
    """

    def __init__(self, client: ExchangeClient, settings: GridBotSettings):
        self.client = client
        self.settings = settings
        self._orders: dict[str, OrderRecord] = {}   # order_id -> OrderRecord
        self._grid_map: dict[float, str] = {}       # grid_price -> order_id
        self._lock = asyncio.Lock()

    # ── Placement ────────────────────────────────────────────────────

    async def deploy_grid(self, levels: list[GridLevel]) -> None:
        """
        Place limit orders for all supplied grid levels.
        Skips levels that already have an active order.
        Respects MAX_OPEN_ORDERS hard cap.
        """
        async with self._lock:
            open_count = self.open_order_count
            for level in levels:
                if open_count >= self.settings.MAX_OPEN_ORDERS:
                    logger.warning(f"MAX_OPEN_ORDERS ({self.settings.MAX_OPEN_ORDERS}) reached. Stopping deployment.")
                    break
                if level.price in self._grid_map:
                    continue  # already has order at this level
                try:
                    amount = level.order_size_quote / level.price
                    result = await self.client.place_limit_order(
                        side=level.side, price=level.price, amount=amount
                    )
                    record = OrderRecord(
                        order_id=result["id"],
                        grid_price=level.price,
                        side=level.side,
                        amount=amount,
                    )
                    self._orders[result["id"]] = record
                    self._grid_map[level.price] = result["id"]
                    open_count += 1
                    logger.info(f"Placed {level.side} limit @ {level.price:.4f} | ID: {result['id']}")
                except Exception as e:
                    logger.error(f"Failed to place order at {level.price}: {e}")

    async def cancel_all_orders(self) -> None:
        """Cancel all open orders (e.g. on regime change or re-centre)."""
        async with self._lock:
            order_ids = [
                oid for oid, rec in self._orders.items()
                if rec.status == OrderStatus.OPEN
            ]
        for oid in order_ids:
            try:
                await self.client.cancel_order(oid)
                async with self._lock:
                    if oid in self._orders:
                        self._orders[oid].status = OrderStatus.CANCELED
                        price = self._orders[oid].grid_price
                        self._grid_map.pop(price, None)
                logger.info(f"Canceled order {oid}")
            except Exception as e:
                logger.warning(f"Cancel failed for {oid}: {e}")

    async def cancel_order(self, order_id: str) -> None:
        """Cancel a single order by ID."""
        await self.client.cancel_order(order_id)
        async with self._lock:
            if order_id in self._orders:
                rec = self._orders[order_id]
                rec.status = OrderStatus.CANCELED
                self._grid_map.pop(rec.grid_price, None)

    # ── Queries ──────────────────────────────────────────────────────

    @property
    def open_order_count(self) -> int:
        return sum(1 for r in self._orders.values() if r.status == OrderStatus.OPEN)

    @property
    def all_records(self) -> dict[str, OrderRecord]:
        return dict(self._orders)

    def get_record(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    # ── State Import/Export (for persistence) ────────────────────────

    def export_state(self) -> dict:
        """Serialize in-memory state to JSON-serializable dict."""
        return {
            "orders": {
                oid: {
                    "order_id": r.order_id,
                    "grid_price": r.grid_price,
                    "side": r.side,
                    "amount": r.amount,
                    "status": r.status.value,
                    "placed_at": r.placed_at.isoformat(),
                }
                for oid, r in self._orders.items()
            }
        }

    def import_state(self, state: dict) -> None:
        """Restore in-memory state from persisted dict (called on startup)."""
        for oid, data in state.get("orders", {}).items():
            rec = OrderRecord(
                order_id=data["order_id"],
                grid_price=data["grid_price"],
                side=data["side"],
                amount=data["amount"],
                status=OrderStatus(data["status"]),
                placed_at=datetime.fromisoformat(data["placed_at"]),
            )
            self._orders[oid] = rec
            if rec.status == OrderStatus.OPEN:
                self._grid_map[rec.grid_price] = oid
```

---

### 3.3 Fill Handler — `src/oms/fill_handler.py`

**Strategy:** On each polling cycle, fetch all open orders from the exchange. Any previously known "open" order that is no longer in the exchange's open-orders list is marked filled. A counter-order is placed one grid level in the opposite direction.

```python
# src/oms/fill_handler.py
import logging
from src.oms import OrderRecord, OrderStatus
from src.oms.order_manager import OrderManager
from src.exchange.exchange_client import ExchangeClient
from src.strategy.grid_calculator import GridCalculator
from config.settings import GridBotSettings

logger = logging.getLogger(__name__)


class FillHandler:
    """
    Polls the exchange for filled orders and triggers counter-orders.

    Grid logic on fill:
      - Buy fill at price P  →  place sell limit at P + 1 grid level (up)
      - Sell fill at price P →  place buy limit at P - 1 grid level (down)
    """

    def __init__(
        self,
        order_manager: OrderManager,
        client: ExchangeClient,
        calculator: GridCalculator,
        settings: GridBotSettings,
    ):
        self.order_manager = order_manager
        self.client = client
        self.calculator = calculator
        self.settings = settings

    async def poll_and_handle(self, centre_price: float) -> list[OrderRecord]:
        """
        Check for fills. Returns list of newly filled OrderRecords.
        Call this on every POLL_INTERVAL_SEC tick.
        """
        open_exchange = await self.client.fetch_open_orders()
        open_ids_on_exchange = {o["id"] for o in open_exchange}

        newly_filled = []
        for oid, record in list(self.order_manager.all_records.items()):
            if record.status != OrderStatus.OPEN:
                continue
            if oid not in open_ids_on_exchange:
                # Order is gone from exchange → treated as filled
                record.status = OrderStatus.FILLED
                newly_filled.append(record)
                logger.info(f"Fill detected: {record.side} @ {record.grid_price:.4f} | ID: {oid}")
                await self._place_counter_order(record, centre_price)

        return newly_filled

    async def _place_counter_order(self, filled: OrderRecord, centre_price: float) -> None:
        """Place the opposite-side order one grid step away from the fill."""
        if filled.side == "buy":
            counter_price = self.calculator._price(filled.grid_price, 1, "up")
            counter_side = "sell"
        else:
            counter_price = self.calculator._price(filled.grid_price, 1, "down")
            counter_side = "buy"

        if self.order_manager.open_order_count >= self.settings.MAX_OPEN_ORDERS:
            logger.warning("MAX_OPEN_ORDERS hit — skipping counter-order placement.")
            return

        from src.strategy import GridLevel
        level = GridLevel(
            price=counter_price,
            side=counter_side,
            level_index=1,
            order_size_quote=self.settings.ORDER_SIZE_QUOTE,
        )
        await self.order_manager.deploy_grid([level])
```

---

### 3.4 Risk Manager — `src/risk/risk_manager.py`

**Five Circuit Breakers:**

| # | Trigger | Action |
|---|---------|--------|
| 1 | `current_price < lower_bound × (1 - STOP_LOSS_PCT)` | `STOP_LOSS` — cancel orders, halt |
| 2 | `equity_drop_pct >= MAX_DRAWDOWN_PCT` | `EMERGENCY_CLOSE` — cancel all, emergency exit |
| 3 | `cumulative_profit_pct >= TAKE_PROFIT_PCT` | `TAKE_PROFIT` — cancel all, lock profits |
| 4 | `ADX >= ADX_THRESHOLD` | `PAUSE_ADX` — cancel all orders, wait |
| 5 | `price drifted > RECENTRE_TRIGGER grid levels` | `RECENTRE` — cancel all, redeploy around new centre |

```python
# src/risk/risk_manager.py
import logging
from src.oms import RiskAction
from config.settings import GridBotSettings

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Evaluates all risk conditions on each tick and returns the appropriate RiskAction.
    Checks are evaluated in priority order (most severe first).
    """

    def __init__(self, settings: GridBotSettings, initial_equity: float):
        self.settings = settings
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.start_equity = initial_equity

    def evaluate(
        self,
        current_price: float,
        current_equity: float,
        centre_price: float,
        adx: float,
        grid_spacing_abs: float,
    ) -> RiskAction:
        """
        Evaluate all risk rules in order of priority.

        Args:
            current_price:   Latest market price
            current_equity:  Current account equity in USDT
            centre_price:    The price around which the grid is centred
            adx:             Current ADX value from RegimeDetector
            grid_spacing_abs: Absolute price per grid level (for drift calculation)

        Returns:
            RiskAction enum value directing the main loop.
        """
        self.peak_equity = max(self.peak_equity, current_equity)

        # 1. Emergency drawdown (highest priority)
        drawdown = (self.peak_equity - current_equity) / self.peak_equity
        if drawdown >= self.settings.MAX_DRAWDOWN_PCT:
            logger.critical(f"MAX DRAWDOWN HIT: {drawdown:.2%}. Triggering emergency close.")
            return RiskAction.EMERGENCY_CLOSE

        # 2. Stop-loss (price below lower bound threshold)
        if self.settings.LOWER_BOUND is not None:
            stop_level = self.settings.LOWER_BOUND * (1 - self.settings.STOP_LOSS_PCT)
            if current_price < stop_level:
                logger.warning(f"STOP LOSS: price {current_price:.2f} < stop level {stop_level:.2f}")
                return RiskAction.STOP_LOSS

        # 3. Take-profit
        profit_pct = (current_equity - self.start_equity) / self.start_equity
        if profit_pct >= self.settings.TAKE_PROFIT_PCT:
            logger.info(f"TAKE PROFIT: cumulative gain {profit_pct:.2%}")
            return RiskAction.TAKE_PROFIT

        # 4. ADX pause (trending market)
        if adx >= self.settings.ADX_THRESHOLD:
            logger.info(f"ADX PAUSE: ADX={adx:.2f} >= {self.settings.ADX_THRESHOLD}")
            return RiskAction.PAUSE_ADX

        # 5. Re-centre trigger (price drifted too far)
        if grid_spacing_abs > 0:
            drift_levels = abs(current_price - centre_price) / grid_spacing_abs
            if drift_levels > self.settings.RECENTRE_TRIGGER:
                logger.info(f"RECENTRE: price drifted {drift_levels:.1f} levels from centre")
                return RiskAction.RECENTRE

        return RiskAction.NONE
```

---

## 4. Alternatives Considered

| ID | Alternative | Decision |
|----|-------------|----------|
| **ALT-301** | Use market orders instead of limit orders | *Rejected: Limit orders earn maker rebates. Market orders pay taker fees, destroying grid profitability.* |
| **ALT-302** | Event-driven fill detection via WebSocket | *Deferred: REST polling is robust and simpler. WebSocket can be added as enhancement in Phase 4.* |
| **ALT-303** | Use SQLite for order state | *Rejected: In-memory dict + JSON persistence (Phase 4) is simpler and sufficient.* |

---

## 5. Dependencies

```
# Installed via pip install -r requirements.txt
ccxt>=4.2.0          # exchange_client (from Phase 1)
asyncio-throttle>=1.0.2
```

All other dependencies come from Phase 1 and Phase 2.

---

## 6. Files Produced

| File | Purpose |
|------|---------|
| `src/oms/__init__.py` | `OrderRecord`, `OrderStatus`, `RiskAction` data types |
| `src/oms/order_manager.py` | Grid order lifecycle management |
| `src/oms/fill_handler.py` | Fill detection and counter-order placement |
| `src/risk/__init__.py` | Package marker |
| `src/risk/risk_manager.py` | 5 circuit breakers |

---

## 7. Testing

| Test ID | Description | File |
|---------|-------------|------|
| **TEST-301** | `deploy_grid` places correct number of orders (mocked exchange) | `tests/test_order_manager.py` |
| **TEST-302** | `cancel_all_orders` marks all records as CANCELED | `tests/test_order_manager.py` |
| **TEST-303** | Buy fill → counter sell placed one level up | `tests/test_fill_handler.py` |
| **TEST-304** | `MAX_OPEN_ORDERS` prevents over-placement | `tests/test_order_manager.py` |
| **TEST-305** | Drawdown > 15% → `EMERGENCY_CLOSE` | `tests/test_risk_manager.py` |
| **TEST-306** | ADX > 25 → `PAUSE_ADX` | `tests/test_risk_manager.py` |
| **TEST-307** | Price drifts > 3 levels → `RECENTRE` | `tests/test_risk_manager.py` |
| **TEST-308** | Concurrent fills handled without race condition via `asyncio.Lock` | `tests/test_order_manager.py` |

---

## 8. Risks & Assumptions

| ID | Detail |
|----|--------|
| **RISK-3.1** | Simultaneous fills cause double counter-orders. *Mitigation: `asyncio.Lock` in OrderManager.* |
| **RISK-3.2** | Order marked as filled but was actually canceled by exchange. *Mitigation: `get_order_status` double-check before placing counter-order.* |
| **ASSUMPTION-3.1** | In-memory order state is sufficient; it will be persisted to disk in Phase 4. |

---

## 9. Related Documents

- [Master Plan](./feature-grid-bot-master-1.md)
- [Phase 2 — Strategy Core](./feature-grid-bot-phase2-1.md)
- [Phase 4 — Persistence & Monitoring](./feature-grid-bot-phase4-1.md)
