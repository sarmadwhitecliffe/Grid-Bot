from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.models.enums import TradeSide


@pytest.mark.asyncio
async def test_verify_order_immediately_flag():
    """
    Verify that fetch_order is skipped when VERIFY_ORDER_IMMEDIATELY is false.
    """
    # Case 1: True (Default)
    with patch.dict("os.environ", {"VERIFY_ORDER_IMMEDIATELY": "true"}):
        exchange = LiveExchange("binance", "key", "secret", MagicMock())
        exchange.exchange = AsyncMock()
        exchange.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }
        exchange.exchange.fetch_order.return_value = {"id": "123", "status": "closed"}

        await exchange.create_market_order("BTC/USDT", TradeSide.BUY, 1.0)

        exchange.exchange.fetch_order.assert_called_once()

    # Case 2: False
    with patch.dict("os.environ", {"VERIFY_ORDER_IMMEDIATELY": "false"}):
        exchange = LiveExchange("binance", "key", "secret", MagicMock())
        exchange.exchange = AsyncMock()
        exchange.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }

        await exchange.create_market_order("BTC/USDT", TradeSide.BUY, 1.0)

        exchange.exchange.fetch_order.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_trades_for_fees_flag():
    """
    Verify that fetch_my_trades is skipped when FETCH_TRADES_FOR_FEES is false.
    """
    # Case 1: True (Default)
    with patch.dict("os.environ", {"FETCH_TRADES_FOR_FEES": "true"}):
        exchange = LiveExchange("binance", "key", "secret", MagicMock())
        exchange.exchange = AsyncMock()
        exchange.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }
        exchange.exchange.fetch_order.return_value = {"id": "123", "status": "closed"}
        exchange.exchange.fetch_my_trades.return_value = []

        await exchange.create_market_order("BTC/USDT", TradeSide.BUY, 1.0)

        # It calls fetch_my_trades inside _populate_order_fees
        exchange.exchange.fetch_my_trades.assert_called()

    # Case 2: False
    with patch.dict("os.environ", {"FETCH_TRADES_FOR_FEES": "false"}):
        exchange = LiveExchange("binance", "key", "secret", MagicMock())
        exchange.exchange = AsyncMock()
        exchange.exchange.create_market_order.return_value = {
            "id": "123",
            "status": "closed",
        }
        exchange.exchange.fetch_order.return_value = {"id": "123", "status": "closed"}

        await exchange.create_market_order("BTC/USDT", TradeSide.BUY, 1.0)

        exchange.exchange.fetch_my_trades.assert_not_called()
