"""
tests/test_fill_handler.py
--------------------------
Unit tests for src/oms/fill_handler.py.

All exchange and OrderManager calls are mocked — no live API connectivity.
Tests verify the fill-to-counter-order cycle logic.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from typing import Dict

from src.oms import OrderRecord, OrderStatus
from src.oms.fill_handler import FillHandler
from src.oms.order_manager import OrderManager
from src.exchange.exchange_client import ExchangeClient
from src.strategy.grid_calculator import GridCalculator
from src.strategy import GridLevel, GridType
from config.settings import GridBotSettings


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_order_manager() -> MagicMock:
    """Create a mock OrderManager with controllable state."""
    om = MagicMock(spec=OrderManager)
    om.deploy_grid = AsyncMock()
    om.cancel_all_orders = AsyncMock()
    om.cancel_order = AsyncMock()
    # Start with empty state
    om.all_records = {}
    type(om).open_order_count = PropertyMock(return_value=0)
    return om


@pytest.fixture
def mock_exchange_client() -> MagicMock:
    """Create a mock ExchangeClient."""
    client = MagicMock(spec=ExchangeClient)
    client.fetch_open_orders = AsyncMock(return_value=[])
    client.place_limit_order = AsyncMock(
        return_value={"id": "counter-001", "status": "open"}
    )
    client.cancel_order = AsyncMock(return_value={"status": "canceled"})
    return client


@pytest.fixture
def mock_calculator() -> GridCalculator:
    """Create a real GridCalculator instance for counter-price logic."""
    return GridCalculator(
        grid_type=GridType.GEOMETRIC,
        spacing_pct=0.01,  # 1% spacing
        spacing_abs=50.0,
        num_grids_up=10,
        num_grids_down=10,
        order_size_quote=100.0,
        price_step=0.01,
    )


@pytest.fixture
def test_settings(base_settings: GridBotSettings) -> GridBotSettings:
    """Return test settings with known MAX_OPEN_ORDERS."""
    base_settings.MAX_OPEN_ORDERS = 20
    base_settings.ORDER_SIZE_QUOTE = 100.0
    return base_settings


@pytest.fixture
def fill_handler(
    mock_order_manager: MagicMock,
    mock_exchange_client: MagicMock,
    mock_calculator: GridCalculator,
    test_settings: GridBotSettings,
) -> FillHandler:
    """Create a FillHandler with all dependencies mocked."""
    return FillHandler(
        order_manager=mock_order_manager,
        client=mock_exchange_client,
        calculator=mock_calculator,
        settings=test_settings,
    )


# ── Helper Functions ──────────────────────────────────────────────────────────


def _make_order_record(
    order_id: str,
    grid_price: float,
    side: str,
    status: OrderStatus = OrderStatus.OPEN,
) -> OrderRecord:
    """Create an OrderRecord for testing."""
    return OrderRecord(
        order_id=order_id,
        grid_price=grid_price,
        side=side,
        amount=100.0 / grid_price,  # $100 worth
        status=status,
    )


def _mock_open_orders_response(order_ids: list[str]) -> list[dict]:
    """Create a mock exchange open_orders response."""
    return [{"id": oid, "status": "open"} for oid in order_ids]


# ── Test Classes ──────────────────────────────────────────────────────────────


class TestFillDetection:
    """Test that the FillHandler correctly detects filled orders."""

    @pytest.mark.asyncio
    async def test_no_fills_when_all_orders_still_open(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """If all tracked orders are still open on exchange, detect no fills."""
        # Setup: OrderManager tracking 3 open orders
        mock_order_manager.all_records = {
            "order-001": _make_order_record("order-001", 30000.0, "buy"),
            "order-002": _make_order_record("order-002", 30300.0, "sell"),
            "order-003": _make_order_record("order-003", 29700.0, "buy"),
        }
        # Exchange still shows all 3 as open
        mock_exchange_client.fetch_open_orders.return_value = (
            _mock_open_orders_response(["order-001", "order-002", "order-003"])
        )

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 0
        # No counter-orders should be placed
        mock_order_manager.deploy_grid.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_buy_fill_detected(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """When a buy order disappears from exchange, mark it as FILLED."""
        # Setup: 2 orders tracked, 1 missing from exchange
        order_filled = _make_order_record("order-buy-1", 29700.0, "buy")
        order_open = _make_order_record("order-sell-1", 30300.0, "sell")
        mock_order_manager.all_records = {
            "order-buy-1": order_filled,
            "order-sell-1": order_open,
        }
        # Exchange only shows the sell order
        mock_exchange_client.fetch_open_orders.return_value = (
            _mock_open_orders_response(["order-sell-1"])
        )

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 1
        assert filled[0].order_id == "order-buy-1"
        assert filled[0].status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_single_sell_fill_detected(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """When a sell order disappears from exchange, mark it as FILLED."""
        order_filled = _make_order_record("order-sell-2", 30600.0, "sell")
        order_open = _make_order_record("order-buy-2", 29400.0, "buy")
        mock_order_manager.all_records = {
            "order-sell-2": order_filled,
            "order-buy-2": order_open,
        }
        mock_exchange_client.fetch_open_orders.return_value = (
            _mock_open_orders_response(["order-buy-2"])
        )

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 1
        assert filled[0].order_id == "order-sell-2"
        assert filled[0].status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_multiple_fills_detected_in_single_poll(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Multiple fills in one polling cycle must all be detected."""
        # 4 orders tracked, 3 are filled
        mock_order_manager.all_records = {
            "order-001": _make_order_record("order-001", 29700.0, "buy"),
            "order-002": _make_order_record("order-002", 30300.0, "sell"),
            "order-003": _make_order_record("order-003", 29400.0, "buy"),
            "order-004": _make_order_record("order-004", 30600.0, "sell"),
        }
        # Only order-004 still open on exchange
        mock_exchange_client.fetch_open_orders.return_value = (
            _mock_open_orders_response(["order-004"])
        )

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 3
        filled_ids = {rec.order_id for rec in filled}
        assert filled_ids == {"order-001", "order-002", "order-003"}
        # All filled orders should have status FILLED
        for rec in filled:
            assert rec.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_only_open_orders_checked_for_fills(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Orders already marked FILLED or CANCELED should not be rechecked."""
        mock_order_manager.all_records = {
            "order-open": _make_order_record(
                "order-open", 30000.0, "buy", OrderStatus.OPEN
            ),
            "order-already-filled": _make_order_record(
                "order-already-filled", 30300.0, "sell", OrderStatus.FILLED
            ),
            "order-canceled": _make_order_record(
                "order-canceled", 29700.0, "buy", OrderStatus.CANCELED
            ),
        }
        # Exchange shows no orders open
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        # Only the OPEN order should be detected as filled
        assert len(filled) == 1
        assert filled[0].order_id == "order-open"


class TestCounterOrderPlacement:
    """Test the placement of counter-orders after fills."""

    @pytest.mark.asyncio
    async def test_buy_fill_triggers_sell_counter_order_above(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        mock_calculator: GridCalculator,
    ) -> None:
        """Buy fill at price P should place sell counter-order one level UP."""
        buy_price = 30000.0
        mock_order_manager.all_records = {
            "buy-order": _make_order_record("buy-order", buy_price, "buy"),
        }
        mock_order_manager.open_order_count = 5
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # Verify deploy_grid was called
        mock_order_manager.deploy_grid.assert_called_once()
        
        # Extract the GridLevel passed to deploy_grid
        call_args = mock_order_manager.deploy_grid.call_args
        levels: list[GridLevel] = call_args[0][0]
        
        assert len(levels) == 1
        level = levels[0]
        assert level.side == "sell"
        # Counter-price should be 1% above buy_price (geometric spacing)
        expected_counter_price = mock_calculator._price(buy_price, 1, "up")
        assert level.price == pytest.approx(expected_counter_price, rel=1e-5)

    @pytest.mark.asyncio
    async def test_sell_fill_triggers_buy_counter_order_below(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        mock_calculator: GridCalculator,
    ) -> None:
        """Sell fill at price P should place buy counter-order one level DOWN."""
        sell_price = 30000.0
        mock_order_manager.all_records = {
            "sell-order": _make_order_record("sell-order", sell_price, "sell"),
        }
        mock_order_manager.open_order_count = 5
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        mock_order_manager.deploy_grid.assert_called_once()
        call_args = mock_order_manager.deploy_grid.call_args
        levels: list[GridLevel] = call_args[0][0]
        
        assert len(levels) == 1
        level = levels[0]
        assert level.side == "buy"
        # Counter-price should be 1% below sell_price
        expected_counter_price = mock_calculator._price(sell_price, 1, "down")
        assert level.price == pytest.approx(expected_counter_price, rel=1e-5)

    @pytest.mark.asyncio
    async def test_multiple_fills_trigger_multiple_counter_orders(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Each fill should trigger exactly one counter-order placement."""
        mock_order_manager.all_records = {
            "buy-1": _make_order_record("buy-1", 29700.0, "buy"),
            "sell-1": _make_order_record("sell-1", 30300.0, "sell"),
            "buy-2": _make_order_record("buy-2", 29400.0, "buy"),
        }
        mock_order_manager.open_order_count = 10
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # deploy_grid should be called 3 times (once per fill)
        assert mock_order_manager.deploy_grid.call_count == 3

    @pytest.mark.asyncio
    async def test_counter_order_uses_correct_order_size(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """Counter-orders must use ORDER_SIZE_QUOTE from settings."""
        mock_order_manager.all_records = {
            "buy-order": _make_order_record("buy-order", 30000.0, "buy"),
        }
        mock_order_manager.open_order_count = 5
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        call_args = mock_order_manager.deploy_grid.call_args
        levels: list[GridLevel] = call_args[0][0]
        assert levels[0].order_size_quote == test_settings.ORDER_SIZE_QUOTE


class TestMaxOpenOrdersConstraint:
    """Test that MAX_OPEN_ORDERS limit is enforced."""

    @pytest.mark.asyncio
    async def test_counter_order_placed_when_below_max(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """Counter-orders should be placed if open_order_count < MAX_OPEN_ORDERS."""
        test_settings.MAX_OPEN_ORDERS = 20
        mock_order_manager.all_records = {
            "buy-order": _make_order_record("buy-order", 30000.0, "buy"),
        }
        # Below the limit
        type(mock_order_manager).open_order_count = PropertyMock(return_value=15)
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # Counter-order should be placed
        mock_order_manager.deploy_grid.assert_called_once()

    @pytest.mark.asyncio
    async def test_counter_order_skipped_when_at_max(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """No counter-orders when open_order_count >= MAX_OPEN_ORDERS."""
        test_settings.MAX_OPEN_ORDERS = 20
        mock_order_manager.all_records = {
            "buy-order": _make_order_record("buy-order", 30000.0, "buy"),
        }
        # At the limit
        type(mock_order_manager).open_order_count = PropertyMock(return_value=20)
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # Counter-order should NOT be placed
        mock_order_manager.deploy_grid.assert_not_called()

    @pytest.mark.asyncio
    async def test_counter_order_skipped_when_exceeding_max(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """No counter-orders when open_order_count > MAX_OPEN_ORDERS."""
        test_settings.MAX_OPEN_ORDERS = 20
        mock_order_manager.all_records = {
            "sell-order": _make_order_record("sell-order", 30600.0, "sell"),
        }
        # Above the limit (defensive edge case)
        type(mock_order_manager).open_order_count = PropertyMock(return_value=25)
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        mock_order_manager.deploy_grid.assert_not_called()

    @pytest.mark.asyncio
    async def test_some_counter_orders_placed_before_hitting_max(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        test_settings: GridBotSettings,
    ) -> None:
        """With multiple fills, counter-orders stop being placed at MAX_OPEN_ORDERS."""
        test_settings.MAX_OPEN_ORDERS = 20
        mock_order_manager.all_records = {
            "buy-1": _make_order_record("buy-1", 29700.0, "buy"),
            "buy-2": _make_order_record("buy-2", 29400.0, "buy"),
            "sell-1": _make_order_record("sell-1", 30300.0, "sell"),
        }
        
        # Track deploy_grid calls
        call_count = [0]
        def get_open_count():
            # First fill: 18 (below limit, place counter)
            # Second fill: 19 (below limit, place counter)
            # Third fill: 20 (at limit, skip counter)
            return 18 + call_count[0]

        type(mock_order_manager).open_order_count = PropertyMock(side_effect=get_open_count)

        # Track deploy_grid calls
        async def track_deploy(*args, **kwargs):
            call_count[0] += 1
            return None

        mock_order_manager.deploy_grid.side_effect = track_deploy
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # Only first 2 fills should place counter-orders (before hitting limit)
        assert mock_order_manager.deploy_grid.call_count == 2


class TestOrderStatusTransitions:
    """Test that OrderStatus transitions are correct."""

    @pytest.mark.asyncio
    async def test_filled_order_status_updated_to_filled(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """When an order fills, its status must change to FILLED."""
        order_record = _make_order_record("order-001", 30000.0, "buy", OrderStatus.OPEN)
        mock_order_manager.all_records = {"order-001": order_record}
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert filled[0].status == OrderStatus.FILLED
        # Original record should also be updated (in-place mutation)
        assert order_record.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_canceled_orders_not_marked_as_filled(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Orders with status CANCELED should not be marked FILLED."""
        mock_order_manager.all_records = {
            "canceled": _make_order_record(
                "canceled", 30000.0, "buy", OrderStatus.CANCELED
            ),
        }
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        # Canceled order should not appear in filled list
        assert len(filled) == 0

    @pytest.mark.asyncio
    async def test_partially_filled_orders_not_detected_as_filled(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Orders with PARTIALLY_FILLED status should not be marked as filled yet."""
        mock_order_manager.all_records = {
            "partial": _make_order_record(
                "partial", 30000.0, "buy", OrderStatus.PARTIALLY_FILLED
            ),
        }
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 0


class TestEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_empty_order_manager_returns_no_fills(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """When OrderManager has no tracked orders, no fills detected."""
        mock_order_manager.all_records = {}
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        assert len(filled) == 0
        mock_order_manager.deploy_grid.assert_not_called()

    @pytest.mark.asyncio
    async def test_exchange_returns_extra_orders_not_tracked(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """If exchange shows orders not in our tracking, ignore them."""
        mock_order_manager.all_records = {
            "tracked": _make_order_record("tracked", 30000.0, "buy"),
        }
        # Exchange shows more orders than we're tracking
        mock_exchange_client.fetch_open_orders.return_value = (
            _mock_open_orders_response(["tracked", "unknown-1", "unknown-2"])
        )

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        # No fills detected since our tracked order is still open
        assert len(filled) == 0

    @pytest.mark.asyncio
    async def test_counter_order_placement_with_zero_price_step(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
        mock_calculator: GridCalculator,
    ) -> None:
        """Counter-order placement should work even with price_step=0."""
        # Set price_step to 0 (no quantization)
        mock_calculator.price_step = 0.0
        
        mock_order_manager.all_records = {
            "buy-order": _make_order_record("buy-order", 30000.0, "buy"),
        }
        mock_order_manager.open_order_count = 5
        mock_exchange_client.fetch_open_orders.return_value = []

        await fill_handler.poll_and_handle(centre_price=30000.0)

        # Should still place counter-order successfully
        mock_order_manager.deploy_grid.assert_called_once()

    @pytest.mark.asyncio
    async def test_fill_detection_with_large_grid(
        self,
        fill_handler: FillHandler,
        mock_order_manager: MagicMock,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Test fill detection with many orders (stress test)."""
        # Create 50 orders, all will be filled
        large_records = {
            f"order-{i:03d}": _make_order_record(
                f"order-{i:03d}",
                30000.0 + i * 100,
                "buy" if i % 2 == 0 else "sell",
            )
            for i in range(50)
        }
        mock_order_manager.all_records = large_records
        mock_order_manager.open_order_count = 5  # Below MAX to allow counter-orders
        mock_exchange_client.fetch_open_orders.return_value = []

        filled = await fill_handler.poll_and_handle(centre_price=30000.0)

        # All 50 orders should be detected as filled
        assert len(filled) == 50
        # All should trigger counter-order attempts
        assert mock_order_manager.deploy_grid.call_count == 50
