"""
main.py
-------
Async entry point for the Grid Trading Bot.

Wires all layers together and runs the main trading loop:

  Config → Exchange → Data → Strategy → OMS → Risk → Persistence → Monitoring

Handles:
  - SIGINT / SIGTERM for graceful shutdown.
  - State recovery from JSON on restart.
  - Regime-gated grid deployment and cancellation.
  - Periodic fill polling, risk evaluation, and state persistence.
"""

import asyncio
import logging
import signal
from typing import Optional

import structlog

from config.settings import settings
from src.data.price_feed import PriceFeed
from src.exchange.exchange_client import ExchangeClient
from src.notification import Notifier
from src.oms.fill_handler import FillHandler
from src.oms.order_manager import OrderManager
from src.persistence.state_store import StateStore
from src.risk.risk_manager import RiskManager
from src.strategy import MarketRegime
from src.strategy.regime_detector import RegimeDetector

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
)

log = structlog.get_logger(__name__)

# How long the main loop sleeps between ticks (seconds).
LOOP_SLEEP: int = settings.POLL_INTERVAL_SEC


class GridBot:
    """
    Orchestrates all Grid Bot subsystems within a single async event loop.

    Lifecycle:
      start() → trading loop → stop() on SIGINT/SIGTERM.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._exchange: Optional[ExchangeClient] = None
        self._price_feed: Optional[PriceFeed] = None
        self._notifier: Optional[Notifier] = None
        self._order_manager: Optional[OrderManager] = None
        self._fill_handler: Optional[FillHandler] = None
        self._risk_manager: Optional[RiskManager] = None
        self._state_store: Optional[StateStore] = None
        self._regime_detector: Optional[RegimeDetector] = None
        self._centre_price: Optional[float] = None
        self._initial_equity: Optional[float] = None

    async def _init_components(self) -> None:
        """Instantiate and initialise all subsystem components."""
        log.info("Initialising GridBot components...")

        self._exchange = ExchangeClient(settings=settings)
        await self._exchange.load_markets()

        self._price_feed = PriceFeed(
            client=self._exchange,
            settings=settings,
        )
        self._regime_detector = RegimeDetector(
            adx_threshold=settings.ADX_THRESHOLD,
            bb_width_threshold=0.04,
        )
        self._notifier = Notifier(
            token=settings.TELEGRAM_BOT_TOKEN or "",
            chat_id=settings.TELEGRAM_CHAT_ID or "",
        )
        self._state_store = StateStore(state_file=settings.STATE_FILE)
        self._order_manager = OrderManager(
            client=self._exchange,
            settings=settings,
        )

        from src.strategy.grid_calculator import GridCalculator
        from src.strategy import GridType

        # Instantiate calculator once so we can share it.
        # But wait, we need price_step from markets.
        market = self._exchange.exchange.market(settings.SYMBOL)
        price_step = (
            float(market["precision"]["price"])
            if "precision" in market and "price" in market["precision"]
            else 0.01
        )

        self._calculator = GridCalculator(
            grid_type=GridType(settings.GRID_TYPE),
            spacing_pct=settings.GRID_SPACING_PCT,
            spacing_abs=settings.GRID_SPACING_ABS,
            num_grids_up=settings.NUM_GRIDS_UP,
            num_grids_down=settings.NUM_GRIDS_DOWN,
            order_size_quote=settings.ORDER_SIZE_QUOTE,
            price_step=price_step,
            lower_bound=settings.LOWER_BOUND,
            upper_bound=settings.UPPER_BOUND,
        )

        self._fill_handler = FillHandler(
            order_manager=self._order_manager,
            client=self._exchange,
            calculator=self._calculator,
            settings=settings,
        )
        balance = await self._exchange.fetch_balance()
        initial_equity = float(balance.get("USDT", {}).get("total", 0.0))
        self._initial_equity = initial_equity
        self._risk_manager = RiskManager(
            settings=settings, initial_equity=initial_equity
        )

        log.info("All components initialised.")

    async def _restore_state(self) -> None:
        """Attempt to recover a previous bot state from the JSON state file."""
        state = self._state_store.load()
        if state is None:
            log.info("No saved state — starting fresh.")
            return
        self._centre_price = state.get("centre_price")
        self._initial_equity = state.get("initial_equity")
        orders = state.get("orders")
        if orders:
            self._order_manager.import_state(orders)
        log.info(
            "Restored state",
            centre_price=self._centre_price,
            n_orders=len(orders) if orders else 0,
        )

    async def _persist_state(self) -> None:
        """Write current runtime state to disk for crash recovery."""
        if self._state_store is None or self._order_manager is None:
            return
        state = {
            "centre_price": self._centre_price,
            "initial_equity": self._initial_equity,
            "orders": self._order_manager.export_state(),
        }
        self._state_store.save(state)

    async def _deploy_grid(self, centre: float) -> None:
        """Calculate grid levels and place all limit orders."""
        levels = self._calculator.calculate(centre)
        await self._order_manager.deploy_grid(levels)
        log.info("Grid deployed", centre=centre, n_levels=len(levels))
        if self._notifier:
            await self._notifier.alert_grid_deployed(
                settings.SYMBOL, centre, len(levels)
            )
        self._centre_price = centre

    async def _trading_loop(self) -> None:
        """Main polling loop — runs until self._running is False."""
        while self._running:
            try:
                # 1. Fetch latest candles.
                ohlcv_df = await self._price_feed.get_ohlcv_dataframe()
                if ohlcv_df is None or ohlcv_df.empty:
                    log.warning("Empty OHLCV data — skipping tick.")
                    await asyncio.sleep(LOOP_SLEEP)
                    continue

                # 2. Regime detection.
                regime = self._regime_detector.detect(ohlcv_df)
                log.info(
                    "Regime",
                    regime=regime.regime.value,
                    adx=round(regime.adx, 2),
                    bb_width=round(regime.bb_width, 4),
                )

                # 3. If TRENDING, cancel all orders and wait.
                if regime.regime == MarketRegime.TRENDING:
                    if self._order_manager.open_order_count > 0:
                        log.info("Market trending — cancelling grid.")
                        await self._order_manager.cancel_all_orders()
                        await self._notifier.alert_risk_action(
                            "PAUSE_ADX", regime.reason
                        )
                    await asyncio.sleep(LOOP_SLEEP)
                    continue

                # 4. Deploy grid if not already active.
                if not self._order_manager.open_order_count > 0:
                    ticker = await self._exchange.get_ticker()
                    current_price = float(ticker["last"])
                    if self._initial_equity is None:
                        balance = await self._exchange.fetch_balance()
                        self._initial_equity = float(
                            balance.get("USDT", {}).get("total", 0)
                        )
                    await self._deploy_grid(current_price)

                # 5. Poll for fills and place counter orders.
                ticker = await self._exchange.get_ticker()
                current_price = float(ticker["last"])
                await self._fill_handler.poll_and_handle(current_price)

                # 6. Risk evaluation.
                balance = await self._exchange.fetch_balance()
                current_equity = float(balance.get("USDT", {}).get("total", 0))
                risk_action = self._risk_manager.evaluate(
                    current_price=current_price,
                    current_equity=current_equity,
                    centre_price=self._centre_price or current_price,
                    adx=regime.adx,
                    grid_spacing_abs=settings.GRID_SPACING_ABS,
                )
                if risk_action.action.value != "NONE":
                    log.warning(
                        "Risk action triggered",
                        action=risk_action.action.value,
                        reason=risk_action.reason,
                    )
                    await self._notifier.alert_risk_action(
                        risk_action.action.value, risk_action.reason
                    )
                    await self._order_manager.cancel_all_orders()
                    if risk_action.action.value in (
                        "EMERGENCY_CLOSE",
                        "STOP_LOSS",
                        "TAKE_PROFIT",
                    ):
                        self._state_store.clear()
                        self._running = False
                        break
                    if risk_action.action.value == "RECENTRE":
                        await self._deploy_grid(current_price)

                # 7. Persist state after every tick.
                await self._persist_state()

            except Exception as exc:  # pylint: disable=broad-except
                log.exception("Error in trading loop", error=str(exc))

            await asyncio.sleep(LOOP_SLEEP)

    async def start(self) -> None:
        """Initialise, restore, and begin the trading loop."""
        await self._init_components()
        await self._restore_state()
        self._running = True
        log.info("Grid Bot starting", symbol=settings.SYMBOL)
        await self._trading_loop()

    async def stop(self, reason: str = "SIGINT") -> None:
        """Cancel all orders and save state before exiting."""
        log.info("Shutting down", reason=reason)
        self._running = False
        if self._order_manager:
            await self._order_manager.cancel_all_orders()
        await self._persist_state()
        if self._notifier:
            await self._notifier.alert_shutdown(reason)
        if self._exchange:
            await self._exchange.close()
        log.info("GridBot stopped.")


async def _run() -> None:
    bot = GridBot()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(bot.stop(reason=s.name)),
        )

    await bot.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())
