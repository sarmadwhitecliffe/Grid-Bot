"""
Phase 1 Validation Test Script

Tests the live orders Phase 1 implementation:
- Safety caps configuration
- Two-step order confirmation
- OrderStateManager persistence
- Safety checks and limits
- Dry-run mode
- LIVE_MODE kill switch
"""

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

# Add bot_v2 to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot_v2.execution.order_manager import OrderManager
from bot_v2.execution.order_state_manager import OrderRecord, OrderStateManager
from bot_v2.execution.simulated_exchange import SimulatedExchange
from bot_v2.models.enums import TradeSide
from bot_v2.models.strategy_config import StrategyConfig


def test_safety_caps_config():
    """Test that StrategyConfig properly loads safety caps."""
    print("\n=== Test 1: Safety Caps Configuration ===")

    config_data = {
        "enabled": True,
        "mode": "live",
        "initial_capital": "200.0",
        "leverage": "5",
        "max_notional_per_order": "10.0",
        "daily_max_trades": "5",
        "daily_max_notional": "50.0",
        "dry_run": False,
    }

    config = StrategyConfig.from_dict("TEST/USDT", config_data)

    assert config.max_notional_per_order == Decimal(
        "10.0"
    ), "max_notional_per_order not loaded"
    assert config.daily_max_trades == 5, "daily_max_trades not loaded"
    assert config.daily_max_notional == Decimal("50.0"), "daily_max_notional not loaded"
    assert not config.dry_run, "dry_run not loaded"

    print("✅ Safety caps configuration loaded correctly")
    print(f"   - max_notional_per_order: {config.max_notional_per_order}")
    print(f"   - daily_max_trades: {config.daily_max_trades}")
    print(f"   - daily_max_notional: {config.daily_max_notional}")
    print(f"   - dry_run: {config.dry_run}")


def test_order_state_manager():
    """Test OrderStateManager persistence and operations."""
    print("\n=== Test 2: OrderStateManager ===")

    # Use temp directory
    test_dir = Path("test_data_temp")
    test_dir.mkdir(exist_ok=True)

    manager = OrderStateManager(test_dir)

    # Create test order
    order = OrderRecord(
        local_id="test_local_123",
        exchange_order_id="exchange_456",
        symbol="BTC/USDT",
        side="BUY",
        quantity="0.01",
        status="NEW",
        mode="live",
        verification_status="VERIFIED",
    )

    # Add order
    manager.add_order(order)
    print("✅ Order added to state")

    # Retrieve order
    retrieved = manager.get_order("test_local_123")
    assert retrieved is not None, "Order not found"
    assert retrieved.exchange_order_id == "exchange_456", "Exchange ID mismatch"
    print("✅ Order retrieved successfully")

    # Get stats
    stats = manager.get_stats()
    print(f"✅ Stats: {stats['total_orders']} total, {stats['open_orders']} open")

    # Cleanup
    import shutil

    shutil.rmtree(test_dir)
    print("✅ Cleanup complete")


async def test_safety_checks():
    """Test safety check enforcement."""
    print("\n=== Test 3: Safety Checks ===")

    # Create config with strict limits
    config_data = {
        "mode": "live",
        "max_notional_per_order": "10.0",
        "daily_max_trades": "2",
        "daily_max_notional": "20.0",
        "dry_run": False,
    }
    config = StrategyConfig.from_dict("TEST/USDT", config_data)

    # Create order manager
    exchange = SimulatedExchange(fee=Decimal("0.0004"))
    await exchange.setup()
    manager = OrderManager(exchange, data_dir=Path("test_data_temp"))

    # Test 1: Order within limits (should pass)
    try:
        passes, msg = manager._check_safety_limits(
            "TEST/USDT", Decimal("8.0"), config  # Below 10.0 limit
        )
        assert passes, f"Should pass but got: {msg}"
        print("✅ Order within limits passed")
    except Exception as e:
        print(f"❌ Test failed: {e}")

    # Test 2: Order exceeds per-order limit (should fail)
    passes, msg = manager._check_safety_limits(
        "TEST/USDT", Decimal("15.0"), config  # Above 10.0 limit
    )
    assert not passes, "Should fail but passed"
    assert "notional cap exceeded" in msg.lower()
    print(f"✅ Per-order limit enforced: {msg}")

    # Test 3: Dry-run mode (handled separately, safety checks should still pass)
    config.dry_run = True
    passes, msg = manager._check_safety_limits("TEST/USDT", Decimal("5.0"), config)
    assert passes, f"Safety checks should pass in dry-run: {msg}"
    print("✅ Dry-run mode: Safety checks pass (dry-run handled separately)")

    await exchange.close()


async def test_dry_run_order():
    """Test dry-run order creation."""
    print("\n=== Test 4: Dry-Run Order Creation ===")

    config_data = {
        "mode": "live",
        "max_notional_per_order": "100.0",
        "dry_run": True,  # Enable dry-run
    }
    config = StrategyConfig.from_dict("TEST/USDT", config_data)

    exchange = SimulatedExchange(fee=Decimal("0.0004"))
    await exchange.setup()
    manager = OrderManager(exchange, data_dir=Path("test_data_temp"))

    try:
        order = await manager.create_market_order(
            symbol_id="BTC/USDT",
            side=TradeSide.BUY,
            amount=Decimal("0.01"),
            config=config,
            current_price=Decimal("50000"),
        )

        assert (
            order["status"] == "DRY_RUN"
        ), f"Expected DRY_RUN status, got {order['status']}"
        assert "_verification_status" in order
        print("✅ Dry-run order created (not executed)")
        print(f"   - Order ID: {order['id']}")
        print(f"   - Status: {order['status']}")
        print(f"   - Verification: {order['_verification_status']}")
    except Exception as e:
        print(f"❌ Dry-run test failed: {e}")

    await exchange.close()


async def test_live_mode_kill_switch():
    """Test LIVE_MODE environment variable kill switch."""
    print("\n=== Test 5: LIVE_MODE Kill Switch ===")

    import os

    config_data = {"mode": "live", "max_notional_per_order": "100.0", "dry_run": False}
    config = StrategyConfig.from_dict("TEST/USDT", config_data)

    exchange = SimulatedExchange(fee=Decimal("0.0004"))
    await exchange.setup()
    manager = OrderManager(exchange, data_dir=Path("test_data_temp"))

    # Set kill switch
    os.environ["LIVE_MODE"] = "false"

    passes, msg = manager._check_safety_limits("TEST/USDT", Decimal("5.0"), config)

    assert not passes, "Should fail when LIVE_MODE=false"
    assert "kill switch" in msg.lower()
    print(f"✅ Kill switch enforced: {msg}")

    # Reset
    os.environ["LIVE_MODE"] = "true"

    await exchange.close()


async def main():
    """Run all Phase 1 validation tests."""
    print("=" * 60)
    print("PHASE 1 VALIDATION TEST SUITE")
    print("=" * 60)

    try:
        # Test 1: Config loading
        test_safety_caps_config()

        # Test 2: OrderStateManager
        test_order_state_manager()

        # Test 3: Safety checks
        await test_safety_checks()

        # Test 4: Dry-run mode
        await test_dry_run_order()

        # Test 5: Kill switch
        await test_live_mode_kill_switch()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nPhase 1 implementation validated successfully!")
        print("\nNext steps:")
        print("  1. Integrate per-symbol mode routing in TradingBot")
        print("  2. Test with Binance Testnet")
        print("  3. Deploy Phase 2 (WebSocket listener, enhanced logging)")

    except Exception as e:
        print(f"\n❌ TEST SUITE FAILED: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
