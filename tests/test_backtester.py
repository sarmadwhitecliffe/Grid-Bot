"""
tests/test_backtester.py
-------------------------
Unit tests for src/backtest/grid_backtester.py and
src/backtest/backtest_report.py.

Tests verify:
  - The simulation completes without error on valid OHLCV input.
  - BacktestResult structure is correct.
  - BacktestReport metrics are within plausible ranges.
"""

import numpy as np
import pandas as pd
import pytest

from config.settings import GridBotSettings
from src.backtest.backtest_report import BacktestReport
from src.backtest.grid_backtester import BacktestResult, GridBacktester


@pytest.fixture
def backtester(base_settings: GridBotSettings) -> GridBacktester:
    return GridBacktester(
        settings=base_settings,
        initial_capital=2_000.0,
        indicator_warmup=30,
    )


@pytest.fixture
def ranging_ohlcv() -> pd.DataFrame:
    """
    Synthetic ~200-bar ranging OHLCV DataFrame where price oscillates
    within a narrow band, giving the grid plenty of fill opportunities.
    """
    rng = np.random.default_rng(seed=7)
    n = 200
    base = 30_000.0
    noise = rng.uniform(-150, 150, n).cumsum() * 0.05
    closes = base + noise
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "open": closes + rng.normal(0, 20, n),
            "high": closes + rng.uniform(50, 200, n),
            "low": closes - rng.uniform(50, 200, n),
            "close": closes,
            "volume": rng.uniform(200, 800, n),
        }
    )


class TestBacktesterRun:
    def test_returns_backtest_result(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        assert isinstance(result, BacktestResult)

    def test_equity_curve_has_values(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        assert len(result.equity_curve) > 0

    def test_initial_capital_set(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        assert result.initial_capital == 2_000.0

    def test_final_equity_positive(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        assert result.final_equity > 0

    def test_fees_non_negative(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        assert result.total_fees_usdt >= 0.0

    def test_no_crash_on_empty_after_warmup(
        self, backtester: GridBacktester
    ) -> None:
        """Warmup window larger than data length should produce empty result."""
        short_df = pd.DataFrame(
            {
                "timestamp": pd.date_range("2024-01-01", periods=25, freq="1h"),
                "open": [30_000.0] * 25,
                "high": [30_100.0] * 25,
                "low": [29_900.0] * 25,
                "close": [30_000.0] * 25,
                "volume": [500.0] * 25,
            }
        )
        result = backtester.run(short_df)
        assert isinstance(result, BacktestResult)


class TestBacktestReport:
    def test_win_rate_between_0_and_1(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        report = BacktestReport(result)
        wr = report.win_rate()
        assert 0.0 <= wr <= 1.0

    def test_max_drawdown_between_0_and_1(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        report = BacktestReport(result)
        dd = report.max_drawdown()
        assert 0.0 <= dd <= 1.0

    def test_profit_factor_positive(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        report = BacktestReport(result)
        pf = report.profit_factor()
        assert pf >= 0.0

    def test_summary_is_string(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        report = BacktestReport(result)
        summary = report.summary()
        assert isinstance(summary, str)
        assert "Backtest Report" in summary

    def test_passes_targets_returns_bool(
        self, backtester: GridBacktester, ranging_ohlcv: pd.DataFrame
    ) -> None:
        result = backtester.run(ranging_ohlcv)
        report = BacktestReport(result)
        passed = report.passes_targets()
        assert isinstance(passed, bool)
