import asyncio
from decimal import Decimal

import pytest

from bot_v2.execution.simulated_exchange import SimulatedExchange
from bot_v2.models.enums import TradeSide


@pytest.mark.asyncio
async def test_slippage_simulation_buy():
    sim = SimulatedExchange(fee=Decimal("0.0002"), slippage_pct=1.0)
    price = Decimal("100")
    # Patch get_market_price to return fixed price
    sim.get_market_price = lambda market_id: asyncio.Future()
    sim.get_market_price("BTC/USDT").set_result(price)

    # Actually use awaitable
    async def fake_get_market_price(market_id):
        return price

    sim.get_market_price = fake_get_market_price
    order = await sim.create_market_order("BTC/USDT", TradeSide.BUY, Decimal("1"))
    assert Decimal(order["price"]) == price + price * Decimal("0.01")
    assert order["info"]["slippage_pct"] == 1.0


@pytest.mark.asyncio
async def test_slippage_simulation_sell():
    sim = SimulatedExchange(fee=Decimal("0.0002"), slippage_pct=2.0)
    price = Decimal("200")

    async def fake_get_market_price(market_id):
        return price

    sim.get_market_price = fake_get_market_price
    order = await sim.create_market_order("ETH/USDT", TradeSide.SELL, Decimal("2"))
    assert Decimal(order["price"]) == price - price * Decimal("0.02")
    assert order["info"]["slippage_pct"] == 2.0
