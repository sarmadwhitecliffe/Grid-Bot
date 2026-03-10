#!/usr/bin/env python3
"""Diagnostic script to analyze 180-day market data and explain liquidation patterns."""

import asyncio
import logging
import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_backtest import fetch_historical_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

def analyze_market_trend(df: pd.DataFrame) -> None:
    start_price = df.iloc[0]["close"]
    end_price = df.iloc[-1]["close"]
    total_change_pct = ((end_price - start_price) / start_price) * 100
    
    trend = "DOWNTREND" if total_change_pct < -5 else "UPTREND" if total_change_pct > 5 else "RANGING"
    
    df["price_change"] = df["close"] - df["open"]
    up_candles = (df["price_change"] > 0).sum()
    down_candles = (df["price_change"] < 0).sum()
    total_candles = len(df)
    
    df["cummax"] = df["close"].cummax()
    df["drawdown"] = (df["close"] - df["cummax"]) / df["cummax"]
    max_drawdown_pct = abs(df["drawdown"].min()) * 100
    
    peak_idx = df["cummax"].idxmax()
    peak_price = df.iloc[peak_idx]["close"]
    trough_idx = df["drawdown"].idxmin()
    trough_price = df.iloc[trough_idx]["close"]
    
    print("\n" + "=" * 60)
    print("=== Market Trend Analysis (180 days) ===")
    print("=" * 60)
    print(f"Start Price: ${start_price:,.2f}")
    print(f"End Price: ${end_price:,.2f}")
    print(f"Total Change: {total_change_pct:+.1f}%")
    print(f"Trend: {trend}")
    print(f"\nPeak Price: ${peak_price:,.2f} (at index {peak_idx})")
    print(f"Trough Price: ${trough_price:,.2f} (at index {trough_idx})")
    print(f"\nCandle Analysis:")
    print(f"- Up candles: {up_candles} ({up_candles/total_candles*100:.1f}%)")
    print(f"- Down candles: {down_candles} ({down_candles/total_candles*100:.1f}%)")
    print(f"- Neutral: {total_candles - up_candles - down_candles}")
    print(f"- Max drawdown from peak: {max_drawdown_pct:.1f}%")
    
    print("\n" + "=" * 60)
    print("=== Liquidation Expectation ===")
    print("=" * 60)
    
    if trend == "DOWNTREND":
        print("✓ In a DOWNTREND:")
        print("  - LONG orders fill as price falls → liquidate when price drops further")
        print("  - SHORT orders ABOVE price rarely fill")
        print("  → Expected: Many LONG liquidations, Zero SHORT liquidations")
        print(f"\nYour data: {trend} ({total_change_pct:+.1f}%)")
        print("✅ This explains why you see only LONG liquidations.")
    elif trend == "UPTREND":
        print("✓ In an UPTREND:")
        print("  - SHORT orders fill as price rises → liquidate when price rises further")
        print("  - LONG orders BELOW price rarely fill")
        print("  → Expected: Many SHORT liquidations, Zero LONG liquidations")
    else:
        print("✓ In RANGING markets:")
        print("  - Both LONG and SHORT fill and close normally")
        print("  - Liquidations should be RARE")
    
    print("=" * 60 + "\n")

async def main() -> None:
    logger.info("Fetching 180 days of BTC/USDT 1h data from Binance...")
    df = await fetch_historical_data(exchange_id="binance", symbol="BTC/USDT", timeframe="1h", days=180)
    if len(df) < 100:
        logger.error("Insufficient data fetched.")
        return
    logger.info(f"Fetched {len(df)} candles. Running trend analysis...\n")
    analyze_market_trend(df)

if __name__ == "__main__":
    asyncio.run(main())
