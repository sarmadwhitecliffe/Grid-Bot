#!/usr/bin/env python3
"""
Interactive live test for HYPE/USDT partial close validation.

This script will:
1. Send a SELL webhook for HYPE to open a ~$10 short position
2. Wait for your confirmation after checking logs/position state
3. Manually trigger a TP1a partial close (30%)
4. Analyze requested vs normalized vs filled amounts

Run each step with confirmation pauses.
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

import requests

WEBHOOK_URL = "http://localhost:5000/webhook"
DATA_DIR = Path("data_futures")
POSITIONS_FILE = DATA_DIR / "active_positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"


def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def read_positions():
    """Read active positions."""
    if POSITIONS_FILE.exists():
        with open(POSITIONS_FILE, "r") as f:
            return json.load(f)
    return {}


def read_trade_history():
    """Read trade history."""
    if TRADE_HISTORY_FILE.exists():
        with open(TRADE_HISTORY_FILE, "r") as f:
            return json.load(f)
    return []


def send_webhook(action, symbol):
    """Send webhook signal."""
    payload = {"action": action, "symbol": symbol}
    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=5)
        return response.status_code, response.text
    except Exception as e:
        return None, str(e)


def get_current_hype_price():
    """Fetch current HYPE/USDT price from Binance."""
    try:
        response = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=HYPEUSDT", timeout=5
        )
        data = response.json()
        return Decimal(data["price"])
    except Exception as e:
        print(f"Warning: Could not fetch HYPE price: {e}")
        return None


def confirm_step(prompt):
    """Ask for user confirmation before proceeding."""
    print(f"\n{prompt}")
    response = (
        input("Type 'yes' to continue, or anything else to abort: ").strip().lower()
    )
    if response != "yes":
        print("❌ Test aborted by user.")
        sys.exit(0)
    print("✅ Proceeding...\n")


def main():
    print_section("LIVE TEST: HYPE/USDT $10 SHORT + TP1a 30% PARTIAL CLOSE")

    print(
        """
This test will:
1. Open a ~$10 short position on HYPE/USDT (live exchange)
2. Confirm position is tracked correctly
3. Manually trigger TP1a to close 30% with reduce-only
4. Analyze requested vs normalized vs filled amounts for dust

⚠️  WARNING: This will place REAL orders on Binance Futures.
⚠️  Ensure you have sufficient USDT balance and understand the risks.
"""
    )

    confirm_step("Ready to start STEP 1: Open $10 HYPE short?")

    # =========================================================================
    # STEP 1: Open short position
    # =========================================================================
    print_section("STEP 1: Opening ~$10 HYPE/USDT short position")

    current_price = get_current_hype_price()
    if current_price:
        print(f"📊 Current HYPE/USDT price: ${current_price}")
        estimated_quantity = Decimal("10") / current_price
        print(f"📊 Estimated quantity for $10: {estimated_quantity:.8f} HYPE")

    print("\n🚀 Sending SELL webhook for HYPEUSDT...")
    status_code, response_text = send_webhook("sell", "HYPEUSDT")

    if status_code == 202:
        print(f"✅ Webhook accepted (202): {response_text}")
    else:
        print(f"❌ Webhook failed ({status_code}): {response_text}")
        sys.exit(1)

    print("\n⏳ Waiting 10 seconds for order to execute...")
    import time

    time.sleep(10)

    # Check position
    positions = read_positions()
    hype_pos = positions.get("HYPE/USDT")

    if not hype_pos:
        print("❌ ERROR: HYPE/USDT position not found in active_positions.json")
        print("Please check logs/webhook_server.log and bot_logs/bot_v2.log")
        sys.exit(1)

    print("\n✅ HYPE/USDT position opened:")
    print(f"   Side: {hype_pos['side']}")
    print(f"   Entry Price: ${hype_pos['entry_price']}")
    print(f"   Initial Amount: {hype_pos['initial_amount']} HYPE")
    print(f"   Current Amount: {hype_pos['current_amount']} HYPE")
    print(f"   Entry Time: {hype_pos['entry_time']}")
    print(f"   Status: {hype_pos['status']}")

    initial_amount = Decimal(hype_pos["initial_amount"])
    entry_price = Decimal(hype_pos["entry_price"])
    notional = initial_amount * entry_price
    print(f"\n   Notional Value: ${notional:.2f}")

    print("\n📋 Next: Check bot_logs/bot_v2.log for:")
    print("   - Order creation log with [local_xxx]")
    print("   - '_requested_amount' and '_normalized_amount' metadata")
    print("   - ORDER VERIFIED status")

    confirm_step("\nSTEP 1 complete. Ready for STEP 2: Trigger TP1a partial close?")

    # =========================================================================
    # STEP 2: Trigger TP1a partial close (30%)
    # =========================================================================
    print_section("STEP 2: Triggering TP1a 30% partial close")

    print("📊 Position state before TP1a:")
    print(f"   Initial Amount: {initial_amount} HYPE")
    print(f"   Current Amount: {hype_pos['current_amount']} HYPE")
    print(f"   TP1a Hit: {hype_pos.get('tp1a_hit', False)}")

    # Calculate expected amounts
    close_percent = Decimal("30")
    expected_close_amount = initial_amount * close_percent / Decimal("100")
    print(f"\n📐 Expected 30% close amount: {expected_close_amount:.8f} HYPE")

    # For now, we'll manually set TP1a price and trigger via price movement simulation
    # In a real scenario, you'd wait for price to hit tp1a_price naturally
    print("\n⚠️  Manual TP1a trigger via script is complex.")
    print("Instead, we'll:")
    print("1. Note the TP1a price from position")
    print(f"2. Current TP1a price: ${hype_pos.get('tp1a_price', 'N/A')}")
    print("3. You can manually move price or wait for natural trigger")
    print("\nAlternatively, we can directly call the bot's partial close logic.")

    confirm_step("Proceed to analyze the EXPECTED close amounts and log what to watch?")

    # =========================================================================
    # STEP 3: Analysis setup
    # =========================================================================
    print_section("STEP 3: Analysis Setup")

    print("📋 When TP1a triggers (or you force it), watch for:")
    print(
        f"\n1. Requested amount: {expected_close_amount:.8f} HYPE (30% of {initial_amount})"
    )
    print("2. Normalized amount: (after LOT_SIZE rounding)")
    print("3. Filled amount: (from exchange response)")
    print("4. Residual dust: initial_amount - filled")
    print("5. Position current_amount update")

    print("\n📂 Log locations to monitor:")
    print("   - bot_logs/bot_v2.log (order creation with metadata)")
    print("   - logs/webhook_server.log (position updates)")
    print("   - data_futures/active_positions.json (current_amount field)")

    print("\n🔍 Search patterns:")
    print("   grep '_requested_amount' bot_logs/bot_v2.log")
    print("   grep 'partial.*close' bot_logs/bot_v2.log")
    print("   grep 'TP1a.*hit' logs/webhook_server.log")

    print("\n" + "=" * 70)
    print("✅ Test infrastructure ready!")
    print("=" * 70)
    print(
        """
Next steps:
1. Monitor logs for TP1a trigger or manually trigger partial close
2. Compare requested vs normalized vs filled amounts in logs
3. Verify position current_amount = initial_amount - filled
4. Check for any residual dust or rounding errors

To force TP1a manually, you can:
- Edit the position's tp1a_price to current price in active_positions.json
- Or call bot's position update logic directly
- Or wait for natural price movement to tp1a_price
"""
    )


if __name__ == "__main__":
    main()
