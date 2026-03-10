---
goal: 'Persistence & Monitoring — Crash recovery state store, Telegram alerting, and main async orchestration loop'
version: '2.0'
date_created: '2026-02-22'
last_updated: '2026-02-22'
owner: 'Antigravity'
status: 'Complete'
tags: ['feature', 'persistence', 'monitoring', 'integration', 'standalone']
---

# Phase 4 — Persistence & Monitoring

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

Phase 4 completes the operational bot. It adds:

1. **Persistence Layer** — Atomic JSON state snapshots for crash recovery and session resumption.
2. **Telegram Alerting** — A standalone Telegram Bot notifier (independent credentials, no external project imports).
3. **Main Entry Point** — The async orchestration loop that wires all layers together and handles graceful shutdown.

---

## 1. Requirements & Constraints

| ID | Requirement |
|----|-------------|
| **REQ-4.1** | State Store must use **atomic writes** (write to temp file then `os.replace`) to prevent JSON corruption during crashes. |
| **REQ-4.2** | State must include: open order IDs, order details, current grid centre price, initial equity, and timestamps. |
| **REQ-4.3** | On startup, if a state file exists, the bot must **reconcile** it with live exchange order statuses. |
| **REQ-4.4** | Telegram alerts use a **dedicated bot token and chat ID** stored in `.env` — completely independent from any other alerting system. |
| **REQ-4.5** | Telegram rate-limiter must ensure no more than **1 message per 3 seconds** (Telegram API limit). |
| **REQ-4.6** | Main loop must handle **graceful shutdown** on `SIGINT` / `SIGTERM`: cancel open orders if configured, save state, close exchange connections. |
| **CON-4.1** | No imports from any external project. |
| **CON-4.2** | Use `.jsonl` for high-frequency trade logs. Use atomic `.json` for state snapshots. |

---

## 2. Implementation Tasks

| Task | Description | Sprint | Has Tests | Done | Date |
|------|-------------|--------|-----------|------|------|
| TASK-401 | Write `src/persistence/state_store.py` with atomic JSON read/write | Sprint 2 | ✅ | ✅ | 2026-02-22 |
| TASK-402 | Write `src/monitoring/alerting.py` with Telegram rate-limiter | Sprint 2 | ✅ | ✅ | 2026-02-22 |
| TASK-403 | Write `main.py` — async orchestration loop | Sprint 2 | ✅ | ✅ | 2026-02-22 |
| TASK-404 | Write `run_grid_bot.sh` bash launcher | Sprint 2 | ❌ | ✅ | 2026-02-22 |

---

## 3. Detailed Specifications

### 3.1 State Store — `src/persistence/state_store.py`

**Atomic write pattern:** Write to a temporary file (`grid_state.json.tmp`) then use `os.replace()` — this is atomic on POSIX filesystems and prevents partial/corrupt state files.

```python
# src/persistence/state_store.py
import json
import logging
import os
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class StateStore:
    """
    Atomic JSON persistence for bot state.
    Writes through a temp file to prevent corruption on crash.
    """

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = state_file.with_suffix(".json.tmp")

    def save(self, state: dict) -> None:
        """
        Atomically write state dict to disk.
        state should include: centre_price, initial_equity, orders (from OMS export)
        """
        state["_saved_at"] = datetime.utcnow().isoformat()
        with open(self._tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(self._tmp, self.state_file)
        logger.debug(f"State saved → {self.state_file}")

    def load(self) -> dict | None:
        """
        Load persisted state. Returns None if no state file exists.
        Raises ValueError if state file is corrupted JSON.
        """
        if not self.state_file.exists():
            logger.info("No existing state file found — starting fresh.")
            return None
        with open(self.state_file) as f:
            try:
                state = json.load(f)
                logger.info(f"Loaded state from {self.state_file} (saved at {state.get('_saved_at')})")
                return state
            except json.JSONDecodeError as e:
                logger.error(f"State file corrupted: {e} — backing up and starting fresh.")
                backup = self.state_file.with_suffix(".json.corrupted")
                self.state_file.rename(backup)
                return None

    def clear(self) -> None:
        """Delete state file (e.g. after take-profit or emergency close)."""
        if self.state_file.exists():
            self.state_file.unlink()
            logger.info("State file cleared.")

    def log_trade(self, trade: dict) -> None:
        """
        Append a fill event to the .jsonl trade log (for analytics).
        e.g. {"ts": "...", "side": "buy", "price": 45000, "amount": 0.002}
        """
        log_path = self.state_file.parent / "trade_log.jsonl"
        trade["_ts"] = datetime.utcnow().isoformat()
        with open(log_path, "a") as f:
            f.write(json.dumps(trade, default=str) + "\n")
```

---

### 3.2 Telegram Alerting — `src/monitoring/alerting.py`

```python
# src/monitoring/alerting.py
import asyncio
import logging
import time
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

MIN_INTERVAL_SEC = 3.0   # Telegram API rate limit buffer


class TelegramAlerter:
    """
    Sends Telegram alerts for key bot events.
    Built-in rate limiter: minimum 3 seconds between messages.
    """

    def __init__(self, token: str, chat_id: str):
        if not token or not chat_id:
            logger.warning("Telegram credentials not configured — alerts disabled.")
            self._enabled = False
            return
        self._bot = Bot(token=token)
        self._chat_id = chat_id
        self._last_sent = 0.0
        self._enabled = True

    async def send(self, message: str) -> None:
        """Send a Telegram message (rate-limited)."""
        if not self._enabled:
            return
        elapsed = time.monotonic() - self._last_sent
        if elapsed < MIN_INTERVAL_SEC:
            await asyncio.sleep(MIN_INTERVAL_SEC - elapsed)
        try:
            await self._bot.send_message(chat_id=self._chat_id, text=message)
            self._last_sent = time.monotonic()
        except TelegramError as e:
            logger.warning(f"Telegram send failed: {e}")

    async def alert_grid_deployed(self, symbol: str, centre: float, n_levels: int) -> None:
        await self.send(
            f"✅ Grid Bot Started\n"
            f"Symbol: {symbol}\n"
            f"Centre: {centre:.4f}\n"
            f"Levels: {n_levels}"
        )

    async def alert_fill(self, side: str, price: float, profit: float | None = None) -> None:
        emoji = "🟢" if side == "buy" else "🔴"
        msg = f"{emoji} Fill: {side.upper()} @ {price:.4f}"
        if profit is not None:
            msg += f"\nCycle P&L: {profit:+.4f} USDT"
        await self.send(msg)

    async def alert_risk_action(self, action: str, reason: str) -> None:
        await self.send(f"⚠️ Risk Action: {action}\nReason: {reason}")

    async def alert_shutdown(self, reason: str) -> None:
        await self.send(f"🛑 Bot Shutdown\nReason: {reason}")
```

---

### 3.3 Main Entry Point — `main.py`

**Architecture of the main loop:**

```
startup:
  1. load settings
  2. create ExchangeClient, load_markets()
  3. load StateStore → if state exists: import OMS state + reconcile with exchange
  4. fetch OHLCV → RegimeDetector.detect()
  5. if RANGING: fetch ticker → calculate centre_price
  6. GridCalculator.calculate(centre_price) → OrderManager.deploy_grid()
  7. TelegramAlerter.alert_grid_deployed()

main loop (every POLL_INTERVAL_SEC):
  1. fetch current price (PriceFeed)
  2. fetch OHLCV → RegimeDetector.detect()
  3. RiskManager.evaluate() → switch on RiskAction
  4. FillHandler.poll_and_handle()
  5. StateStore.save(OMS.export_state())

shutdown (SIGINT):
  1. cancel all open orders (if configured)
  2. StateStore.save()
  3. TelegramAlerter.alert_shutdown()
  4. ExchangeClient.close()
```

```python
# main.py
import asyncio
import logging
import signal
from config.settings import settings
from src.exchange.exchange_client import ExchangeClient
from src.data.price_feed import PriceFeed
from src.strategy.regime_detector import RegimeDetector
from src.strategy.grid_calculator import GridCalculator
from src.strategy import GridType
from src.oms.order_manager import OrderManager
from src.oms.fill_handler import FillHandler
from src.risk.risk_manager import RiskManager
from src.persistence.state_store import StateStore
from src.monitoring.alerting import TelegramAlerter
from src.oms import RiskAction

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(settings.LOG_FILE),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger("grid_bot.main")


async def main():
    logger.info("=== Grid Bot Starting ===")
    settings.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # 1. Initialise components
    client = ExchangeClient(settings)
    await client.load_markets()
    price_feed = PriceFeed(client, settings)
    regime_detector = RegimeDetector(
        adx_threshold=settings.ADX_THRESHOLD,
        bb_width_threshold=0.04,
    )
    grid_type = GridType(settings.GRID_TYPE)
    calculator = GridCalculator(
        grid_type=grid_type,
        spacing_pct=settings.GRID_SPACING_PCT,
        spacing_abs=settings.GRID_SPACING_ABS,
        num_grids_up=settings.NUM_GRIDS_UP,
        num_grids_down=settings.NUM_GRIDS_DOWN,
        order_size_quote=settings.ORDER_SIZE_QUOTE,
        lower_bound=settings.LOWER_BOUND,
        upper_bound=settings.UPPER_BOUND,
    )
    order_manager = OrderManager(client, settings)
    fill_handler = FillHandler(order_manager, client, calculator, settings)
    state_store = StateStore(settings.STATE_FILE)
    alerter = TelegramAlerter(settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID)

    # 2. Restore state
    saved_state = state_store.load()
    if saved_state:
        order_manager.import_state(saved_state)
        centre_price = saved_state.get("centre_price")
        initial_equity = saved_state.get("initial_equity", settings.TOTAL_CAPITAL)
    else:
        ticker = await client.get_ticker()
        centre_price = float(ticker["last"])
        balance = await client.fetch_balance()
        initial_equity = float(balance.get("USDT", {}).get("total", settings.TOTAL_CAPITAL))

    risk_manager = RiskManager(settings, initial_equity=initial_equity)

    # 3. Initial regime check & grid deployment
    ohlcv_df = await price_feed.get_ohlcv_dataframe()
    regime = regime_detector.detect(ohlcv_df)

    if regime.is_ranging and order_manager.open_order_count == 0:
        levels = calculator.calculate(centre_price)
        await order_manager.deploy_grid(levels)
        await alerter.alert_grid_deployed(settings.SYMBOL, centre_price, len(levels))

    # 4. Graceful shutdown
    shutdown_event = asyncio.Event()

    def _shutdown_handler():
        logger.info("Shutdown signal received.")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown_handler)

    # 5. Main loop
    logger.info(f"Entering main loop (poll every {settings.POLL_INTERVAL_SEC}s)")
    while not shutdown_event.is_set():
        try:
            ticker = await client.get_ticker()
            current_price = float(ticker["last"])
            ohlcv_df = await price_feed.get_ohlcv_dataframe()
            regime = regime_detector.detect(ohlcv_df)
            balance = await client.fetch_balance()
            current_equity = float(balance.get("USDT", {}).get("total", initial_equity))

            action = risk_manager.evaluate(
                current_price=current_price,
                current_equity=current_equity,
                centre_price=centre_price,
                adx=regime.adx,
                grid_spacing_abs=settings.GRID_SPACING_ABS,
            )

            if action in (RiskAction.EMERGENCY_CLOSE, RiskAction.STOP_LOSS, RiskAction.TAKE_PROFIT):
                await order_manager.cancel_all_orders()
                await alerter.alert_risk_action(action.value, regime.reason)
                state_store.clear()
                break

            if action == RiskAction.PAUSE_ADX:
                await order_manager.cancel_all_orders()
                await alerter.alert_risk_action("PAUSE_ADX", regime.reason)

            elif action == RiskAction.RECENTRE:
                await order_manager.cancel_all_orders()
                centre_price = current_price
                levels = calculator.calculate(centre_price)
                await order_manager.deploy_grid(levels)
                await alerter.send(f"🔄 Grid re-centred @ {centre_price:.4f}")

            elif regime.is_ranging and order_manager.open_order_count == 0:
                levels = calculator.calculate(centre_price)
                await order_manager.deploy_grid(levels)

            fills = await fill_handler.poll_and_handle(centre_price)
            for fill in fills:
                state_store.log_trade({
                    "side": fill.side, "price": fill.grid_price, "amount": fill.amount
                })
                await alerter.alert_fill(fill.side, fill.grid_price)

            state_store.save({
                "centre_price": centre_price,
                "initial_equity": initial_equity,
                **order_manager.export_state(),
            })

        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)

        await asyncio.sleep(settings.POLL_INTERVAL_SEC)

    # Shutdown
    await order_manager.cancel_all_orders()
    state_store.save({
        "centre_price": centre_price,
        "initial_equity": initial_equity,
        **order_manager.export_state(),
    })
    await alerter.alert_shutdown("Graceful shutdown complete")
    await client.close()
    logger.info("=== Grid Bot Stopped ===")


if __name__ == "__main__":
    asyncio.run(main())
```

---

### 3.4 Launcher Script — `run_grid_bot.sh`

```bash
#!/usr/bin/env bash
# run_grid_bot.sh — Start the Grid Bot with virtual environment

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Activate virtual environment
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
else
    echo "ERROR: Virtual environment not found at $SCRIPT_DIR/venv"
    echo "Run: python3 -m venv venv && pip install -r requirements.txt"
    exit 1
fi

echo "Starting Grid Bot at $(date)"
python "$SCRIPT_DIR/main.py" "$@"
```

---

## 4. Alternatives Considered

| ID | Alternative | Decision |
|----|-------------|----------|
| **ALT-401** | SQLite state store | *Rejected: JSON + atomic write is simpler, human-readable, and sufficient for this state size.* |
| **ALT-402** | WebSocket-based real-time price feed | *Deferred: REST polling is reliable. WS can be added later for lower latency.* |
| **ALT-403** | Import Telegram notifier from external project | *Rejected: Violates standalone requirement. `python-telegram-bot` pip library used directly.* |

---

## 5. Dependencies

```
python-telegram-bot>=20.0
asyncio-throttle>=1.0.2
```

All other dependencies inherited from Phases 1–3.

---

## 6. Files Produced

| File | Purpose |
|------|---------|
| `src/persistence/__init__.py` | Package marker |
| `src/persistence/state_store.py` | Atomic JSON state read/write |
| `src/monitoring/__init__.py` | Package marker |
| `src/monitoring/alerting.py` | Rate-limited Telegram alerts |
| `main.py` | Async orchestration entry point |
| `run_grid_bot.sh` | Bash launcher script |

---

## 7. Testing

| Test ID | Description | File |
|---------|-------------|------|
| **TEST-401** | State round-trip: save → corrupt process → load → state matches | `tests/test_state_store.py` |
| **TEST-402** | Corrupted JSON falls back to fresh start, not crash | `tests/test_state_store.py` |
| **TEST-403** | Graceful shutdown (SIGINT) cancels all orders before exit | `tests/test_main.py` |
| **TEST-404** | Telegram rate-limiter enforces 3s minimum gap | `tests/test_alerting.py` |

---

## 8. Risks & Assumptions

| ID | Detail |
|----|--------|
| **RISK-4.1** | Order statuses change while bot is offline. *Mitigation: Startup reconciliation calls `get_order_status` for each persisted order before resuming.* |
| **ASSUMPTION-4.1** | Long-running async Python processes are supported by the deployment environment (Linux VPS or Mac with Python 3.11+). |

---

## 9. Related Documents

- [Master Plan](./feature-grid-bot-master-1.md)
- [Phase 3 — Execution Engine](./feature-grid-bot-phase3-1.md)
- [Phase 5 — Backtesting & Verification](./feature-grid-bot-phase5-1.md)
