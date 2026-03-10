"""
src/monitoring/alerting.py
---------------------------
Rate-limited Telegram alerting for key bot events.

Credentials (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) are loaded from .env.
If either credential is missing, alerting is silently disabled — the bot
continues operating normally without alerts.
"""

import asyncio
import logging
import time
from typing import Optional

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# Minimum seconds between Telegram messages (API rate limit buffer).
MIN_INTERVAL_SEC: float = 3.0


class TelegramAlerter:
    """
    Sends Telegram messages for key Grid Bot lifecycle events.

    Built-in rate limiter ensures no more than one message per
    MIN_INTERVAL_SEC seconds, respecting Telegram API limits.
    """

    def __init__(self, token: str, chat_id: str) -> None:
        """
        Initialise the Telegram alerter.

        If token or chat_id is empty, alerting is disabled and all
        send() calls are no-ops. No exception is raised.

        Args:
            token:   Telegram Bot API token from @BotFather.
            chat_id: Target Telegram chat or channel ID.
        """
        self._lock = asyncio.Lock()
        if not token or not chat_id:
            logger.warning(
                "Telegram credentials not configured — alerts disabled."
            )
            self._enabled = False
            self._bot: Optional[Bot] = None
            self._chat_id = ""
            return

        self._bot = Bot(token=token)
        self._chat_id = chat_id
        self._last_sent: float = 0.0
        self._enabled = True

    async def send(self, message: str) -> None:
        """
        Send a Telegram message, rate-limited to MIN_INTERVAL_SEC.

        If alerting is disabled, this method returns immediately.

        Args:
            message: Plain-text message to send.
        """
        if not self._enabled:
            return

        async with self._lock:
            elapsed = time.monotonic() - self._last_sent
            if elapsed < MIN_INTERVAL_SEC:
                await asyncio.sleep(MIN_INTERVAL_SEC - elapsed)
            try:
                await self._bot.send_message(chat_id=self._chat_id, text=message)
                self._last_sent = time.monotonic()
            except TelegramError as exc:
                logger.warning("Telegram send failed: %s", exc)

    async def alert_grid_deployed(
        self, symbol: str, centre: float, n_levels: int
    ) -> None:
        """
        Notify that a new grid has been deployed.

        Args:
            symbol:   Trading pair (e.g. 'BTC/USDT').
            centre:   Centre price of the grid.
            n_levels: Total number of grid levels placed.
        """
        await self.send(
            f"Grid Bot Started\n"
            f"Symbol: {symbol}\n"
            f"Centre: {centre:.4f}\n"
            f"Levels: {n_levels}"
        )

    async def alert_fill(
        self, side: str, price: float, profit: Optional[float] = None
    ) -> None:
        """
        Notify that a grid order was filled.

        Args:
            side:   'buy' or 'sell'.
            price:  Fill price.
            profit: Optional cycle P&L in USDT for completed buy->sell cycles.
        """
        direction = "BUY" if side == "buy" else "SELL"
        msg = f"Fill: {direction} @ {price:.4f}"
        if profit is not None:
            msg += f"\nCycle P&L: {profit:+.4f} USDT"
        await self.send(msg)

    async def alert_risk_action(self, action: str, reason: str) -> None:
        """
        Notify that a risk circuit breaker was triggered.

        Args:
            action: RiskAction enum value string.
            reason: Human-readable description of why the action was triggered.
        """
        await self.send(f"Risk Action: {action}\nReason: {reason}")

    async def alert_shutdown(self, reason: str) -> None:
        """
        Notify that the bot is shutting down.

        Args:
            reason: Description of why the bot is stopping.
        """
        await self.send(f"Bot Shutdown\nReason: {reason}")
