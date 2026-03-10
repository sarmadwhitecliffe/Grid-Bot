"""
Telegram Notifier - EXACT copy from bot_v1 (bot.py lines 1661-1825)

Enhanced Telegram notifier with rich formatting and improved message handling.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class Notifier:
    """Enhanced Telegram notifier with rich formatting and improved message handling."""

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
        """Escape special characters for Telegram MarkdownV2.

        Telegram MarkdownV2 requires many characters to be escaped with a
        backslash. This helper ensures that any user-provided string won't
        break the message formatting. It intentionally does NOT escape
        characters inside explicit code spans that the caller writes (i.e.,
        backticks in the template). Callers should avoid inserting raw
        formatting tokens into templates when possible.
        """
        # Convert to str first
        s = str(text)

        # We'll preserve inline code spans wrapped in single backticks: `code`
        # and only escape special MarkdownV2 characters outside those spans.
        # Split the text into segments that are either code spans (kept as-is)
        # or non-code text (escaped).
        try:
            parts = re.split(r"(`[^`]*`)", s)
        except Exception:
            parts = [s]

        special_chars = r"_*[]()~`>#+-=|{}!."

        escaped_parts = []
        for part in parts:
            if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
                # It's an inline code span; leave it unchanged
                escaped_parts.append(part)
            else:
                # Escape backslash first
                p = part.replace("\\", "\\\\")
                # Escape special MarkdownV2 characters
                for ch in special_chars:
                    p = p.replace(ch, f"\\{ch}")
                escaped_parts.append(p)

        return "".join(escaped_parts)

    def format_currency(self, amount: Decimal, symbol: str = "USDT") -> str:
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

    def format_price(self, price: Decimal, decimals: int = 4) -> str:
        """Format price values with consistent decimal places."""
        return f"{price:.{decimals}f}"

    def format_r_multiple(self, r_value: Decimal) -> str:
        """Format R-multiple values with enhanced visualization."""
        if r_value > 0:
            return f"🟢 `{r_value:+.2f}R`"
        elif r_value < 0:
            return f"🔴 `{r_value:+.2f}R`"
        else:
            return f"⚪ `{r_value:.2f}R`"

    def get_profit_emoji(self, pnl: Decimal) -> str:
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

    def format_duration(
        self, start_time: datetime, end_time: Optional[datetime] = None
    ) -> str:
        """Format trade duration in human-readable format."""
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        duration = end_time - start_time
        total_seconds = int(duration.total_seconds())

        if total_seconds < 3600:  # Less than 1 hour
            minutes = total_seconds // 60
            return f"{minutes}m"
        elif total_seconds < 86400:  # Less than 1 day
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"
        else:  # 1 day or more
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            return f"{days}d {hours}h"

    def create_progress_bar(
        self, current: float, maximum: float, length: int = 10
    ) -> str:
        """Create a visual progress bar using Unicode characters."""
        if maximum <= 0:
            return "░" * length

        progress = min(current / maximum, 1.0)
        filled = int(progress * length)
        empty = length - filled

        return "▓" * filled + "░" * empty

    def format_header(self, title: str, icon: str = "📊") -> str:
        """Create a formatted header with dividers."""
        escaped_title = self.escape_markdown(title)
        return f"{icon} *{escaped_title}*"

    async def send(self, message: str) -> None:
        """
        Send a message via Telegram with enhanced error handling and retries.

        Args:
            message: The message text to send
        """
        if not self.api_url or not self.chat_id:
            return

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            # Use MarkdownV2 for richer formatting; callers must ensure
            # text is escaped appropriately via `escape_markdown`.
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient() as client:
                    # Defensive: if using MarkdownV2, ensure the message is escaped
                    # here so callers who forget to escape don't cause a hard error.
                    send_payload = payload.copy()
                    if send_payload.get("parse_mode") == "MarkdownV2":
                        try:
                            send_payload["text"] = self.escape_markdown(
                                send_payload["text"]
                            )
                        except Exception:
                            # If escaping fails for any reason, fall back to original text
                            logger.exception(
                                "Error escaping Telegram message; sending unescaped text instead"
                            )
                            send_payload["text"] = message

                    response = await client.post(
                        self.api_url, json=send_payload, timeout=15
                    )

                    if response.status_code == 200:
                        logger.debug("Telegram message sent successfully")
                        return
                    elif response.status_code == 429:  # Rate limited
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
                        # Be tolerant of content-type header variants (e.g.,
                        # 'application/json; charset=utf-8'). Attempt to
                        # parse JSON safely.
                        error_data = {}
                        content_type = response.headers.get("content-type", "")
                        if "application/json" in content_type.lower():
                            try:
                                error_data = response.json()
                            except Exception:
                                error_data = {}
                        else:
                            # Try JSON parse as a fallback
                            try:
                                error_data = response.json()
                            except Exception:
                                error_data = {}

                        description = str(
                            error_data.get("description", "No description available")
                        )
                        logger.error(
                            f"Telegram API error (HTTP {response.status_code}) - Attempt {attempt + 1}/{max_retries}: {description}"
                        )

                        # If Telegram complains about parsing entities, attempt
                        # one fallback send using plain text (no parse_mode).
                        try:
                            desc_lower = description.lower()
                        except Exception:
                            desc_lower = ""

                        if (
                            "can't parse entities" in desc_lower
                            or "can't parse entities" in response.text.lower()
                        ):
                            logger.warning(
                                "Telegram parse error detected; retrying once without parse_mode as a fallback"
                            )
                            fallback_payload = payload.copy()
                            # Remove parse_mode entirely for plain text send; some
                            # Telegram API versions reject null/None parse_mode values.
                            fallback_payload.pop("parse_mode", None)
                            # Send plain text (unescaped) to avoid double-escaping issues
                            fallback_payload["text"] = message
                            try:
                                async with httpx.AsyncClient() as client2:
                                    resp2 = await client2.post(
                                        self.api_url, json=fallback_payload, timeout=15
                                    )
                                    if resp2.status_code == 200:
                                        logger.info(
                                            "Telegram message sent successfully with fallback plain text"
                                        )
                                        return
                                    else:
                                        logger.error(
                                            "Telegram fallback send failed (HTTP %s): %s",
                                            resp2.status_code,
                                            resp2.text,
                                        )
                            except Exception as e:
                                logger.error(
                                    "Error during Telegram fallback send: %s", e
                                )

            except httpx.RequestError as e:
                logger.error(
                    f"Network error sending Telegram message (attempt {attempt + 1}/{max_retries}): {e}"
                )
            except Exception as e:
                # httpx raises various subclasses; catch-all here to avoid
                # the notifier bringing down the bot. Detailed info is
                # logged for diagnosis.
                logger.error(
                    f"Unexpected error sending Telegram message (attempt {attempt + 1}/{max_retries}): {e}"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(2**attempt)  # Exponential backoff

        logger.error("Failed to send Telegram message after all retry attempts")

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
        """
        Send notification for partial position close (TP1a).

        Args:
            symbol: Trading symbol
            side: Position side (long/short)
            close_amount: Amount closed
            close_percent: Percentage of position closed
            remaining_amount: Amount remaining
            partial_pnl: Partial profit/loss
            price: Exit price
            reason: Exit reason
        """
        try:
            pnl_emoji = self.get_profit_emoji(Decimal(str(partial_pnl)))
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
