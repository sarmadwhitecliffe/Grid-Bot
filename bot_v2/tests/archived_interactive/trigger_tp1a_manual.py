#!/usr/bin/env python3
"""
Direct TP1a Trigger for HYPE/USDT

This script will:
1. Load the current HYPE/USDT position
2. Simulate TP1a hit by calling the bot's partial close logic directly
3. Monitor the order execution with requested/normalized/filled metadata
"""

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

# Add bot_v2 to path
sys.path.insert(0, str(Path(__file__).parent))

import os

from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.execution.order_manager import OrderManager
from bot_v2.models.enums import TradeSide

DATA_DIR = Path("data_futures")
POSITIONS_FILE = DATA_DIR / "active_positions.json"


async def main():
    print("=" * 70)
    print("  STEP 3: Trigger TP1a 30% Partial Close on HYPE/USDT")
    print("=" * 70)

    # Load position
    with open(POSITIONS_FILE, "r") as f:
        positions_data = json.load(f)

    hype_data = positions_data.get("HYPE/USDT")
    if not hype_data:
        print("❌ ERROR: HYPE/USDT position not found!")
        return

    print("\n📊 Current HYPE/USDT Position:")
    print(f"   Side: {hype_data['side']}")
    print(f"   Entry Price: ${hype_data['entry_price']}")
    print(f"   Initial Amount: {hype_data['initial_amount']} HYPE")
    print(f"   Current Amount: {hype_data['current_amount']} HYPE")
    print(f"   TP1a Price: ${hype_data['tp1a_price']}")
    print(f"   TP1a Hit: {hype_data['tp1a_hit']}")

    initial_amount = Decimal(hype_data["initial_amount"])
    Decimal(hype_data["current_amount"])
    Decimal(hype_data["entry_price"])

    # Calculate 30% close
    tp1a_close_percent = Decimal("30")
    requested_close_amount = initial_amount * tp1a_close_percent / Decimal("100")

    print("\n📐 TP1a Calculation:")
    print("   Close Percent: 30%")
    print(f"   Requested Close Amount: {requested_close_amount:.8f} HYPE")
    print(f"   Expected Remaining: {initial_amount - requested_close_amount:.8f} HYPE")

    print("\n⚠️  This will place a REAL reduce-only BUY order on Binance Futures!")
    print("   Symbol: HYPE/USDT")
    print("   Side: BUY (close short)")
    print(f"   Amount: {requested_close_amount} HYPE")
    print("   Params: reduceOnly=True, positionSide=SHORT")

    response = (
        input("\nType 'yes' to execute the partial close order: ").strip().lower()
    )
    if response != "yes":
        print("❌ Aborted by user.")
        return

    print("\n🚀 Executing partial close order...")

    # Initialize LiveExchange with credentials from environment
    api_key = os.getenv("FUTURES_API_KEY")
    api_secret = os.getenv("FUTURES_API_SECRET")

    if not api_key or not api_secret:
        print(
            "❌ ERROR: FUTURES_API_KEY and FUTURES_API_SECRET must be set in environment!"
        )
        print("   These are required for live trading.")
        return

    exchange = LiveExchange(name="binance", key=api_key, secret=api_secret)
    await exchange.setup()

    # Initialize OrderManager
    order_manager = OrderManager(exchange=exchange, data_dir=DATA_DIR)

    try:
        # Fetch current price for notional calculation
        current_price = await exchange.get_market_price("HYPEUSDT")
        print(f"📊 Current HYPE/USDT price: ${current_price}")

        # Create the partial close order
        print("\n📤 Creating market order:")
        print("   Symbol: HYPE/USDT")
        print("   Side: BUY (close short)")
        print(f"   Amount: {requested_close_amount}")
        print("   Reduce Only: True")

        order_params = {
            "reduceOnly": True,
            "positionSide": "SHORT",  # For hedged accounts
        }

        order_result = await order_manager.create_market_order(
            symbol_id="HYPE/USDT",
            side=TradeSide.BUY,  # BUY to close short
            amount=requested_close_amount,
            params=order_params,
            current_price=current_price,
            config=None,  # No safety checks for manual test
        )

        print("\n✅ Order executed successfully!")
        print("\n📊 Order Details:")
        print(f"   Order ID: {order_result.get('id', 'N/A')}")
        print(f"   Status: {order_result.get('status', 'N/A')}")
        print(f"   Filled: {order_result.get('filled', 'N/A')} HYPE")
        print(f"   Average Price: ${order_result.get('average', 'N/A')}")
        print(f"   Fee: {order_result.get('fee', {}).get('cost', 'N/A')}")

        # Show metadata
        print("\n📋 Sizing Metadata:")
        print(f"   _requested_amount: {order_result.get('_requested_amount', 'N/A')}")
        print(f"   _normalized_amount: {order_result.get('_normalized_amount', 'N/A')}")
        print(
            f"   _current_price_used: {order_result.get('_current_price_used', 'N/A')}"
        )
        print(f"   _notional: {order_result.get('_notional', 'N/A')}")
        print(
            f"   _verification_status: {order_result.get('_verification_status', 'N/A')}"
        )

        filled_amount = Decimal(str(order_result.get("filled", 0)))
        expected_remaining = initial_amount - filled_amount
        actual_dust = requested_close_amount - filled_amount

        print("\n📐 Reconciliation:")
        print(f"   Initial Amount: {initial_amount} HYPE")
        print(f"   Requested Close: {requested_close_amount} HYPE (30%)")
        print(f"   Filled Amount: {filled_amount} HYPE")
        print(f"   Dust/Residual: {actual_dust} HYPE")
        print(f"   Expected Remaining: {expected_remaining} HYPE")

        if actual_dust != 0:
            print(
                f"\n⚠️  DUST DETECTED: {actual_dust} HYPE difference between requested and filled!"
            )
        else:
            print("\n✅ No dust: Requested and filled amounts match exactly.")

        print("\n📂 Next steps:")
        print("   1. Check data_futures/active_positions.json")
        print(f"      - Verify current_amount updated to {expected_remaining}")
        print("   2. Check bot_logs/bot_v2.log for order metadata")
        print("   3. If dust exists, consider implementing sweep logic")

    except Exception as e:
        print(f"\n❌ ERROR executing partial close: {e}")
        import traceback

        traceback.print_exc()

    finally:
        await exchange.close()
        print("\n✅ Exchange connection closed.")


if __name__ == "__main__":
    asyncio.run(main())
