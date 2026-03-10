"""
tests/test_order_manager.py
----------------------------
Unit tests for src/oms/order_manager.py.

All exchange calls are mocked — no live API connectivity required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.oms import OrderRecord, OrderStatus
from src.oms.order_manager import OrderManager
from config.settings import GridBotSettings
from src.strategy import GridLevel, GridType


def _make_levels(n: int = 4, centre: float = 30_000.0) -> list:
    """Create n synthetic GridLevel objects alternating buy/sell."""
    levels = []
    for i in range(n):
        offset = (i - n // 2 + 0.5) * 300.0
        price = centre + offset
        side = "buy" if price < centre else "sell"
        levels.append(
            GridLevel(
                price=price,
                side=side,
                level_index=i,
                order_size_quote=100.0,
            )
        )
    return levels


@pytest.fixture
def order_manager(mock_exchange: MagicMock, base_settings: GridBotSettings) -> OrderManager:
    return OrderManager(client=mock_exchange, settings=base_settings)


class TestDeployGrid:
    @pytest.mark.asyncio
    async def test_deploy_returns_count(
        self, order_manager: OrderManager, mock_exchange: MagicMock
    ) -> None:
        """deploy_grid() should return the number of successfully placed orders."""
        order_idx = [0]
        async def mock_place(*args, **kwargs):
            order_idx[0] += 1
            return {"id": f"order-{order_idx[0]}", "status": "open"}
        mock_exchange.place_limit_order.side_effect = mock_place
        levels = _make_levels(4)
        await order_manager.deploy_grid(levels)
        count = order_manager.open_order_count
        assert count == 4

    @pytest.mark.asyncio
    async def test_orders_registered_after_deploy(
        self, order_manager: OrderManager, mock_exchange: MagicMock
    ) -> None:
        """After deployment, OMS should track all placed orders as OPEN."""
        call_idx = [0]

        async def make_order(*_args, **_kwargs):
            call_idx[0] += 1
            return {"id": f"order-{call_idx[0]:03d}", "status": "open"}

        mock_exchange.place_limit_order.side_effect = make_order
        levels = _make_levels(4)
        await order_manager.deploy_grid(levels)
        open_orders = order_manager.all_records
        assert len(open_orders) == 4
        for rec in open_orders.values():
            assert rec.status == OrderStatus.OPEN

    @pytest.mark.asyncio
    async def test_duplicate_price_skipped(
        self, order_manager: OrderManager, mock_exchange: MagicMock
    ) -> None:
        """Deploying the same price level twice must not create a duplicate."""
        order_idx = [0]
        async def mock_place(*args, **kwargs):
            order_idx[0] += 1
            return {"id": f"dup-{order_idx[0]}", "status": "open"}
        mock_exchange.place_limit_order.side_effect = mock_place
        levels = _make_levels(2)
        await order_manager.deploy_grid(levels)
        await order_manager.deploy_grid(levels)
        count2 = order_manager.open_order_count - 2
        # Second deploy should skip all: already mapped.
        assert count2 == 0


class TestCancelAllOrders:
    @pytest.mark.asyncio
    async def test_cancel_clears_open_orders(
        self, order_manager: OrderManager, mock_exchange: MagicMock
    ) -> None:
        """cancel_all_orders() should cancel and update all OPEN orders."""
        call_idx = [0]

        async def make_order(*_args, **_kwargs):
            call_idx[0] += 1
            return {"id": f"cancel-{call_idx[0]:03d}", "status": "open"}

        mock_exchange.place_limit_order.side_effect = make_order
        mock_exchange.cancel_order.return_value = {"status": "canceled"}
        levels = _make_levels(3)
        await order_manager.deploy_grid(levels)
        await order_manager.cancel_all_orders()
        assert order_manager.open_order_count == 0


class TestStateExportImport:
    @pytest.mark.asyncio
    async def test_export_then_import_round_trip(
        self, order_manager: OrderManager, mock_exchange: MagicMock
    ) -> None:
        """State exported and re-imported must restore the same orders."""
        call_idx = [0]

        async def make_order(*_args, **_kwargs):
            call_idx[0] += 1
            return {"id": f"rt-{call_idx[0]:03d}", "status": "open"}

        mock_exchange.place_limit_order.side_effect = make_order
        levels = _make_levels(3)
        await order_manager.deploy_grid(levels)
        exported = order_manager.export_state()
        new_manager = OrderManager(client=mock_exchange, settings=order_manager.settings)
        new_manager.import_state(exported)
        assert len(new_manager.all_records) == 3
