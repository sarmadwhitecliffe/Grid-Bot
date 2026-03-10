#!/usr/bin/env python3
"""
scripts/optimize_params.py
--------------------------
Grid-search optimizer for 6 parameters:
(GRID_SPACING_PCT, ADX_THRESHOLD, bb_width_threshold, NUM_GRIDS, RECENTRE_TRIGGER, LEVERAGE)
with walk-forward 70/30 validation.

Outputs best params per symbol to config/grid_config_optimized.json
"""

import asyncio
import itertools
import json
import logging
import multiprocessing
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Tuple

import yaml

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import GridBotSettings  # noqa: E402
from src.backtest.grid_backtester import GridBacktester  # noqa: E402
from src.backtest.backtest_report import BacktestReport  # noqa: E402
from scripts.run_backtest import fetch_historical_data  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s"
)
logger = logging.getLogger("optimizer")

OPTIMIZATION_SPACE_PATH = PROJECT_ROOT / "config" / "optimization_space.yaml"
OUTPUT_PATH = PROJECT_ROOT / "config" / "strategy_configs.json"


def load_optimization_space() -> Dict[str, Any]:
    """
    Load optimization space ranges from config/optimization_space.yaml.

    Returns:
        Mapping of parameter names to lists of candidate values.
    """
    if not OPTIMIZATION_SPACE_PATH.exists():
        raise FileNotFoundError(
            f"Optimization space file not found: {OPTIMIZATION_SPACE_PATH}"
        )

    with open(OPTIMIZATION_SPACE_PATH, "r") as f:
        space = yaml.safe_load(f)

    required_keys = {
        "SYMBOL",
        "TIMEFRAMES",
        "LOOKBACK_DAYS",
        "MIN_WIN_RATE",
        "MIN_PROFIT_FACTOR",
        "MAX_DRAWDOWN",
        "TOP_N",
        "GRID_SPACING_PCT",
        "ADX_THRESHOLD",
        "bb_width_threshold",
        "NUM_GRIDS",
        "RECENTRE_TRIGGER",
        "LEVERAGE",
    }
    missing = required_keys.difference(space.keys())
    if missing:
        raise ValueError(f"Optimization space missing keys: {sorted(missing)}")

    if not isinstance(space.get("SYMBOL"), list):
        raise ValueError("Optimization space key 'SYMBOL' must be a list")
    if not isinstance(space.get("TIMEFRAMES"), list):
        raise ValueError("Optimization space key 'TIMEFRAMES' must be a list")

    return space


def _require_scalar(space: Dict[str, Any], key: str, expected_type: type) -> Any:
    """
    Fetch a scalar config value with type validation.

    Args:
        space: Loaded optimization config dict.
        key: Key to fetch.
        expected_type: Expected scalar type.

    Returns:
        Scalar value from config.

    Raises:
        ValueError: If the value is missing or the wrong type.
    """
    if key not in space:
        raise ValueError(f"Optimization space missing key: {key}")

    value = space[key]
    if isinstance(value, list) or not isinstance(value, expected_type):
        raise ValueError(
            f"Optimization space key '{key}' must be a {expected_type.__name__}"
        )

    return value


def params_key(params: Dict[str, Any], keys: List[str]) -> Tuple[Any, ...]:
    """
    Build a stable key for a params dict.

    Args:
        params: Parameter dictionary.
        keys: Ordered list of keys to use in the key.

    Returns:
        Tuple key that can index results across timeframes.
    """
    return tuple(params[k] for k in keys)


def evaluate_params(args) -> Dict[str, Any]:
    params, df_train = args

    # Silence noisy backtest logs in worker processes
    logging.getLogger("src.backtest.grid_backtester").setLevel(logging.CRITICAL)
    logging.getLogger("src.backtest.backtest_report").setLevel(logging.CRITICAL)
    logging.getLogger("src.strategy.regime_detector").setLevel(logging.CRITICAL)
    logging.getLogger("src.strategy.grid_calculator").setLevel(logging.CRITICAL)

    # Base settings
    settings = GridBotSettings(
        EXCHANGE_ID=params["EXCHANGE_ID"],
        API_KEY=params["API_KEY"],
        API_SECRET=params["API_SECRET"],
        TESTNET=params["TESTNET"],
        MARKET_TYPE=params["MARKET_TYPE"],
        MARGIN_MODE=params["MARGIN_MODE"],
        DUAL_SIDE=params["DUAL_SIDE"],
        GRID_TYPE=params["GRID_TYPE"],
        GRID_SPACING_PCT=params["GRID_SPACING_PCT"],
        GRID_SPACING_ABS=params["GRID_SPACING_ABS"],
        NUM_GRIDS_UP=params["NUM_GRIDS"],
        NUM_GRIDS_DOWN=params["NUM_GRIDS"],
        ORDER_SIZE_QUOTE=params["ORDER_SIZE_QUOTE"],
        LOWER_BOUND=params["LOWER_BOUND"],
        UPPER_BOUND=params["UPPER_BOUND"],
        TOTAL_CAPITAL=params["TOTAL_CAPITAL"],
        RESERVE_CAPITAL_PCT=params["RESERVE_CAPITAL_PCT"],
        MAX_OPEN_ORDERS=params["MAX_OPEN_ORDERS"],
        LEVERAGE=params["LEVERAGE"],
        STOP_LOSS_PCT=params["STOP_LOSS_PCT"],
        MAX_DRAWDOWN_PCT=params["MAX_DRAWDOWN_PCT"],
        TAKE_PROFIT_PCT=params["TAKE_PROFIT_PCT"],
        ADX_THRESHOLD=params["ADX_THRESHOLD"],
        RECENTRE_TRIGGER=params["RECENTRE_TRIGGER"],
        FUNDING_INTERVAL_HOURS=params["FUNDING_INTERVAL_HOURS"],
        POLL_INTERVAL_SEC=params["POLL_INTERVAL_SEC"],
        OHLCV_TIMEFRAME=params["OHLCV_TIMEFRAME"],
        OHLCV_LIMIT=params["OHLCV_LIMIT"],
        SYMBOL=params["SYMBOL"],
        TELEGRAM_BOT_TOKEN=params["TELEGRAM_BOT_TOKEN"],
        TELEGRAM_CHAT_ID=params["TELEGRAM_CHAT_ID"],
    )

    # Note: bb_width_threshold is hardcoded in GridBot / RegimeDetector to 0.04 usually,
    # but the backtester might recreate it. Let's patch it in settings if we want it passed.

    backtester = GridBacktester(
        settings=settings,
        initial_capital=params["BACKTEST_INITIAL_CAPITAL"],
    )

    # Overwrite regime detector thresholds from optimization space
    backtester.regime_detector.bb_width_threshold = params["bb_width_threshold"]
    backtester.regime_detector.adx_threshold = params["ADX_THRESHOLD"]

    try:
        result = backtester.run(df_train)
        report = BacktestReport(result)

        return {
            "params": params,
            "win_rate": report.win_rate(),
            "profit_factor": report.profit_factor(),
            "max_drawdown": report.max_drawdown(),
            "sharpe": report.sharpe_ratio(),
            "net_profit": result.final_equity - result.initial_capital,
            "funding_fees": getattr(result, "funding_fees_usdt", 0.0),
            "num_trades": len(report._completed_trades),
        }
    except Exception:
        return {
            "params": params,
            "win_rate": 0,
            "profit_factor": 0,
            "max_drawdown": 1,
            "sharpe": -99,
            "net_profit": -999,
            "funding_fees": 0,
            "num_trades": 0,
        }


def combine_metrics(
    per_timeframe: Dict[str, Dict[Tuple[Any, ...], Dict[str, Any]]],
) -> Dict[Tuple[Any, ...], Dict[str, Any]]:
    """
    Combine metrics across timeframes for each param set.

    Args:
        per_timeframe: Mapping of timeframe -> param_key -> metrics.

    Returns:
        Mapping of param_key -> combined metrics.
    """
    timeframes = list(per_timeframe.keys())
    if not timeframes:
        return {}

    common_keys = set(per_timeframe[timeframes[0]].keys())
    for tf in timeframes[1:]:
        common_keys = common_keys.intersection(per_timeframe[tf].keys())

    combined: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for key in common_keys:
        metrics_list = [per_timeframe[tf][key] for tf in timeframes]
        avg_win_rate = sum(m["win_rate"] for m in metrics_list) / len(metrics_list)
        avg_profit_factor = sum(m["profit_factor"] for m in metrics_list) / len(
            metrics_list
        )
        avg_net_profit = sum(m["net_profit"] for m in metrics_list) / len(metrics_list)
        worst_max_drawdown = max(m["max_drawdown"] for m in metrics_list)
        min_win_rate = min(m["win_rate"] for m in metrics_list)
        avg_sharpe = sum(m["sharpe"] for m in metrics_list) / len(metrics_list)
        avg_num_trades = sum(m["num_trades"] for m in metrics_list) / len(metrics_list)

        combined[key] = {
            "params": metrics_list[0]["params"],
            "avg_win_rate": avg_win_rate,
            "min_win_rate": min_win_rate,
            "avg_profit_factor": avg_profit_factor,
            "avg_net_profit": avg_net_profit,
            "worst_max_drawdown": worst_max_drawdown,
            "avg_sharpe": avg_sharpe,
            "avg_num_trades": avg_num_trades,
        }

    return combined


def rank_combined_results(
    combined_results: Dict[Tuple[Any, ...], Dict[str, Any]],
    min_win_rate: float,
    min_profit_factor: float,
    max_drawdown: float,
    min_trades: float = 0,
) -> List[Dict[str, Any]]:
    """
    Rank combined results with guardrails.

    Args:
        combined_results: Combined metrics keyed by param set.

    Returns:
        Ranked list of combined results.
    """
    results = list(combined_results.values())
    results.sort(
        key=lambda x: (
            x["avg_profit_factor"],
            x["avg_net_profit"],
            -x["worst_max_drawdown"],
        ),
        reverse=True,
    )

    valid = [
        r
        for r in results
        if r["min_win_rate"] >= min_win_rate
        and r["avg_profit_factor"] >= min_profit_factor
        and r["worst_max_drawdown"] <= max_drawdown
        and r["avg_num_trades"] >= min_trades
    ]

    if not valid:
        if results:
            best_r = results[0]
            logger.error(
                "No combinations met guardrails (Min WR: %.2f, Min PF: %.2f, Max DD: %.2f, Min Trades: %.1f). "
                "Best found - WR: %.2f, PF: %.2f, DD: %.2f, Trades: %.1f. Using all results.",
                min_win_rate,
                min_profit_factor,
                max_drawdown,
                min_trades,
                best_r["min_win_rate"],
                best_r["avg_profit_factor"],
                best_r["worst_max_drawdown"],
                best_r["avg_num_trades"],
            )
        else:
            logger.error("No combinations available.")
        valid = results

    return valid


async def run_optimizer() -> None:
    space = load_optimization_space()
    symbols = space.pop("SYMBOL")
    timeframes = space.pop("TIMEFRAMES")
    lookback_days = int(_require_scalar(space, "LOOKBACK_DAYS", int))
    min_win_rate = float(_require_scalar(space, "MIN_WIN_RATE", float))
    min_profit_factor = float(_require_scalar(space, "MIN_PROFIT_FACTOR", float))
    min_trades = float(_require_scalar(space, "MIN_TRADES", int))
    max_drawdown = float(_require_scalar(space, "MAX_DRAWDOWN", float))
    top_n = int(_require_scalar(space, "TOP_N", int))

    base_config = {k: v for k, v in space.items() if not isinstance(v, list)}
    param_space = {k: v for k, v in space.items() if isinstance(v, list)}

    keys, values = zip(*param_space.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    logger.info(
        "Loaded %d parameter combinations from %s",
        len(combinations),
        OPTIMIZATION_SPACE_PATH,
    )

    output: Dict[str, Dict[str, Any]] = {}

    for symbol in symbols:
        logger.info("Starting optimization for %s", symbol)
        timeframe_train_results: Dict[str, Dict[Tuple[Any, ...], Dict[str, Any]]] = {}
        timeframe_test_results: Dict[str, Dict[Tuple[Any, ...], Dict[str, Any]]] = {}

        for timeframe in timeframes:
            logger.info(
                "Fetching %d days of %s data for %s", lookback_days, timeframe, symbol
            )
            df = await fetch_historical_data(
                exchange_id="binance",
                symbol=symbol,
                timeframe=timeframe,
                days=lookback_days,
            )

            if len(df) < 1000:
                logger.error("Not enough data fetched for %s %s", symbol, timeframe)
                continue

            split_idx = int(len(df) * 0.7)
            df_train = df.iloc[:split_idx].reset_index(drop=True)
            df_test = df.iloc[split_idx:].reset_index(drop=True)

            logger.info(
                "Train split: %d candles. Test split: %d candles.",
                len(df_train),
                len(df_test),
            )

            args = [
                ({**base_config, **p, "SYMBOL": symbol}, df_train) for p in combinations
            ]
            logger.info(
                "Testing %d parameter combinations on train split...", len(args)
            )

            with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
                train_results = pool.map(evaluate_params, args)

            train_map: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
            for res in train_results:
                train_map[params_key(res["params"], list(keys))] = res

            timeframe_train_results[timeframe] = train_map

        if len(timeframe_train_results) != len(timeframes):
            logger.error("Skipping %s due to missing timeframe data", symbol)
            continue

        combined_train = combine_metrics(timeframe_train_results)
        ranked_train = rank_combined_results(
            combined_train,
            min_win_rate=min_win_rate,
            min_profit_factor=min_profit_factor,
            max_drawdown=max_drawdown,
            min_trades=min_trades,
        )
        top_params = [r["params"] for r in ranked_train[:top_n]]

        for timeframe in timeframes:
            df = await fetch_historical_data(
                exchange_id="binance",
                symbol=symbol,
                timeframe=timeframe,
                days=lookback_days,
            )
            split_idx = int(len(df) * 0.7)
            df_test = df.iloc[split_idx:].reset_index(drop=True)

            test_args = [
                ({**base_config, **p, "SYMBOL": symbol}, df_test) for p in top_params
            ]
            logger.info(
                "Testing top %d combinations on test split for %s %s",
                len(test_args),
                symbol,
                timeframe,
            )

            with multiprocessing.Pool(processes=multiprocessing.cpu_count()) as pool:
                test_results = pool.map(evaluate_params, test_args)

            test_map: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
            for res in test_results:
                test_map[params_key(res["params"], list(keys))] = res

            timeframe_test_results[timeframe] = test_map

        combined_test = combine_metrics(timeframe_test_results)
        ranked_test = rank_combined_results(
            combined_test,
            min_win_rate=min_win_rate,
            min_profit_factor=min_profit_factor,
            max_drawdown=max_drawdown,
            min_trades=min_trades,
        )

        if not ranked_test:
            logger.error("No valid results for %s", symbol)
            continue

        best_result = ranked_test[0]
        best = best_result["params"]
        
        # FINAL STEP: Run a full-period backtest for the best params to ensure reported metrics 
        # match a standalone full-period backtest.
        logger.info("Running final full-period validation for best params...")
        full_df = await fetch_historical_data(
            exchange_id="binance",
            symbol=symbol,
            timeframe=timeframes[0], # Using first timeframe for summary
            days=lookback_days,
        )
        full_metrics = evaluate_params(({**best, "SYMBOL": symbol}, full_df))
        
        # Log the params being validated regardless of pass/fail
        logger.info(f"Full-period validation params for {symbol}: {best}")

        # Guardrail check on full period results
        passed_guardrails = (
            full_metrics["win_rate"] >= min_win_rate and
            full_metrics["profit_factor"] >= min_profit_factor and
            full_metrics["max_drawdown"] <= max_drawdown and
            full_metrics["num_trades"] >= min_trades
        )

        if passed_guardrails:
            output[symbol] = {"best_params": best, "metrics": full_metrics}
            logger.info(
                "Best params for %s (PASSED): %s\n"
                "Metrics (FULL PERIOD) | PF: %.2f | WinRate: %.2f%% | NetProfit: %.2f | MaxDD: %.2f%% | Trades: %.1f",
                symbol,
                best,
                full_metrics["profit_factor"],
                full_metrics["win_rate"] * 100,
                full_metrics["net_profit"],
                full_metrics["max_drawdown"] * 100,
                float(full_metrics["num_trades"]),
            )
        else:
            logger.warning(
                "Best params for %s (FAILED GUARDRAILS - SKIPPING): PF %.2f, WR %.2f%%, DD %.2f%%, Trades %.1f",
                symbol,
                full_metrics["profit_factor"],
                full_metrics["win_rate"] * 100,
                full_metrics["max_drawdown"] * 100,
                float(full_metrics["num_trades"])
            )

    # Load existing config to merge winners
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, "r") as f:
            try:
                final_output = json.load(f)
            except json.JSONDecodeError:
                final_output = {}
    else:
        final_output = {}

    # Update with new winners only (keep existing ones if they weren't in this run or didn't pass)
    for symbol, data in output.items():
        # Ensure standard fields are included for the bot
        params = data["best_params"]
        final_output[symbol] = {
            "enabled": True,
            "mode": "local_sim",
            "initial_capital": str(params.get("BACKTEST_INITIAL_CAPITAL", 2000.0)),
            "leverage": str(params.get("LEVERAGE", 1)),
            "capital_usage_percent": "100",
            "timeframe": params.get("OHLCV_TIMEFRAME", "15m"),
            "grid_enabled": True,
            "grid_spacing_pct": str(params.get("GRID_SPACING_PCT")),
            "grid_num_grids_up": int(params.get("NUM_GRIDS", 25)) // 2,
            "grid_num_grids_down": int(params.get("NUM_GRIDS", 25)) // 2,
            "grid_order_size_quote": str(params.get("ORDER_SIZE_QUOTE", 100.0)),
            "grid_recentre_trigger": int(params.get("RECENTRE_TRIGGER", 3)),
            "grid_adx_threshold": int(params.get("ADX_THRESHOLD", 30)),
            "grid_bb_width_threshold": str(params.get("bb_width_threshold", "0.04")),
            "grid_max_open_orders": int(params.get("MAX_OPEN_ORDERS", 100)),
            "cost_floor_multiplier": "2.0",
            "slippage_pct": "0.1"
        }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(final_output, f, indent=4)

    # Generate Summary Report
    summary_path = PROJECT_ROOT / "data" / "backtest_results" / "optimization_summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(summary_path, "w") as f:
        f.write("# Optimization Summary Report\n\n")
        f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"**Criteria:** PF >= {min_profit_factor}, WR >= {min_win_rate}, DD <= {max_drawdown}\n\n")
        f.write("| Symbol | PF | WinRate | Net Profit | MaxDD | Trades |\n")
        f.write("| :--- | :--- | :--- | :--- | :--- | :--- |\n")
        for symbol, data in output.items():
            m = data["metrics"]
            f.write(f"| {symbol} | {m['profit_factor']:.2f} | {m['win_rate']*100:.1f}% | {m['net_profit']:.2f} | {m['max_drawdown']*100:.2f}% | {m['num_trades']} |\n")

    logger.info("Optimization task complete. Summary report at %s", summary_path)


if __name__ == "__main__":
    # Disable loud backtest logs
    logging.getLogger("src.strategy.regime_detector").setLevel(logging.CRITICAL)
    logging.getLogger("src.strategy.grid_calculator").setLevel(logging.CRITICAL)
    logging.getLogger("src.backtest.grid_backtester").setLevel(logging.CRITICAL)

    asyncio.run(run_optimizer())
