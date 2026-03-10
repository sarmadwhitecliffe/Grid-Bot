#!/usr/bin/env python3
"""
scripts/run_backtest.py
-----------------------
Standalone script to execute 90-day BTC/USDT grid strategy backtest.

Fetches historical OHLCV data via ccxt, runs GridBacktester simulation,
generates performance report, and validates against target metrics.

Usage:
    python scripts/run_backtest.py
    python scripts/run_backtest.py --symbol ETH/USDT --timeframe 4h
    python scripts/run_backtest.py --days 60 --capital 5000
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List

import ccxt.async_support as ccxt
import pandas as pd
import yaml

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import GridBotSettings
from src.backtest.grid_backtester import GridBacktester
from src.backtest.backtest_report import (
    TARGET_WIN_RATE,
    TARGET_PROFIT_FACTOR,
    TARGET_MAX_DRAWDOWN,
    BacktestReport,
)

# Retry configuration for network failures
MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 5]  # seconds

logger = logging.getLogger(__name__)


async def fetch_ohlcv_with_retry(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since: int,
    limit: int,
) -> List[List[float]]:
    """
    Fetch OHLCV data with exponential backoff retry logic.

    Args:
        exchange: Async ccxt exchange instance.
        symbol: Trading pair symbol (e.g., 'BTC/USDT').
        timeframe: Candle timeframe (e.g., '1h').
        since: Unix timestamp (ms) to start fetching from.
        limit: Maximum number of candles to fetch.

    Returns:
        List of OHLCV candles: [[timestamp, open, high, low, close, volume], ...]

    Raises:
        ccxt.NetworkError: If all retry attempts fail.
    """
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(
                "Fetching OHLCV: symbol=%s timeframe=%s limit=%d (attempt %d/%d)",
                symbol,
                timeframe,
                limit,
                attempt + 1,
                MAX_RETRIES,
            )
            candles = await exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=since,
                limit=limit,
            )
            logger.info("Fetched %d candles for %s %s", len(candles), symbol, timeframe)
            return candles
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                logger.warning(
                    "Network error on attempt %d: %s — retrying in %ds",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("All retry attempts exhausted for OHLCV fetch")
                raise

    return []


async def fetch_historical_data(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    days: int,
) -> pd.DataFrame:
    cache_dir = PROJECT_ROOT / "data" / "cache" / "ohlcv_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace("/", "_")
    cache_path = cache_dir / f"{safe_symbol}_{timeframe}_{days}d_backtest.parquet"

    if cache_path.exists():
        logger.info(
            f"Loading {days} days of {symbol} {timeframe} data from Parquet cache: {cache_path}"
        )
        return pd.read_parquet(cache_path)

    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({"enableRateLimit": True})

    try:
        await exchange.load_markets()
        timeframe_minutes = {
            "1m": 1,
            "5m": 5,
            "15m": 15,
            "1h": 60,
            "4h": 240,
            "1d": 1440,
        }
        minutes_per_candle = timeframe_minutes.get(timeframe, 60)
        candles_per_day = 1440 / minutes_per_candle
        total_candles = int(days * candles_per_day)

        logger.info(
            "Fetching %d days (%d candles) of %s %s data from %s",
            days,
            total_candles,
            symbol,
            timeframe,
            exchange_id,
        )

        now = datetime.utcnow()
        start_time = now - timedelta(days=days)
        since = int(start_time.timestamp() * 1000)

        all_candles = []
        chunk_size = 1000
        current_since = since

        while len(all_candles) < total_candles:
            chunk = await fetch_ohlcv_with_retry(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                since=int(current_since),
                limit=chunk_size,
            )

            if not chunk:
                break
            all_candles.extend(chunk)
            current_since = int(chunk[-1][0]) + 1
            if current_since > int(now.timestamp() * 1000):
                break

        df = pd.DataFrame(
            all_candles,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Save to Parquet
        logger.info(f"Caching fetched data to Parquet: {cache_path}")
        df.to_parquet(cache_path, engine="pyarrow")

        return df
    finally:
        await exchange.close()


def create_config_snapshot(settings: GridBotSettings) -> str:
    """
    Generate a human-readable configuration summary for the report.

    Args:
        settings: Validated GridBotSettings instance.

    Returns:
        Multi-line string with key configuration parameters.
    """
    lines = [
        "Configuration Snapshot:",
        "-" * 50,
        f"Symbol         : {settings.SYMBOL}",
        f"Market Type    : {settings.MARKET_TYPE}",
        f"Grid Type      : {settings.GRID_TYPE}",
        f"Grid Spacing   : {settings.GRID_SPACING_PCT * 100:.2f}% (geometric) / ${settings.GRID_SPACING_ABS} (arithmetic)",
        f"Grids Up/Down  : {settings.NUM_GRIDS_UP} / {settings.NUM_GRIDS_DOWN}",
        f"Order Size     : ${settings.ORDER_SIZE_QUOTE} USDT",
        f"Total Capital  : ${settings.TOTAL_CAPITAL} USDT",
        f"Max Drawdown   : {settings.MAX_DRAWDOWN_PCT * 100:.1f}%",
        f"Stop Loss      : {settings.STOP_LOSS_PCT * 100:.1f}%",
        f"ADX Threshold  : {settings.ADX_THRESHOLD}",
        f"Recentre Trigger: {settings.RECENTRE_TRIGGER} levels",
        "-" * 50,
    ]
    return "\n".join(lines)


def save_report(
    report: BacktestReport,
    settings: GridBotSettings,
    symbol: str,
    timeframe: str,
    days: int,
    output_path: Path,
) -> None:
    """
    Save formatted backtest report to text file.

    Args:
        report: Generated BacktestReport instance.
        settings: Configuration used for the backtest.
        symbol: Trading pair tested.
        timeframe: Candle timeframe used.
        days: Number of days simulated.
        output_path: Output file path.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    content = [
        "=" * 70,
        f"Grid Bot Backtest Report — {symbol} {timeframe}",
        "=" * 70,
        f"Generated: {timestamp}",
        f"Simulation Period: {days} days",
        "",
        create_config_snapshot(settings),
        "",
        report.summary(),
        "",
        "=" * 70,
        "Target Validation (TASK-504):",
        "-" * 70,
        f"Win Rate       : {report.win_rate() * 100:.1f}% {'✓' if report.win_rate() >= TARGET_WIN_RATE else '✗'} (target: {TARGET_WIN_RATE * 100:.0f}%)",
        f"Profit Factor  : {report.profit_factor():.2f} {'✓' if report.profit_factor() >= TARGET_PROFIT_FACTOR else '✗'} (target: {TARGET_PROFIT_FACTOR:.1f})",
        f"Max Drawdown   : {report.max_drawdown() * 100:.1f}% {'✓' if report.max_drawdown() <= TARGET_MAX_DRAWDOWN else '✗'} (target: ≤{TARGET_MAX_DRAWDOWN * 100:.0f}%)",
        "",
        "=" * 70,
        f"Overall Verdict: {'PASSED ✓' if report.passes_targets(TARGET_WIN_RATE, TARGET_PROFIT_FACTOR, 0.0, TARGET_MAX_DRAWDOWN) else 'FAILED ✗'}",
        "=" * 70,
    ]

    report_text = "\n".join(content)

    with open(output_path, "w") as f:
        f.write(report_text)

    # Also export trades to CSV if there are any
    csv_path = output_path.with_suffix(".csv")
    report.export_trades_csv(str(csv_path))

    logger.info("Report saved to %s", output_path)


async def main(args: argparse.Namespace) -> int:
    """
    Main execution function.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code: 0 if targets met, 1 if failed or error occurred.
    """
    try:
        # Load configuration
        settings = GridBotSettings()
        
        # Manual override from YAML to ensure optimized params are used
        with open(PROJECT_ROOT / "config" / "grid_config.yaml", "r") as f:
            y_cfg = yaml.safe_load(f)
            for k, v in y_cfg.items():
                if hasattr(settings, k):
                    setattr(settings, k, v)

        # Override from CLI args
        if args.symbol: settings.SYMBOL = args.symbol
        if args.timeframe: settings.OHLCV_TIMEFRAME = args.timeframe
        if args.capital: settings.TOTAL_CAPITAL = args.capital
        
        symbol = settings.SYMBOL
        timeframe = settings.OHLCV_TIMEFRAME
        days = args.days
        capital = settings.TOTAL_CAPITAL

        logger.info(
            "Starting backtest: symbol=%s timeframe=%s days=%d capital=$%.2f",
            symbol,
            timeframe,
            days,
            capital,
        )

        # Fetch historical data
        df: pd.DataFrame = await fetch_historical_data(
            exchange_id=settings.EXCHANGE_ID,
            symbol=symbol,
            timeframe=timeframe,
            days=days,
        )

        if len(df) < 100:
            logger.error("Insufficient data: only %d candles fetched", len(df))
            return 1

        indicator_warmup = 50

        # Optional: subset to exact dates to precisely mimic optimizer out-of-sample splits
        if args.exact_start_date:
            start_dt = pd.to_datetime(args.exact_start_date)
            eligible = df.loc[df["timestamp"] >= start_dt]
            if eligible.empty:
                logger.error(
                    "Start date %s is outside fetched data range", args.exact_start_date
                )
            else:
                start_idx = int(eligible.index.to_list()[0])
                indicator_warmup = int(min(indicator_warmup, start_idx))
                keep_from_idx = int(max(0, start_idx - indicator_warmup))
                df = df.iloc[keep_from_idx:].reset_index(drop=True)
                logger.info(
                    "Subsetted data to start exact simulation at %s (warmup=%d candles)",
                    start_dt,
                    indicator_warmup,
                )

        if args.exact_end_date:
            end_dt = pd.to_datetime(args.exact_end_date)
            df = df.loc[df["timestamp"] <= end_dt].reset_index(drop=True)
            logger.info("Subsetted data to end exact simulation at %s", end_dt)

        # Run backtest
        logger.info("Initializing GridBacktester")
        print(f"DEBUG: Spacing: {settings.GRID_SPACING_PCT}, ADX: {settings.ADX_THRESHOLD}, BB: {settings.bb_width_threshold}, Grids: {settings.NUM_GRIDS_UP}")
        backtester = GridBacktester(
            settings=settings,
            initial_capital=capital,
            indicator_warmup=indicator_warmup,
        )

        # Force injection of bb_width_threshold from YAML/ENV
        if hasattr(settings, "bb_width_threshold"):
            backtester.regime_detector.bb_width_threshold = settings.bb_width_threshold

        logger.info("Running simulation...")
        result = backtester.run(df)

        # Generate report
        logger.info("Generating performance report")
        report = BacktestReport(result)

        # Print to console
        print("\n" + "=" * 70)
        print(report.summary())
        print("=" * 70)
        print(f"\nTarget Validation (TASK-504):")
        print(
            f"  Win Rate      : {report.win_rate() * 100:.1f}% {'✓' if report.win_rate() >= TARGET_WIN_RATE else '✗'} (target: ≥{TARGET_WIN_RATE * 100:.0f}%)"
        )
        print(
            f"  Profit Factor : {report.profit_factor():.2f} {'✓' if report.profit_factor() >= TARGET_PROFIT_FACTOR else '✗'} (target: ≥{TARGET_PROFIT_FACTOR:.1f})"
        )
        print(
            f"  Max Drawdown  : {report.max_drawdown() * 100:.1f}% {'✓' if report.max_drawdown() <= TARGET_MAX_DRAWDOWN else '✗'} (target: ≤{TARGET_MAX_DRAWDOWN * 100:.0f}%)"
        )

        passed = report.passes_targets(
            target_win_rate=TARGET_WIN_RATE,
            target_profit_factor=TARGET_PROFIT_FACTOR,
            target_sharpe=0.0,  # Not checking Sharpe for TASK-504
            target_max_drawdown=TARGET_MAX_DRAWDOWN,
        )

        print(f"\nOverall Verdict: {'PASSED ✓' if passed else 'FAILED ✗'}")
        print("=" * 70 + "\n")

        # Save report to file
        output_path = (
            PROJECT_ROOT
            / "data"
            / "backtest_results"
            / f"{symbol.replace('/', '_').lower()}_{days}d_report.txt"
        )
        save_report(
            report=report,
            settings=settings,
            symbol=symbol,
            timeframe=timeframe,
            days=days,
            output_path=output_path,
        )

        return 0 if passed else 1

    except Exception as exc:
        logger.exception("Backtest failed with error: %s", exc)
        return 1


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Run Grid Bot backtest simulation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_backtest.py
  python scripts/run_backtest.py --symbol ETH/USDT --timeframe 4h
  python scripts/run_backtest.py --days 60 --capital 5000
        """,
    )

    parser.add_argument(
        "--symbol",
        type=str,
        help="Trading pair symbol (default: from config)",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        help="Candle timeframe (default: from config)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=90,
        help="Number of days to simulate (default: 90)",
    )
    parser.add_argument(
        "--exact-start-date",
        type=str,
        help="Optional ISO datetime to start the exact test window from (e.g. '2025-12-30T01:00:00')",
    )
    parser.add_argument(
        "--exact-end-date",
        type=str,
        help="Optional ISO datetime to end the exact test window (e.g. '2026-02-22T04:00:00')",
    )
    parser.add_argument(
        "--capital",
        type=float,
        help="Initial capital in USDT (default: from config)",
    )
    parser.add_argument(
        "--exchange",
        type=str,
        help="ccxt exchange ID (default: from config)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose debug logging",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        # Re-enable noisy loggers when explicitly verbose
        logging.getLogger("src.strategy.regime_detector").setLevel(logging.DEBUG)
        logging.getLogger("src.strategy.grid_calculator").setLevel(logging.DEBUG)
        logging.getLogger("src.backtest.grid_backtester").setLevel(logging.DEBUG)

    exit_code = asyncio.run(main(args))
    sys.exit(exit_code)
