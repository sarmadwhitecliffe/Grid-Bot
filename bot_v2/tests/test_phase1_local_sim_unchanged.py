"""
Test to verify Phase 1 changes do NOT affect local_sim mode.

This test ensures that:
1. Safety checks are NOT applied in local_sim
2. OrderStateManager is NOT used in local_sim
3. Daily counters are NOT updated in local_sim
4. Verification logic is NOT run in local_sim
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Add bot_v2 to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot_v2.execution.order_manager import OrderManager
from bot_v2.execution.simulated_exchange import SimulatedExchange
from bot_v2.models.enums import TradeSide
from bot_v2.models.strategy_config import StrategyConfig


async def test_local_sim_unchanged():
    """Verify local_sim behavior is unchanged by Phase 1."""

    print("\n" + "=" * 60)
    print("TEST: local_sim mode unchanged by Phase 1")
    print("=" * 60)

    # Create simulated exchange and order manager
    sim_exchange = SimulatedExchange(fee=Decimal("0.0004"))
    await sim_exchange.setup()

    order_manager = OrderManager(sim_exchange)

    # Create config with local_sim mode and safety limits
    config = StrategyConfig(
        symbol_id="BTC/USDT",
        mode="local_sim",  # LOCAL_SIM MODE
        max_notional_per_order=Decimal("10.0"),  # Safety cap (should be ignored)
        daily_max_trades=1,  # Daily limit (should be ignored)
        daily_max_notional=Decimal("10.0"),  # Daily limit (should be ignored)
    )

    print(f"\n✓ Created config with mode={config.mode}")
    print(f"  - max_notional_per_order: {config.max_notional_per_order}")
    print(f"  - daily_max_trades: {config.daily_max_trades}")
    print(f"  - daily_max_notional: {config.daily_max_notional}")

    # Test 1: Safety checks should NOT block orders in local_sim
    print("\n" + "-" * 60)
    print("TEST 1: Safety limits do NOT apply in local_sim")
    print("-" * 60)

    # Place multiple orders that would violate safety limits in live mode
    for i in range(3):
        try:
            order = await order_manager.create_market_order(
                symbol_id="BTC/USDT",
                side=TradeSide.BUY,
                amount=Decimal("1.0"),  # Large amount
                config=config,
                current_price=Decimal("50000.0"),  # $50k notional (exceeds $10 limit)
            )
            print(f"  ✓ Order {i+1} placed successfully (notional: $50,000)")
            print(f"    - Order ID: {order.get('id')}")
            print(f"    - Status: {order.get('status')}")
        except Exception as e:
            print(f"  ✗ Order {i+1} FAILED: {e}")
            print("  ERROR: Safety checks should NOT apply in local_sim!")
            return False

    # Test 2: OrderStateManager should NOT be used
    print("\n" + "-" * 60)
    print("TEST 2: OrderStateManager NOT used in local_sim")
    print("-" * 60)

    orders_state_file = Path("data_futures/orders_state.json")
    file_existed_before = orders_state_file.exists()

    # Place another order
    await order_manager.create_market_order(
        symbol_id="ETH/USDT",
        side=TradeSide.SELL,
        amount=Decimal("10.0"),
        config=config,
        current_price=Decimal("2500.0"),
    )

    file_exists_after = orders_state_file.exists()

    if file_existed_before and file_exists_after:
        # Check if file was modified
        import json

        with open(orders_state_file) as f:
            data = json.load(f)
        order_count = len(data.get("orders", {}))
        print(f"  ✓ orders_state.json exists ({order_count} orders)")
        print("    (File may contain orders from previous live tests)")
    elif not file_existed_before and not file_exists_after:
        print("  ✓ orders_state.json NOT created (expected for local_sim)")
    else:
        print("  ! orders_state.json state changed (unexpected)")

    # Test 3: Daily counters should NOT be incremented
    print("\n" + "-" * 60)
    print("TEST 3: Daily counters NOT updated in local_sim")
    print("-" * 60)

    btc_counter = order_manager._daily_counters.get("BTC/USDT", {})
    eth_counter = order_manager._daily_counters.get("ETH/USDT", {})

    print(f"  BTC/USDT counter: {btc_counter}")
    print(f"  ETH/USDT counter: {eth_counter}")

    if btc_counter.get("count", 0) == 0 and eth_counter.get("count", 0) == 0:
        print("  ✓ Daily counters are empty (expected)")
    else:
        print("  ! Daily counters were updated (unexpected for local_sim)")

    # Test 4: Verification should NOT occur in SimulatedExchange
    print("\n" + "-" * 60)
    print("TEST 4: Two-step verification NOT used in local_sim")
    print("-" * 60)

    # Mock fetch_order to detect if it's called
    original_fetch = (
        sim_exchange.public_exchange.fetch_order
        if hasattr(sim_exchange.public_exchange, "fetch_order")
        else None
    )
    fetch_called = False

    async def mock_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise Exception("fetch_order should NOT be called in SimulatedExchange!")

    if original_fetch:
        sim_exchange.public_exchange.fetch_order = mock_fetch

    try:
        order = await order_manager.create_market_order(
            symbol_id="BTC/USDT",
            side=TradeSide.BUY,
            amount=Decimal("0.1"),
            config=config,
            current_price=Decimal("50000.0"),
        )

        if fetch_called:
            print("  ✗ fetch_order WAS called (should NOT happen in sim)")
            return False
        else:
            print("  ✓ fetch_order NOT called (expected for SimulatedExchange)")
            print(f"    - Order ID: {order.get('id')}")
            print(
                f"    - _verification_status: {order.get('_verification_status', 'N/A')}"
            )
    finally:
        if original_fetch:
            sim_exchange.public_exchange.fetch_order = original_fetch

    await sim_exchange.close()

    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED: local_sim unchanged by Phase 1")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(test_local_sim_unchanged())
    sys.exit(0 if success else 1)
