from unittest.mock import AsyncMock

import httpx
import pytest

from bot_v2.notifications.notifier import Notifier


class DummyResponse:
    def __init__(self, status_code=200, headers=None, json_data=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data
        self.text = text

    def json(self):
        if isinstance(self._json, Exception):
            raise self._json
        return self._json


class DummyAsyncClient:
    def __init__(self, responses):
        # responses: iterable of DummyResponse or exceptions to raise
        self._responses = list(responses)
        self.post_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json, timeout):
        self.post_calls.append((url, json, timeout))
        if not self._responses:
            # Default to success if nothing left
            return DummyResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                json_data={"ok": True},
            )
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.mark.asyncio
async def test_escape_markdown_basic():
    n = Notifier(token=None, chat_id=None)
    s = "Hello_world!*[]()~`>#+-=|{}.!"
    escaped = n.escape_markdown(s)
    # Ensure known special characters are escaped with a backslash
    special_chars = "_*[]()~`>#+-=|{}!."
    for ch in special_chars:
        assert f"\\{ch}" in escaped, f"Character {ch!r} was not escaped in {escaped!r}"


@pytest.mark.asyncio
async def test_send_success_monkeypatch(monkeypatch):
    # Successful 200 response and parse_mode present
    resp = DummyResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        json_data={"ok": True},
    )
    client = DummyAsyncClient([resp])

    async def client_factory():
        return client

    monkeypatch.setattr(httpx, "AsyncClient", lambda: client)

    n = Notifier(token="T", chat_id="123")
    await n.send("Test message")

    assert client.post_calls, "Expected AsyncClient.post to be called"
    _url, payload, _timeout = client.post_calls[0]
    assert payload.get("parse_mode") == "MarkdownV2"
    assert payload.get("text") == "Test message"


@pytest.mark.asyncio
async def test_send_rate_limit_then_success(monkeypatch):
    # First response is 429 with Retry-After, then 200
    r1 = DummyResponse(
        status_code=429,
        headers={"Retry-After": "0.1", "content-type": "application/json"},
        json_data={"ok": False, "description": "Too many"},
    )
    r2 = DummyResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        json_data={"ok": True},
    )
    client = DummyAsyncClient([r1, r2])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: client)

    n = Notifier(token="T", chat_id="123")
    await n.send("Retry message")

    # Two post calls expected (retry)
    assert len(client.post_calls) >= 2


@pytest.mark.asyncio
async def test_send_non_json_error(monkeypatch):
    # Response with non-json content-type and json() raising
    r = DummyResponse(
        status_code=500,
        headers={"content-type": "text/html"},
        json_data=ValueError("not json"),
        text="<html>error</html>",
    )
    client = DummyAsyncClient([r])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: client)

    n = Notifier(token="T", chat_id="123")
    # Should not raise
    await n.send("Should handle non-json error")


@pytest.mark.asyncio
async def test_send_request_error(monkeypatch):
    # Simulate network error from httpx
    err = httpx.RequestError("network fail")
    client = DummyAsyncClient([err])
    monkeypatch.setattr(httpx, "AsyncClient", lambda: client)

    n = Notifier(token="T", chat_id="123")
    # Should not raise even if request fails
    await n.send("Network failure test")


@pytest.mark.asyncio
async def test_send_partial_close_notification_calls_send():
    n = Notifier(token=None, chat_id=None)
    n.send = AsyncMock()

    await n.send_partial_close_notification(
        symbol="HYPE/USDT",
        side="long",
        close_amount=1.234,
        close_percent=30.0,
        remaining_amount=2.345,
        partial_pnl=12.34,
        price=38.12,
        reason="TP1a",
    )

    assert n.send.called, "Expected send_partial_close_notification to call send()"
    message_arg = n.send.call_args[0][0]
    assert "Partial Close" in message_arg
    assert "HYPE" in message_arg
    assert "12.34 USD" in message_arg
    assert "2.345" in message_arg  # remaining amount
    assert "📈" in message_arg  # long emoji


@pytest.mark.asyncio
async def test_send_partial_close_notification_short_position():
    n = Notifier(token=None, chat_id=None)
    n.send = AsyncMock()

    await n.send_partial_close_notification(
        symbol="BTC/USDT",
        side="short",
        close_amount=0.5,
        close_percent=25.0,
        remaining_amount=1.5,
        partial_pnl=-25.50,
        price=45000.0,
        reason="TP1a",
    )

    assert n.send.called
    message_arg = n.send.call_args[0][0]

    # Check the message structure
    lines = message_arg.split("\n")
    assert lines[0] == "✂️ Partial Close"
    assert "━━━━━━━━━━━━━━━━━━━━" in lines[1]
    assert "📉 `BTC/USDT` SHORT" in lines[2]
    assert "💰 P&L: `-25.50 USD` 📉" in lines[3]
    assert "📊 Remaining: `1.5000`" in lines[4]
    assert "━━━━━━━━━━━━━━━━━━━━" in lines[5]


@pytest.mark.asyncio
async def test_format_currency():
    """Test currency formatting with different symbols and values"""
    from decimal import Decimal

    n = Notifier(token=None, chat_id=None)

    # Test positive values
    assert n.format_currency(Decimal("123.456")) == "+123.46 USDT"
    assert n.format_currency(Decimal("123.456"), "BTC") == "+123.46 BTC"

    # Test negative values
    assert n.format_currency(Decimal("-123.456")) == "-123.46 USDT"
    assert n.format_currency(Decimal("-123.456"), "BTC") == "-123.46 BTC"

    # Test zero
    assert n.format_currency(Decimal("0")) == "+0.00 USDT"


@pytest.mark.asyncio
async def test_format_percentage():
    """Test percentage formatting"""
    n = Notifier(token=None, chat_id=None)

    assert n.format_percentage(0.1) == "+0.1%"
    assert n.format_percentage(-0.1) == "-0.1%"
    assert n.format_percentage(0.0) == "+0.0%"
    assert n.format_percentage(1.0) == "+1.0%"
    assert n.format_percentage(-1.0) == "-1.0%"


@pytest.mark.asyncio
async def test_format_price():
    """Test price formatting with different decimal places"""
    from decimal import Decimal

    n = Notifier(token=None, chat_id=None)

    # Default 4 decimals
    assert n.format_price(Decimal("123.456789")) == "123.4568"

    # Custom decimals
    assert n.format_price(Decimal("123.456789"), 2) == "123.46"
    assert n.format_price(Decimal("123.456789"), 6) == "123.456789"
    assert n.format_price(Decimal("123.456789"), 0) == "123"


@pytest.mark.asyncio
async def test_format_r_multiple():
    """Test R-multiple formatting with emojis"""
    from decimal import Decimal

    n = Notifier(token=None, chat_id=None)

    # Positive R
    assert n.format_r_multiple(Decimal("1.5")) == "🟢 `+1.50R`"
    assert n.format_r_multiple(Decimal("0.5")) == "🟢 `+0.50R`"

    # Negative R
    assert n.format_r_multiple(Decimal("-1.5")) == "🔴 `-1.50R`"
    assert n.format_r_multiple(Decimal("-0.5")) == "🔴 `-0.50R`"

    # Zero R
    assert n.format_r_multiple(Decimal("0")) == "⚪ `0.00R`"


@pytest.mark.asyncio
async def test_get_profit_emoji():
    """Test profit/loss emoji selection"""
    from decimal import Decimal

    n = Notifier(token=None, chat_id=None)

    # Large positive
    assert n.get_profit_emoji(Decimal("100")) == "🚀"
    assert n.get_profit_emoji(Decimal("51")) == "🚀"

    # Medium positive
    assert n.get_profit_emoji(Decimal("50")) == "💰"
    assert n.get_profit_emoji(Decimal("20")) == "💰"
    assert n.get_profit_emoji(Decimal("11")) == "💰"

    # Small positive
    assert n.get_profit_emoji(Decimal("10")) == "✅"
    assert n.get_profit_emoji(Decimal("1")) == "✅"
    assert n.get_profit_emoji(Decimal("0.01")) == "✅"

    # Small negative
    assert n.get_profit_emoji(Decimal("-1")) == "❌"
    assert n.get_profit_emoji(Decimal("-9")) == "❌"
    assert n.get_profit_emoji(Decimal("-10")) == "❌"

    # Medium negative
    assert n.get_profit_emoji(Decimal("-11")) == "📉"
    assert n.get_profit_emoji(Decimal("-49")) == "📉"

    # Large negative
    assert n.get_profit_emoji(Decimal("-50")) == "📉"
    assert n.get_profit_emoji(Decimal("-51")) == "💥"
    assert n.get_profit_emoji(Decimal("-100")) == "💥"


@pytest.mark.asyncio
async def test_format_duration():
    """Test duration formatting"""
    from datetime import datetime, timedelta, timezone

    n = Notifier(token=None, chat_id=None)

    # Test with start and end times
    start = datetime(2025, 11, 23, 10, 0, 0, tzinfo=timezone.utc)
    end = datetime(
        2025, 11, 23, 12, 30, 45, tzinfo=timezone.utc
    )  # 2 hours 30 minutes 45 seconds

    assert n.format_duration(start, end) == "2h 30m"

    # Test less than 1 hour
    short_end = start + timedelta(minutes=45)
    assert n.format_duration(start, short_end) == "45m"

    # Test more than 1 day
    far_end = start + timedelta(days=1, hours=2)
    assert n.format_duration(start, far_end) == "1d 2h"


@pytest.mark.asyncio
async def test_create_progress_bar():
    """Test progress bar creation"""
    n = Notifier(token=None, chat_id=None)

    # Full progress
    assert n.create_progress_bar(10, 10) == "▓▓▓▓▓▓▓▓▓▓"

    # Half progress
    assert n.create_progress_bar(5, 10) == "▓▓▓▓▓░░░░░"

    # No progress
    assert n.create_progress_bar(0, 10) == "░░░░░░░░░░"

    # Custom length
    assert n.create_progress_bar(2, 4, 6) == "▓▓▓░░░"

    # Edge case: zero maximum
    assert n.create_progress_bar(1, 0) == "░░░░░░░░░░"


@pytest.mark.asyncio
async def test_format_header():
    """Test header formatting"""
    n = Notifier(token=None, chat_id=None)

    # Default icon
    assert n.format_header("Test Title") == "📊 *Test Title*"

    # Custom icon
    assert n.format_header("Test Title", "🚀") == "🚀 *Test Title*"
