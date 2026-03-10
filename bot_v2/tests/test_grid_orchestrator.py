"""
Tests for GridOrchestrator - Unit & Integration
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from decimal import Decimal
from bot_v2.grid.orchestrator import GridOrchestrator
from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.models.enums import TradeSide

@pytest.fixture
def mock_deps():
    order_manager = AsyncMock()
    order_manager.create_limit_order = AsyncMock(return_value={"id": "grid-order-1"})
    exchange = AsyncMock()
    risk_manager = MagicMock()
    risk_manager.get_tier_info.return_value = {
        "tier": "PROBATION",
        "capital_allocation": 0.3
    }
    config = MagicMock(spec=StrategyConfig)
    config.grid_spacing_pct = Decimal("0.01")
    config.grid_num_grids_up = 5
    config.grid_num_grids_down = 5
    config.grid_order_size_quote = Decimal("100")
    config.grid_recentre_trigger = 3
    config.grid_stop_policy = "cancel_open_orders"
    order_manager.cancel_orders_for_symbol = AsyncMock(return_value=0)
    return order_manager, exchange, config, risk_manager

@pytest.mark.asyncio
async def test_orchestrator_deployment(mock_deps):
    order_manager, exchange, config, risk_manager = mock_deps
    orchestrator = GridOrchestrator("BTC/USDT", config, order_manager, exchange, risk_manager)
    
    # Mock exchange price
    exchange.get_market_price.return_value = Decimal("50000")
    
    await orchestrator.start()
    
    assert orchestrator.is_active is True
    assert orchestrator.centre_price == Decimal("50000")
    
    # Check if orders were placed (5 up + 5 down = 10 total)
    assert order_manager.create_limit_order.call_count == 10
    
    # Verify risk adjustment (100 * 0.3 = 30)
    # The first order price is centre / (1+spacing) = 50000 / 1.01 = 49504.95
    # Amount = 30 / 49504.95 = 0.000606
    # We check that the calculator's order_size_quote was updated
    assert orchestrator.calculator.order_size_quote == 30.0

@pytest.mark.asyncio
async def test_orchestrator_handle_fill(mock_deps):
    order_manager, exchange, config, risk_manager = mock_deps
    orchestrator = GridOrchestrator("BTC/USDT", config, order_manager, exchange, risk_manager)
    orchestrator.is_active = True
    
    # Simulate a Buy fill at 49000
    fill_price = Decimal("49000")
    amount = Decimal("0.002")
    
    await orchestrator.handle_fill("order_123", fill_price, amount, TradeSide.BUY)
    
    # Should place a SELL counter-order 1% higher (49490)
    expected_counter_price = fill_price * Decimal("1.01")
    
    order_manager.create_limit_order.assert_called_with(
        symbol_id="BTC/USDT",
        side=TradeSide.SELL,
        amount=amount,
        price=expected_counter_price,
        config=config
    )


@pytest.mark.asyncio
async def test_orchestrator_stop_cancels_orders_by_policy(mock_deps):
    order_manager, exchange, config, risk_manager = mock_deps
    orchestrator = GridOrchestrator("BTC/USDT", config, order_manager, exchange, risk_manager)
    orchestrator.is_active = True

    await orchestrator.stop(reason="test")

    order_manager.cancel_orders_for_symbol.assert_awaited_once_with("BTC/USDT")
    assert orchestrator.is_active is False


@pytest.mark.asyncio
async def test_orchestrator_stop_can_keep_orders_open(mock_deps):
    order_manager, exchange, config, risk_manager = mock_deps
    config.grid_stop_policy = "keep_open_orders"
    orchestrator = GridOrchestrator("BTC/USDT", config, order_manager, exchange, risk_manager)
    orchestrator.is_active = True

    await orchestrator.stop(reason="test")

    order_manager.cancel_orders_for_symbol.assert_not_called()
    assert orchestrator.is_active is False
