"""
Tests for Execution Layer (Exchange Interfaces and Order Manager)

Tests cover:
- ExchangeInterface abstract contract
- LiveExchange CCXT integration
- SimulatedExchange order simulation
- OrderManager order lifecycle
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import ccxt.async_support as ccxt_async
import pytest

from bot_v2.execution.exchange_interface import ExchangeInterface
from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.execution.order_manager import OrderManager
from bot_v2.execution.order_state_manager import OrderRecord, OrderStateManager
from bot_v2.execution.simulated_exchange import SimulatedExchange
from bot_v2.models.enums import TradeSide
from bot_v2.models.exceptions import OrderExecutionError

# ==============================================================================
# Test ExchangeInterface (Abstract Base Class)
# ==============================================================================


class TestExchangeInterface:
    """Test ExchangeInterface abstract contract enforcement."""

    def test_cannot_instantiate_abstract_interface(self):
        """ExchangeInterface cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            ExchangeInterface()

    def test_subclass_must_implement_all_methods(self):
        """Subclass must implement all abstract methods."""

        class IncompleteExchange(ExchangeInterface):
            # Missing all method implementations
            pass

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteExchange()

    def test_complete_subclass_can_be_instantiated(self):
        """Complete subclass with all methods can be instantiated."""

        class CompleteExchange(ExchangeInterface):
            async def setup(self) -> bool:
                return True

            async def close(self) -> None:
                pass

            def format_market_id(self, symbol: str):
                return symbol

            async def get_market_price(self, market_id: str):
                return Decimal("50000")

            async def create_market_order(
                self, market_id: str, side, amount, params=None
            ):
                return {"id": "test"}

            async def fetch_ohlcv(self, market_id: str, timeframe: str, limit: int):
                return None

        exchange = CompleteExchange()
        assert isinstance(exchange, ExchangeInterface)


# ==============================================================================
# Test LiveExchange (CCXT Integration)
# ==============================================================================


class TestLiveExchange:
    """Test LiveExchange CCXT-based live trading."""

    @pytest.fixture
    def mock_ccxt_exchange(self):
        """Create mock CCXT exchange."""
        mock_exchange = MagicMock()
        mock_exchange.id = "binance"
        mock_exchange.load_markets = AsyncMock(return_value={})
        mock_exchange.close = AsyncMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value={"last": 50000.0})
        mock_exchange.create_market_order = AsyncMock(
            return_value={"id": "test-order-123", "status": "closed", "filled": 0.1}
        )
        mock_exchange.fetch_ohlcv = AsyncMock(
            return_value=[
                [1234567890000, 50000, 51000, 49000, 50500, 100],
                [1234567950000, 50500, 51500, 50000, 51000, 150],
            ]
        )
        mock_exchange.market = Mock(return_value={"id": "BTCUSDT"})
        return mock_exchange

    @pytest.fixture
    def live_exchange(self, mock_ccxt_exchange):
        """Create LiveExchange with mocked CCXT."""
        with patch("bot_v2.execution.live_exchange.ccxt_async") as mock_ccxt:
            mock_ccxt.binance = Mock(return_value=mock_ccxt_exchange)
            exchange = LiveExchange("binance", "test_key", "test_secret")
            exchange.exchange = mock_ccxt_exchange
            return exchange

    @pytest.mark.asyncio
    async def test_setup_success(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange setup loads markets successfully."""
        result = await live_exchange.setup()
        assert result is True
        # Expect 2 calls: one for authenticated exchange, one for public exchange
        assert mock_ccxt_exchange.load_markets.call_count == 2

    @pytest.mark.asyncio
    async def test_setup_failure(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange setup handles errors gracefully."""
        mock_ccxt_exchange.load_markets.side_effect = Exception("Connection failed")
        result = await live_exchange.setup()
        # Should return True if at least one succeeds, or False if both fail?
        # In implementation:
        # 1. Auth fails -> catch, success=False
        # 2. Public fails -> catch, return success (False)
        assert result is False

    @pytest.mark.asyncio
    async def test_close(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange closes connection properly."""
        await live_exchange.close()
        # Expect 2 calls: one for authenticated exchange, one for public exchange
        assert mock_ccxt_exchange.close.call_count == 2

    def test_format_market_id_success(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange formats market ID correctly."""
        market_id = live_exchange.format_market_id("BTC/USDT")
        assert market_id == "BTCUSDT"
        mock_ccxt_exchange.market.assert_called_with("BTC/USDT")

    def test_format_market_id_invalid_symbol(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange returns None for invalid symbol."""
        mock_ccxt_exchange.market.side_effect = ccxt_async.BadSymbol("Invalid")
        market_id = live_exchange.format_market_id("INVALID/PAIR")
        assert market_id is None

    @pytest.mark.asyncio
    async def test_get_market_price_success(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange fetches market price successfully."""
        price = await live_exchange.get_market_price("BTCUSDT")
        assert price == Decimal("50000")
        mock_ccxt_exchange.fetch_ticker.assert_called_with("BTCUSDT")

    @pytest.mark.asyncio
    async def test_get_market_price_failure(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange handles price fetch errors."""
        mock_ccxt_exchange.fetch_ticker.side_effect = Exception("Network error")
        price = await live_exchange.get_market_price("BTCUSDT")
        assert price is None

    @pytest.mark.asyncio
    async def test_create_market_order_success(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange creates market order successfully."""
        order = await live_exchange.create_market_order(
            "BTCUSDT", TradeSide.BUY, Decimal("0.1"), {}
        )
        assert order["id"] == "test-order-123"
        assert order["status"] == "closed"
        mock_ccxt_exchange.create_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_market_order_failure(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange handles order creation errors."""
        mock_ccxt_exchange.create_market_order.side_effect = Exception(
            "Insufficient balance"
        )

        with pytest.raises(OrderExecutionError, match="Order for BTCUSDT failed"):
            await live_exchange.create_market_order(
                "BTCUSDT", TradeSide.BUY, Decimal("0.1"), {}
            )

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_success(self, live_exchange, mock_ccxt_exchange):
        """LiveExchange fetches OHLCV data successfully."""
        df = await live_exchange.fetch_ohlcv("BTCUSDT", "1m", 100)
        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        mock_ccxt_exchange.fetch_ohlcv.assert_called_with("BTCUSDT", "1m", limit=100)


# ==============================================================================
# Test SimulatedExchange (Testing Mode)
# ==============================================================================


class TestSimulatedExchange:
    """Test SimulatedExchange order simulation."""

    @pytest.fixture
    def mock_public_exchange(self):
        """Create mock public Binance exchange."""
        mock_exchange = MagicMock()
        mock_exchange.fetch_ticker = AsyncMock(return_value={"last": 50000.0})
        mock_exchange.fetch_ohlcv = AsyncMock(
            return_value=[[1234567890000, 50000, 51000, 49000, 50500, 100]]
        )
        mock_exchange.close = AsyncMock()
        return mock_exchange

    @pytest.fixture
    def simulated_exchange(self, mock_public_exchange):
        """Create SimulatedExchange with mocked public API."""
        with patch("bot_v2.execution.simulated_exchange.ccxt_async") as mock_ccxt:
            mock_ccxt.binance = Mock(return_value=mock_public_exchange)
            exchange = SimulatedExchange(fee=Decimal("0.0004"))
            exchange.public_exchange = mock_public_exchange
            return exchange

    @pytest.mark.asyncio
    async def test_setup_always_succeeds(self, simulated_exchange):
        """SimulatedExchange setup always succeeds."""
        result = await simulated_exchange.setup()
        assert result is True

    @pytest.mark.asyncio
    async def test_close(self, simulated_exchange, mock_public_exchange):
        """SimulatedExchange closes public exchange connection."""
        await simulated_exchange.close()
        mock_public_exchange.close.assert_called_once()

    def test_format_market_id_returns_symbol(self, simulated_exchange):
        """SimulatedExchange returns symbol as-is."""
        market_id = simulated_exchange.format_market_id("BTC/USDT")
        assert market_id == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_get_market_price_success(
        self, simulated_exchange, mock_public_exchange
    ):
        """SimulatedExchange fetches price from public API."""
        price = await simulated_exchange.get_market_price("BTC/USDT")
        assert price == Decimal("50000")
        mock_public_exchange.fetch_ticker.assert_called_with("BTC/USDT")

    @pytest.mark.asyncio
    async def test_get_market_price_failure(
        self, simulated_exchange, mock_public_exchange
    ):
        """SimulatedExchange handles price fetch errors."""
        mock_public_exchange.fetch_ticker.side_effect = Exception("Network error")
        price = await simulated_exchange.get_market_price("BTC/USDT")
        assert price is None

    @pytest.mark.asyncio
    async def test_create_simulated_order_success(
        self, simulated_exchange, mock_public_exchange
    ):
        """SimulatedExchange creates simulated order with correct structure."""
        order = await simulated_exchange.create_market_order(
            "BTC/USDT", TradeSide.BUY, Decimal("0.1"), {}
        )

        # Verify order structure
        assert order["info"]["simulated"] is True
        assert order["symbol"] == "BTC/USDT"
        assert order["type"] == "market"
        assert order["side"] == "buy"
        assert order["amount"] == "0.1"
        assert order["filled"] == "0.1"
        assert order["remaining"] == "0"
        assert "id" in order
        assert "timestamp" in order

    @pytest.mark.asyncio
    async def test_create_simulated_order_calculates_fee(self, simulated_exchange):
        """SimulatedExchange calculates trading fee correctly."""
        order = await simulated_exchange.create_market_order(
            "BTC/USDT", TradeSide.BUY, Decimal("0.1"), {}
        )

        # Fee = amount * price * fee_rate
        # = 0.1 * 50000 * 0.0004 = 2.0
        expected_cost = Decimal("0.1") * Decimal("50000")  # 5000
        expected_fee = expected_cost * Decimal("0.0004")  # 2.0

        assert Decimal(order["cost"]) == expected_cost
        assert Decimal(order["fee"]["cost"]) == expected_fee
        assert order["fee"]["currency"] == "USDT"

    @pytest.mark.asyncio
    async def test_create_simulated_order_no_price(
        self, simulated_exchange, mock_public_exchange
    ):
        """SimulatedExchange raises error if price fetch fails."""
        mock_public_exchange.fetch_ticker.side_effect = Exception("Network error")

        with pytest.raises(OrderExecutionError, match="Failed to fetch price"):
            await simulated_exchange.create_market_order(
                "BTC/USDT", TradeSide.SELL, Decimal("0.1"), {}
            )

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_success(self, simulated_exchange, mock_public_exchange):
        """SimulatedExchange fetches OHLCV from public API."""
        df = await simulated_exchange.fetch_ohlcv("BTC/USDT", "1m", 100)
        assert df is not None
        assert len(df) == 1
        mock_public_exchange.fetch_ohlcv.assert_called_with("BTC/USDT", "1m", limit=100)


# ==============================================================================
# Test OrderManager (Order Lifecycle)
# ==============================================================================


class TestOrderManager:
    """Test OrderManager order lifecycle management."""

    @pytest.fixture
    def mock_exchange(self):
        """Create mock exchange."""
        exchange = Mock(spec=ExchangeInterface)
        exchange.format_market_id = Mock(return_value="BTCUSDT")
        exchange.get_market_price = AsyncMock(return_value=Decimal("50000"))
        exchange.create_market_order = AsyncMock(
            return_value={
                "id": "order-123",
                "symbol": "BTC/USDT",
                "side": "buy",
                "amount": 0.1,
                "type": "market",
                "status": "closed",
                "filled": 0.1,
                "average": 50000,
            }
        )
        return exchange

    @pytest.fixture
    def order_manager(self, mock_exchange, tmp_path):
        """Create OrderManager with mock exchange."""
        return OrderManager(
            mock_exchange,
            order_state_manager=OrderStateManager(tmp_path),
        )

    @pytest.mark.asyncio
    async def test_create_market_order_success(self, order_manager, mock_exchange):
        """OrderManager creates order successfully."""
        order = await order_manager.create_market_order(
            "BTC/USDT", TradeSide.BUY, Decimal("0.1")
        )

        assert order["id"] == "order-123"
        assert order["status"] == "closed"
        mock_exchange.create_market_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_market_order_tracks_pending(
        self, order_manager, mock_exchange
    ):
        """OrderManager persists filled market orders without exposing them as pending."""
        order = await order_manager.create_market_order(
            "BTC/USDT", TradeSide.BUY, Decimal("0.1")
        )

        assert order["id"] == "order-123"
        pending = order_manager.get_pending_orders()
        assert "order-123" not in pending

        persisted = order_manager.order_state_manager.get_order_by_exchange_id("order-123")
        assert persisted is not None
        assert persisted.symbol == "BTC/USDT"
        assert persisted.side == "BUY"
        assert Decimal(persisted.quantity) == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_create_market_order_invalid_amount(self, order_manager):
        """OrderManager rejects invalid order amounts."""
        with pytest.raises(OrderExecutionError, match="Invalid order amount"):
            await order_manager.create_market_order(
                "BTC/USDT", TradeSide.BUY, Decimal("0")
            )

        with pytest.raises(OrderExecutionError, match="Invalid order amount"):
            await order_manager.create_market_order(
                "BTC/USDT", TradeSide.BUY, Decimal("-0.1")
            )

    @pytest.mark.asyncio
    async def test_create_market_order_invalid_symbol(
        self, order_manager, mock_exchange
    ):
        """OrderManager rejects invalid symbols."""
        mock_exchange.format_market_id.return_value = None

        with pytest.raises(OrderExecutionError, match="Invalid symbol"):
            await order_manager.create_market_order(
                "INVALID/PAIR", TradeSide.BUY, Decimal("0.1")
            )

    @pytest.mark.asyncio
    async def test_create_market_order_exchange_error(
        self, order_manager, mock_exchange
    ):
        """OrderManager handles exchange errors."""
        mock_exchange.create_market_order.side_effect = Exception(
            "Insufficient balance"
        )

        with pytest.raises(OrderExecutionError, match="Failed to create market order"):
            await order_manager.create_market_order(
                "BTC/USDT", TradeSide.BUY, Decimal("0.1")
            )

    @pytest.mark.asyncio
    async def test_get_current_price_success(self, order_manager, mock_exchange):
        """OrderManager fetches current price."""
        price = await order_manager.get_current_price("BTC/USDT")
        assert price == Decimal("50000")
        mock_exchange.get_market_price.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_current_price_invalid_symbol(self, order_manager, mock_exchange):
        """OrderManager handles invalid symbol for price fetch."""
        mock_exchange.format_market_id.return_value = None
        price = await order_manager.get_current_price("INVALID/PAIR")
        assert price is None

    @pytest.mark.asyncio
    async def test_clear_order_tracking(self, order_manager, mock_exchange):
        """OrderManager clears order tracking."""
        await order_manager.order_state_manager.add_order(
            OrderRecord(
                local_id="local-order-123",
                exchange_order_id="order-123",
                symbol="BTC/USDT",
                side="BUY",
                quantity="0.1",
                avg_price="50000",
                status="NEW",
                mode="local_sim",
            )
        )

        assert "order-123" in order_manager.get_pending_orders()
        await order_manager.clear_order_tracking("order-123")
        assert "order-123" not in order_manager.get_pending_orders()

    @pytest.mark.asyncio
    async def test_get_pending_orders_returns_copy(self, order_manager, mock_exchange):
        """OrderManager returns copy of pending orders."""
        await order_manager.create_market_order(
            "BTC/USDT", TradeSide.BUY, Decimal("0.1")
        )

        pending1 = order_manager.get_pending_orders()
        pending2 = order_manager.get_pending_orders()

        # Verify they're copies (different objects)
        assert pending1 is not pending2
        # But have same content
        assert pending1 == pending2
