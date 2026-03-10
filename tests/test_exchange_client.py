"""
tests/test_exchange_client.py
------------------------------
Unit tests for src/exchange/exchange_client.py.

All exchange calls are mocked — no live API connectivity required.
Tests cover initialization, order operations, data fetching, and retry logic.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt.async_support as ccxt
import pytest

from config.settings import GridBotSettings
from src.exchange.exchange_client import RETRY_DELAYS, ExchangeClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ccxt_exchange() -> MagicMock:
    """Return a mock ccxt exchange instance with async methods."""
    exchange = MagicMock(spec=ccxt.Exchange)
    exchange.load_markets = AsyncMock(return_value={})
    exchange.fetch_ticker = AsyncMock(
        return_value={"last": 30000.0, "bid": 29995.0, "ask": 30005.0}
    )
    exchange.create_limit_order = AsyncMock(
        return_value={"id": "order-001", "status": "open", "price": 30000.0, "amount": 0.01}
    )
    exchange.cancel_order = AsyncMock(return_value={"id": "order-001", "status": "canceled"})
    exchange.fetch_order = AsyncMock(
        return_value={"id": "order-001", "status": "open", "price": 30000.0}
    )
    exchange.fetch_open_orders = AsyncMock(
        return_value=[
            {"id": "order-001", "status": "open", "price": 30000.0},
            {"id": "order-002", "status": "open", "price": 29500.0},
        ]
    )
    exchange.fetch_balance = AsyncMock(
        return_value={
            "BTC": {"free": 1.0, "used": 0.5, "total": 1.5},
            "USDT": {"free": 10000.0, "used": 2000.0, "total": 12000.0},
        }
    )
    exchange.fetch_ohlcv = AsyncMock(
        return_value=[
            [1640000000000, 29000, 29100, 28900, 29050, 100],
            [1640003600000, 29050, 29200, 29000, 29150, 120],
        ]
    )
    exchange.close = AsyncMock()
    return exchange


@pytest.fixture
def exchange_client(
    base_settings: GridBotSettings, mock_ccxt_exchange: MagicMock
) -> ExchangeClient:
    """Return an ExchangeClient instance with mocked ccxt exchange."""
    with patch(f"ccxt.async_support.{base_settings.EXCHANGE_ID}", return_value=mock_ccxt_exchange):
        client = ExchangeClient(base_settings)
        client.exchange = mock_ccxt_exchange
        return client


# ---------------------------------------------------------------------------
# Initialization Tests
# ---------------------------------------------------------------------------


class TestInitialization:
    def test_init_creates_exchange_with_rate_limit(self, base_settings: GridBotSettings) -> None:
        """ExchangeClient should instantiate ccxt exchange with enableRateLimit=True."""
        with patch(f"ccxt.async_support.{base_settings.EXCHANGE_ID}") as mock_class:
            mock_class.return_value = MagicMock()
            client = ExchangeClient(base_settings)
            
            mock_class.assert_called_once()
            call_args = mock_class.call_args[0][0]
            assert call_args["enableRateLimit"] is True
            assert call_args["apiKey"] == base_settings.API_KEY
            assert call_args["secret"] == base_settings.API_SECRET

    def test_init_futures_sets_default_type(self, base_settings: GridBotSettings) -> None:
        """When MARKET_TYPE is 'futures', defaultType should be set to 'future'."""
        base_settings.MARKET_TYPE = "futures"
        with patch(f"ccxt.async_support.{base_settings.EXCHANGE_ID}") as mock_class:
            mock_class.return_value = MagicMock()
            client = ExchangeClient(base_settings)
            
            call_args = mock_class.call_args[0][0]
            assert "options" in call_args
            assert call_args["options"]["defaultType"] == "future"

    def test_init_testnet_enables_testnet_flag(self, base_settings: GridBotSettings) -> None:
        """When TESTNET is True, testnet option should be set."""
        base_settings.TESTNET = True
        with patch(f"ccxt.async_support.{base_settings.EXCHANGE_ID}") as mock_class:
            mock_class.return_value = MagicMock()
            client = ExchangeClient(base_settings)
            
            call_args = mock_class.call_args[0][0]
            assert "options" in call_args
            assert call_args["options"]["testnet"] is True

    def test_init_stores_symbol(self, base_settings: GridBotSettings) -> None:
        """ExchangeClient should store the configured symbol."""
        with patch(f"ccxt.async_support.{base_settings.EXCHANGE_ID}") as mock_class:
            mock_class.return_value = MagicMock()
            client = ExchangeClient(base_settings)
            
            assert client.symbol == base_settings.SYMBOL


# ---------------------------------------------------------------------------
# load_markets Tests
# ---------------------------------------------------------------------------


class TestLoadMarkets:
    @pytest.mark.asyncio
    async def test_load_markets_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """load_markets() should call exchange.load_markets() with retry wrapper."""
        await exchange_client.load_markets()
        
        mock_ccxt_exchange.load_markets.assert_called_once()

    @pytest.mark.asyncio
    async def test_load_markets_retries_on_network_error(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """load_markets() should retry on NetworkError with exponential backoff."""
        mock_ccxt_exchange.load_markets.side_effect = [
            ccxt.NetworkError("Connection failed"),
            ccxt.NetworkError("Connection failed"),
            {},  # Success on third attempt
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await exchange_client.load_markets()
            
            assert mock_ccxt_exchange.load_markets.call_count == 3
            assert mock_sleep.call_count == 2
            # Verify backoff delays: [1, 2] (not 5 because third succeeds)
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)

    @pytest.mark.asyncio
    async def test_load_markets_exhausts_retries(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """load_markets() should raise after exhausting all retry attempts."""
        mock_ccxt_exchange.load_markets.side_effect = ccxt.NetworkError("Persistent failure")
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ccxt.NetworkError):
                await exchange_client.load_markets()
            
            # Should attempt: initial + 3 retries = 4 total
            assert mock_ccxt_exchange.load_markets.call_count == 4

    @pytest.mark.asyncio
    async def test_load_markets_fails_immediately_on_exchange_error(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """load_markets() should not retry on non-retryable ExchangeError."""
        mock_ccxt_exchange.load_markets.side_effect = ccxt.ExchangeError("Invalid API key")
        
        with pytest.raises(ccxt.ExchangeError):
            await exchange_client.load_markets()
        
        # Should fail immediately without retries
        assert mock_ccxt_exchange.load_markets.call_count == 1


# ---------------------------------------------------------------------------
# place_limit_order Tests
# ---------------------------------------------------------------------------


class TestPlaceLimitOrder:
    @pytest.mark.asyncio
    async def test_place_limit_order_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """place_limit_order() should successfully place a buy order."""
        result = await exchange_client.place_limit_order("buy", 30000.0, 0.01)
        
        mock_ccxt_exchange.create_limit_order.assert_called_once_with(
            "BTC/USDT", "buy", 0.01, 30000.0, {}
        )
        assert result["id"] == "order-001"
        assert result["status"] == "open"

    @pytest.mark.asyncio
    async def test_place_limit_order_sell_side(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """place_limit_order() should support sell orders."""
        mock_ccxt_exchange.create_limit_order.return_value = {
            "id": "order-sell-001",
            "status": "open",
        }
        
        result = await exchange_client.place_limit_order("sell", 30500.0, 0.02)
        
        mock_ccxt_exchange.create_limit_order.assert_called_once_with(
            "BTC/USDT", "sell", 0.02, 30500.0, {}
        )
        assert result["id"] == "order-sell-001"

    @pytest.mark.asyncio
    async def test_place_limit_order_retries_with_backoff(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """place_limit_order() should retry with delays [1, 2, 5] on NetworkError."""
        mock_ccxt_exchange.create_limit_order.side_effect = [
            ccxt.RequestTimeout("Timeout"),
            ccxt.NetworkError("Connection lost"),
            {"id": "order-retry-001", "status": "open"},
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await exchange_client.place_limit_order("buy", 29000.0, 0.005)
            
            assert result["id"] == "order-retry-001"
            assert mock_ccxt_exchange.create_limit_order.call_count == 3
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)

    @pytest.mark.asyncio
    async def test_place_limit_order_all_retries_exhausted(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """place_limit_order() should raise after all retry attempts fail."""
        error = ccxt.NetworkError("Persistent network failure")
        mock_ccxt_exchange.create_limit_order.side_effect = error
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ccxt.NetworkError) as exc_info:
                await exchange_client.place_limit_order("buy", 30000.0, 0.01)
            
            assert mock_ccxt_exchange.create_limit_order.call_count == 4
            assert mock_sleep.call_count == 3
            # Verify all backoff delays were used
            mock_sleep.assert_any_call(1)
            mock_sleep.assert_any_call(2)
            mock_sleep.assert_any_call(5)

    @pytest.mark.asyncio
    async def test_place_limit_order_non_retryable_error(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """place_limit_order() should fail immediately on ExchangeError."""
        mock_ccxt_exchange.create_limit_order.side_effect = ccxt.InsufficientFunds(
            "Insufficient balance"
        )
        
        with pytest.raises(ccxt.InsufficientFunds):
            await exchange_client.place_limit_order("buy", 30000.0, 1.0)
        
        assert mock_ccxt_exchange.create_limit_order.call_count == 1


# ---------------------------------------------------------------------------
# cancel_order Tests
# ---------------------------------------------------------------------------


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """cancel_order() should successfully cancel an open order."""
        result = await exchange_client.cancel_order("order-001")
        
        mock_ccxt_exchange.cancel_order.assert_called_once_with("order-001", "BTC/USDT")
        assert result["id"] == "order-001"
        assert result["status"] == "canceled"

    @pytest.mark.asyncio
    async def test_cancel_order_retries_on_timeout(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """cancel_order() should retry on RequestTimeout."""
        mock_ccxt_exchange.cancel_order.side_effect = [
            ccxt.RequestTimeout("Timeout"),
            {"id": "order-002", "status": "canceled"},
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await exchange_client.cancel_order("order-002")
            
            assert result["status"] == "canceled"
            assert mock_ccxt_exchange.cancel_order.call_count == 2
            mock_sleep.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_cancel_order_order_not_found(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """cancel_order() should propagate OrderNotFound without retry."""
        mock_ccxt_exchange.cancel_order.side_effect = ccxt.OrderNotFound("Order not found")
        
        with pytest.raises(ccxt.OrderNotFound):
            await exchange_client.cancel_order("nonexistent-order")
        
        assert mock_ccxt_exchange.cancel_order.call_count == 1


# ---------------------------------------------------------------------------
# fetch_open_orders Tests
# ---------------------------------------------------------------------------


class TestFetchOpenOrders:
    @pytest.mark.asyncio
    async def test_fetch_open_orders_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_open_orders() should return list of open orders for the symbol."""
        orders = await exchange_client.fetch_open_orders()
        
        mock_ccxt_exchange.fetch_open_orders.assert_called_once_with("BTC/USDT")
        assert len(orders) == 2
        assert orders[0]["id"] == "order-001"
        assert orders[1]["id"] == "order-002"

    @pytest.mark.asyncio
    async def test_fetch_open_orders_empty_list(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_open_orders() should return empty list when no orders exist."""
        mock_ccxt_exchange.fetch_open_orders.return_value = []
        
        orders = await exchange_client.fetch_open_orders()
        
        assert orders == []

    @pytest.mark.asyncio
    async def test_fetch_open_orders_retries_on_network_error(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_open_orders() should retry on NetworkError."""
        mock_ccxt_exchange.fetch_open_orders.side_effect = [
            ccxt.NetworkError("Connection failed"),
            [{"id": "order-003", "status": "open"}],
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            orders = await exchange_client.fetch_open_orders()
            
            assert len(orders) == 1
            assert mock_ccxt_exchange.fetch_open_orders.call_count == 2


# ---------------------------------------------------------------------------
# fetch_ohlcv Tests
# ---------------------------------------------------------------------------


class TestFetchOHLCV:
    @pytest.mark.asyncio
    async def test_fetch_ohlcv_default_params(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_ohlcv() should use default timeframe '1h' and limit 200."""
        candles = await exchange_client.fetch_ohlcv()
        
        mock_ccxt_exchange.fetch_ohlcv.assert_called_once_with(
            "BTC/USDT", "1h", limit=200
        )
        assert len(candles) == 2
        assert candles[0][0] == 1640000000000

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_custom_timeframe(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_ohlcv() should accept custom timeframe parameter."""
        await exchange_client.fetch_ohlcv(timeframe="4h", limit=100)
        
        mock_ccxt_exchange.fetch_ohlcv.assert_called_once_with(
            "BTC/USDT", "4h", limit=100
        )

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_various_timeframes(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_ohlcv() should work with various timeframe strings."""
        timeframes = ["15m", "1h", "4h", "1d", "1w"]
        
        for tf in timeframes:
            mock_ccxt_exchange.fetch_ohlcv.reset_mock()
            await exchange_client.fetch_ohlcv(timeframe=tf)
            
            mock_ccxt_exchange.fetch_ohlcv.assert_called_once()
            args = mock_ccxt_exchange.fetch_ohlcv.call_args[0]
            assert args[1] == tf

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_retries_on_timeout(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_ohlcv() should retry on RequestTimeout."""
        mock_ccxt_exchange.fetch_ohlcv.side_effect = [
            ccxt.RequestTimeout("Timeout"),
            [
                [1640000000000, 30000, 30100, 29900, 30050, 150],
            ],
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            candles = await exchange_client.fetch_ohlcv()
            
            assert len(candles) == 1
            assert mock_ccxt_exchange.fetch_ohlcv.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_returns_ohlcv_format(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_ohlcv() should return list of [timestamp, o, h, l, c, v]."""
        candles = await exchange_client.fetch_ohlcv()
        
        assert isinstance(candles, list)
        first_candle = candles[0]
        assert len(first_candle) == 6
        assert isinstance(first_candle[0], int)  # timestamp
        assert isinstance(first_candle[1], (int, float))  # open
        assert isinstance(first_candle[4], (int, float))  # close
        assert isinstance(first_candle[5], (int, float))  # volume


# ---------------------------------------------------------------------------
# Additional Methods Tests
# ---------------------------------------------------------------------------


class TestGetTicker:
    @pytest.mark.asyncio
    async def test_get_ticker_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """get_ticker() should fetch current ticker for the symbol."""
        ticker = await exchange_client.get_ticker()
        
        mock_ccxt_exchange.fetch_ticker.assert_called_once_with("BTC/USDT")
        assert ticker["last"] == 30000.0
        assert "bid" in ticker
        assert "ask" in ticker

    @pytest.mark.asyncio
    async def test_get_ticker_retries_on_network_error(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """get_ticker() should retry on NetworkError."""
        mock_ccxt_exchange.fetch_ticker.side_effect = [
            ccxt.NetworkError("Connection lost"),
            {"last": 30100.0, "bid": 30095.0, "ask": 30105.0},
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            ticker = await exchange_client.get_ticker()
            
            assert ticker["last"] == 30100.0
            assert mock_ccxt_exchange.fetch_ticker.call_count == 2


class TestGetOrderStatus:
    @pytest.mark.asyncio
    async def test_get_order_status_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """get_order_status() should fetch current order status."""
        order = await exchange_client.get_order_status("order-001")
        
        mock_ccxt_exchange.fetch_order.assert_called_once_with("order-001", "BTC/USDT")
        assert order["id"] == "order-001"
        assert order["status"] == "open"


class TestFetchBalance:
    @pytest.mark.asyncio
    async def test_fetch_balance_success(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """fetch_balance() should return account balances."""
        balance = await exchange_client.fetch_balance()
        
        mock_ccxt_exchange.fetch_balance.assert_called_once()
        assert "BTC" in balance
        assert balance["BTC"]["total"] == 1.5
        assert "USDT" in balance
        assert balance["USDT"]["free"] == 10000.0


class TestClose:
    @pytest.mark.asyncio
    async def test_close_closes_exchange_session(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """close() should gracefully close the ccxt HTTP session."""
        await exchange_client.close()
        
        mock_ccxt_exchange.close.assert_called_once()


# ---------------------------------------------------------------------------
# Retry Logic Integration Tests
# ---------------------------------------------------------------------------


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_uses_exact_backoff_delays(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """_retry() should use delays [1, 2, 5] in sequence."""
        mock_ccxt_exchange.fetch_ticker.side_effect = [
            ccxt.NetworkError("Fail 1"),
            ccxt.NetworkError("Fail 2"),
            ccxt.NetworkError("Fail 3"),
            {"last": 30000.0},
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await exchange_client.get_ticker()
            
            assert mock_sleep.call_count == 3
            # Verify exact delay sequence
            calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert calls == [1, 2, 5]

    @pytest.mark.asyncio
    async def test_retry_handles_request_timeout(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """_retry() should treat RequestTimeout as retryable."""
        mock_ccxt_exchange.fetch_balance.side_effect = [
            ccxt.RequestTimeout("Timeout"),
            {"USDT": {"free": 5000.0}},
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            balance = await exchange_client.fetch_balance()
            
            assert balance["USDT"]["free"] == 5000.0
            assert mock_ccxt_exchange.fetch_balance.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_does_not_retry_invalid_order(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """_retry() should not retry InvalidOrder errors."""
        mock_ccxt_exchange.create_limit_order.side_effect = ccxt.InvalidOrder("Invalid price")
        
        with pytest.raises(ccxt.InvalidOrder):
            await exchange_client.place_limit_order("buy", -100.0, 0.01)
        
        assert mock_ccxt_exchange.create_limit_order.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_preserves_function_arguments(
        self, exchange_client: ExchangeClient, mock_ccxt_exchange: MagicMock
    ) -> None:
        """_retry() should pass all args/kwargs correctly on each attempt."""
        mock_ccxt_exchange.fetch_ohlcv.side_effect = [
            ccxt.NetworkError("Fail"),
            [[1640000000000, 30000, 30100, 29900, 30050, 100]],
        ]
        
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await exchange_client.fetch_ohlcv(timeframe="15m", limit=50)
            
            # Verify both calls used the same arguments
            assert mock_ccxt_exchange.fetch_ohlcv.call_count == 2
            for call in mock_ccxt_exchange.fetch_ohlcv.call_args_list:
                assert call[0] == ("BTC/USDT", "15m")
                assert call[1] == {"limit": 50}


# ---------------------------------------------------------------------------
# RETRY_DELAYS Constant Test
# ---------------------------------------------------------------------------


class TestRetryConstants:
    def test_retry_delays_defined(self) -> None:
        """RETRY_DELAYS should be [1, 2, 5] for exponential backoff."""
        assert RETRY_DELAYS == [1, 2, 5]
