"""
src/notification/notifier.py
-----------------------------
Unified Telegram notifier for Grid Bot.

Supports:
- Grid deployed notifications
- Tier change notifications
- Heartbeat/system status
- Risk action alerts
- Bot shutdown notifications

Uses httpx for HTTP calls with retry logic and rate-limit handling.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    """Unified Telegram notifier with rich formatting and retry handling."""

    def __init__(self, token: Optional[str], chat_id: Optional[str]) -> None:
        self.token = token
        self.chat_id = chat_id
        self.api_url = (
            f"https://api.telegram.org/bot{self.token}/sendMessage"
            if self.token
            else None
        )

    @staticmethod
    def escape_markdown(text: Any) -> str:
        """Escape special characters for Telegram MarkdownV2."""
        s = str(text)

        try:
            parts = re.split(r"(`[^`]*`)", s)
        except Exception:
            parts = [s]

        special_chars = r"_*[]()~`>#+-=|{}!."

        escaped_parts = []
        for part in parts:
            if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
                escaped_parts.append(part)
            else:
                p = part.replace("\\", "\\\\")
                for ch in special_chars:
                    p = p.replace(ch, f"\\{ch}")
                escaped_parts.append(p)

        return "".join(escaped_parts)

    def format_currency(self, amount: float, symbol: str = "USDT") -> str:
        """Format currency amounts with proper sign indicators."""
        if amount >= 0:
            return f"+{amount:.2f} {symbol}"
        else:
            return f"{amount:.2f} {symbol}"

    def format_percentage(self, value: float) -> str:
        """Format percentage values with proper sign indicators."""
        if value >= 0:
            return f"+{value:.1f}%"
        else:
            return f"{value:.1f}%"

    async def send(self, message: str) -> None:
        """
        Send a message via Telegram with enhanced error handling and retries.
        """
        if not self.api_url or not self.chat_id:
            return

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    send_payload = payload.copy()
                    if send_payload.get("parse_mode") == "MarkdownV2":
                        try:
                            send_payload["text"] = self.escape_markdown(
                                send_payload["text"]
                            )
                        except Exception:
                            send_payload["text"] = message

                    response = await client.post(
                        self.api_url, json=send_payload, timeout=15
                    )

                    if response.status_code == 200:
                        logger.debug("Telegram message sent successfully")
                        return
                    elif response.status_code == 429:
                        retry_after_header = response.headers.get("Retry-After", "1")
                        try:
                            retry_after = int(float(retry_after_header))
                        except Exception:
                            retry_after = 1
                        logger.warning(
                            f"Telegram rate limit hit, waiting {retry_after}s before retry"
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    else:
                        content_type = response.headers.get("content-type", "")
                        error_data = {}
                        if "application/json" in content_type.lower():
                            try:
                                error_data = response.json()
                            except Exception:
                                error_data = {}
                        else:
                            try:
                                error_data = response.json()
                            except Exception:
                                error_data = {}

                        description = str(
                            error_data.get("description", "No description available")
                        )
                        logger.error(
                            f"Telegram API error (HTTP {response.status_code}) - "
                            f"Attempt {attempt + 1}/{max_retries}: {description}"
                        )

                        if "can't parse entities" in description.lower():
                            logger.warning(
                                "Telegram parse error detected; retrying once without parse_mode"
                            )
                            fallback_payload = payload.copy()
                            fallback_payload.pop("parse_mode", None)
                            fallback_payload["text"] = message
                            try:
                                async with httpx.AsyncClient() as client2:
                                    resp2 = await client2.post(
                                        self.api_url, json=fallback_payload, timeout=15
                                    )
                                    if resp2.status_code == 200:
                                        logger.info(
                                            "Telegram message sent successfully with fallback"
                                        )
                                        return
                            except Exception as e:
                                logger.error(
                                    "Error during Telegram fallback send: %s", e
                                )

            except httpx.RequestError as e:
                logger.error(
                    f"Network error sending Telegram message "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error sending Telegram message "
                    f"(attempt {attempt + 1}/{max_retries}): {e}"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)

        logger.error("Failed to send Telegram message after all retry attempts")

    async def alert_grid_deployed(
        self, symbol: str, centre: float, n_levels: int
    ) -> None:
        """
        Send notification when a new grid is deployed.

        Args:
            symbol: Trading pair (e.g. 'BTC/USDT')
            centre: Centre price of the grid
            n_levels: Total number of grid levels placed
        """
        msg = (
            f"🚀 *Grid Started*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {self.escape_markdown(symbol)}\n"
            f"💰 Centre: `{centre:.2f}`\n"
            f"📏 Levels: `{n_levels}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    async def alert_tier_change(
        self,
        symbol: str,
        old_tier: str,
        new_tier: str,
        metrics: Dict[str, float],
    ) -> None:
        """
        Send notification for tier promotion/demotion.

        Args:
            symbol: Trading symbol
            old_tier: Previous tier name
            new_tier: New tier name
            metrics: Performance metrics (win_rate, profit_factor, sharpe_ratio)
        """
        tier_order = ["PROBATION", "CONSERVATIVE", "STANDARD", "AGGRESSIVE", "CHAMPION"]
        old_idx = tier_order.index(old_tier) if old_tier in tier_order else 0
        new_idx = tier_order.index(new_tier) if new_tier in tier_order else 0

        is_promotion = new_idx > old_idx

        if is_promotion:
            emoji = "📈"
            title = "Tier Promotion"
            color = "🟢"
        else:
            emoji = "📉"
            title = "Tier Demotion"
            color = "🔴"

        win_rate = metrics.get("win_rate", 0) * 100 if metrics.get("win_rate") else 0
        pf = metrics.get("profit_factor", 0)
        sharpe = metrics.get("sharpe_ratio", 0)

        msg = (
            f"{emoji} *{title}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 {self.escape_markdown(symbol)}\n"
            f"{color} {self.escape_markdown(old_tier)} → {self.escape_markdown(new_tier)}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Performance:*\n"
            f"  • Win Rate: `{win_rate:.1f}%`\n"
            f"  • Profit Factor: `{pf:.2f}`\n"
            f"  • Sharpe: `{sharpe:.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    async def send_heartbeat(
        self,
        capital: float,
        n_grids: int,
        unrealized_pnl: float = 0.0,
    ) -> None:
        """
        Send hourly heartbeat with system status.

        Args:
            capital: Current capital in USDT
            n_grids: Number of active grids
            unrealized_pnl: Optional unrealized P&L
        """
        now = datetime.now(timezone.utc)
        now_str = now.strftime("%Y-%m-%d %H:%M UTC")

        pnl_str = (
            f" | Unrealized: {self.format_currency(unrealized_pnl)}"
            if unrealized_pnl != 0
            else ""
        )

        msg = (
            f"💓 *System Status*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {now_str}\n"
            f"💰 Capital: ${capital:.0f}{pnl_str}\n"
            f"📊 Grids: `{n_grids}`\n"
            f"🤖 Running normally\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    async def alert_risk_action(self, action: str, reason: str) -> None:
        """
        Send notification when a risk circuit breaker is triggered.

        Args:
            action: Risk action taken (e.g., RECENTRE, PAUSE_ADX, EMERGENCY_CLOSE)
            reason: Human-readable reason
        """
        msg = (
            f"⚠️ *Risk Action*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ {self.escape_markdown(action)}\n"
            f"📝 {self.escape_markdown(reason)}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    async def alert_shutdown(self, reason: str) -> None:
        """
        Send notification when the bot is shutting down.

        Args:
            reason: Shutdown reason (e.g., SIGINT, SIGTERM)
        """
        msg = (
            f"🛑 *Bot Shutdown*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📝 {self.escape_markdown(reason)}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await self.send(msg)

    def format_duration(
        self, start_time: datetime, end_time: Optional[datetime] = None
    ) -> str:
        """Format trade duration in human-readable format."""
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        duration = end_time - start_time
        total_seconds = int(duration.total_seconds())

        if total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes}m"
        elif total_seconds < 86400:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        else:
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            return f"{days}d {hours}h"

    def format_r_multiple(self, r_value: float) -> str:
        """Format R-multiple values with enhanced visualization."""
        if r_value > 0:
            return f"🟢 `{r_value:+.2f}R`"
        elif r_value < 0:
            return f"🔴 `{r_value:+.2f}R`"
        else:
            return f"⚪ `{r_value:.2f}R`"

    def get_profit_emoji(self, pnl: float) -> str:
        """Get appropriate emoji based on profit/loss."""
        if pnl > 50:
            return "🚀"
        elif pnl > 10:
            return "💰"
        elif pnl > 0:
            return "✅"
        elif pnl < -50:
            return "💥"
        elif pnl < -10:
            return "📉"
        else:
            return "❌"

    async def send_partial_close_notification(
        self,
        symbol: str,
        side: str,
        close_amount: Decimal,
        close_percent: float,
        remaining_amount: Decimal,
        partial_pnl: float,
        price: float,
        reason: str,
    ) -> None:
        """Send notification for partial position close."""
        try:
            pnl_emoji = self.get_profit_emoji(partial_pnl)
            side_emoji = "📈" if side == "long" else "📉"

            message = (
                f"✂️ Partial Close\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{side_emoji} `{self.escape_markdown(symbol)}` {self.escape_markdown(side.upper())}\n"
                f"💰 P&L: `{partial_pnl:+.2f} USD` {pnl_emoji}\n"
                f"📊 Remaining: `{remaining_amount:.4f}`\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )

            await self.send(message)

        except Exception as e:
            logger.error(
                f"Error sending partial close notification: {e}", exc_info=True
            )
