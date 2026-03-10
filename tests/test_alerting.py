"""
tests/test_alerting.py
----------------------
Test suite for TelegramAlerter rate-limited alerting.

Verifies:
- Initialization with valid and missing credentials
- Rate limiting enforcement (MIN_INTERVAL_SEC)
- Alert message formatting for all event types
- Graceful degradation when alerting disabled
- TelegramError handling without crashes
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TelegramError

from src.monitoring.alerting import MIN_INTERVAL_SEC, TelegramAlerter


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


def test_init_with_valid_credentials():
    """
    When initialized with valid token and chat_id, alerting is enabled.
    """
    alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
    
    assert alerter._enabled is True
    assert alerter._bot is not None
    assert alerter._chat_id == "123456789"
    assert alerter._last_sent == 0.0


def test_init_with_empty_token_disables_alerting():
    """
    When token is empty, alerting is disabled without raising exceptions.
    """
    alerter = TelegramAlerter(token="", chat_id="123456789")
    
    assert alerter._enabled is False
    assert alerter._bot is None
    assert alerter._chat_id == ""


def test_init_with_empty_chat_id_disables_alerting():
    """
    When chat_id is empty, alerting is disabled without raising exceptions.
    """
    alerter = TelegramAlerter(token="valid_token", chat_id="")
    
    assert alerter._enabled is False
    assert alerter._bot is None
    assert alerter._chat_id == ""


def test_init_with_both_empty_disables_alerting():
    """
    When both credentials are empty, alerting is disabled gracefully.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    assert alerter._enabled is False
    assert alerter._bot is None


def test_init_logs_warning_when_credentials_missing(caplog):
    """
    When credentials are missing, a warning is logged.
    """
    with caplog.at_level("WARNING"):
        TelegramAlerter(token="", chat_id="123456789")
    
    assert "Telegram credentials not configured" in caplog.text
    assert "alerts disabled" in caplog.text


# ---------------------------------------------------------------------------
# Rate Limiting Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limiter_enforces_min_interval():
    """
    Rate limiter enforces MIN_INTERVAL_SEC delay between messages.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        # Send first message
        start = time.monotonic()
        await alerter.send("Message 1")
        
        # Send second message immediately
        await alerter.send("Message 2")
        elapsed = time.monotonic() - start
        
        # Total elapsed should be >= MIN_INTERVAL_SEC
        assert elapsed >= MIN_INTERVAL_SEC
        assert mock_bot.send_message.call_count == 2


@pytest.mark.asyncio
async def test_rate_limiter_allows_immediate_first_message():
    """
    First message is sent immediately without delay.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        start = time.monotonic()
        await alerter.send("First message")
        elapsed = time.monotonic() - start
        
        # First message should send instantly (< 0.5s tolerance)
        assert elapsed < 0.5
        mock_bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limiter_tracks_last_sent_time():
    """
    Rate limiter correctly updates _last_sent timestamp after sending.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        before = time.monotonic()
        await alerter.send("Test message")
        after = time.monotonic()
        
        # _last_sent should be updated to roughly current time
        assert before <= alerter._last_sent <= after


# ---------------------------------------------------------------------------
# Message Formatting Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_grid_deployed_formats_message():
    """
    alert_grid_deployed() formats message with symbol, centre, and levels.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_grid_deployed(
            symbol="BTC/USDT", centre=30000.0, n_levels=20
        )
        
        # Verify send_message was called with formatted text
        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        
        assert call_args.kwargs["chat_id"] == "123456789"
        message = call_args.kwargs["text"]
        assert "Grid Bot Started" in message
        assert "BTC/USDT" in message
        assert "30000.0000" in message
        assert "20" in message


@pytest.mark.asyncio
async def test_alert_fill_formats_buy_message():
    """
    alert_fill() formats BUY fill message correctly.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_fill(side="buy", price=29500.0)
        
        mock_bot.send_message.assert_called_once()
        message = mock_bot.send_message.call_args.kwargs["text"]
        
        assert "BUY" in message
        assert "29500.0000" in message
        assert "Cycle P&L" not in message  # No profit provided


@pytest.mark.asyncio
async def test_alert_fill_formats_sell_message_with_profit():
    """
    alert_fill() formats SELL fill message with profit when provided.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_fill(side="sell", price=30500.0, profit=25.50)
        
        mock_bot.send_message.assert_called_once()
        message = mock_bot.send_message.call_args.kwargs["text"]
        
        assert "SELL" in message
        assert "30500.0000" in message
        assert "Cycle P&L" in message
        assert "+25.5000 USDT" in message


@pytest.mark.asyncio
async def test_alert_risk_action_formats_message():
    """
    alert_risk_action() formats message with action and reason.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_risk_action(
            action="PAUSE_TRADING", reason="ADX > 25 (trending market)"
        )
        
        mock_bot.send_message.assert_called_once()
        message = mock_bot.send_message.call_args.kwargs["text"]
        
        assert "Risk Action: PAUSE_TRADING" in message
        assert "Reason: ADX > 25" in message


@pytest.mark.asyncio
async def test_alert_shutdown_formats_message():
    """
    alert_shutdown() formats message with shutdown reason.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_shutdown(reason="Max drawdown exceeded")
        
        mock_bot.send_message.assert_called_once()
        message = mock_bot.send_message.call_args.kwargs["text"]
        
        assert "Bot Shutdown" in message
        assert "Reason: Max drawdown exceeded" in message


# ---------------------------------------------------------------------------
# Disabled Alerting Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_returns_immediately_when_disabled():
    """
    When alerting is disabled, send() returns immediately without delay.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    start = time.monotonic()
    await alerter.send("Test message")
    elapsed = time.monotonic() - start
    
    # Should return instantly (< 0.1s)
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_alert_grid_deployed_no_op_when_disabled():
    """
    alert_grid_deployed() is no-op when alerting disabled.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    # Should not raise exception
    await alerter.alert_grid_deployed(symbol="BTC/USDT", centre=30000.0, n_levels=20)


@pytest.mark.asyncio
async def test_alert_fill_no_op_when_disabled():
    """
    alert_fill() is no-op when alerting disabled.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    # Should not raise exception
    await alerter.alert_fill(side="buy", price=29500.0, profit=10.0)


@pytest.mark.asyncio
async def test_alert_risk_action_no_op_when_disabled():
    """
    alert_risk_action() is no-op when alerting disabled.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    # Should not raise exception
    await alerter.alert_risk_action(action="PAUSE", reason="Test")


@pytest.mark.asyncio
async def test_alert_shutdown_no_op_when_disabled():
    """
    alert_shutdown() is no-op when alerting disabled.
    """
    alerter = TelegramAlerter(token="", chat_id="")
    
    # Should not raise exception
    await alerter.alert_shutdown(reason="Test shutdown")


# ---------------------------------------------------------------------------
# Error Handling Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_telegram_error_logged_as_warning(caplog):
    """
    When send_message raises TelegramError, it is logged and not re-raised.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramError("Network timeout")
        )
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        with caplog.at_level("WARNING"):
            # Should not raise exception
            await alerter.send("Test message")
        
        assert "Telegram send failed" in caplog.text
        assert "Network timeout" in caplog.text


@pytest.mark.asyncio
async def test_telegram_error_does_not_crash_bot():
    """
    TelegramError during send does not propagate up to crash the bot.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramError("Bad request")
        )
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        # Should complete without exception
        try:
            await alerter.send("Test message")
        except TelegramError:
            pytest.fail("TelegramError should not propagate")


@pytest.mark.asyncio
async def test_multiple_telegram_errors_handled_gracefully():
    """
    Multiple consecutive TelegramErrors are handled without breaking state.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramError("Persistent failure")
        )
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        # Send multiple messages, all should fail gracefully
        for _ in range(3):
            await alerter.send("Test message")
        
        # Alerter should still be enabled
        assert alerter._enabled is True


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_empty_message():
    """
    Sending an empty message does not crash.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.send("")
        
        mock_bot.send_message.assert_called_once_with(chat_id="123456789", text="")


@pytest.mark.asyncio
async def test_alert_fill_with_negative_profit():
    """
    alert_fill() correctly formats negative profit (loss).
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_fill(side="sell", price=29000.0, profit=-15.75)
        
        message = mock_bot.send_message.call_args.kwargs["text"]
        assert "-15.7500 USDT" in message


@pytest.mark.asyncio
async def test_alert_grid_deployed_with_zero_levels():
    """
    alert_grid_deployed() handles edge case of zero levels.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        await alerter.alert_grid_deployed(symbol="BTC/USDT", centre=30000.0, n_levels=0)
        
        message = mock_bot.send_message.call_args.kwargs["text"]
        assert "Levels: 0" in message


@pytest.mark.asyncio
async def test_concurrent_sends_respect_rate_limit():
    """
    Multiple concurrent send() calls respect rate limiting.
    """
    with patch("src.monitoring.alerting.Bot") as mock_bot_class:
        mock_bot = MagicMock()
        mock_bot.send_message = AsyncMock()
        mock_bot_class.return_value = mock_bot
        
        alerter = TelegramAlerter(token="valid_token", chat_id="123456789")
        
        start = time.monotonic()
        # Send 3 messages concurrently
        await asyncio.gather(
            alerter.send("Msg 1"),
            alerter.send("Msg 2"),
            alerter.send("Msg 3"),
        )
        elapsed = time.monotonic() - start
        
        # Should take at least 2 * MIN_INTERVAL_SEC for 3 messages
        expected_min = 2 * MIN_INTERVAL_SEC
        assert elapsed >= expected_min
        assert mock_bot.send_message.call_count == 3
