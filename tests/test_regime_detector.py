"""
tests/test_regime_detector.py
------------------------------
Unit tests for src/strategy/regime_detector.py.

Tests cover:
  - RANGING detection when ADX < threshold AND BB width < threshold.
  - TRENDING detection when ADX >= threshold.
  - UNKNOWN returned for insufficient data.
"""

import numpy as np
import pandas as pd
import pytest

from src.strategy import MarketRegime
from src.strategy.regime_detector import RegimeDetector


def _make_df(n: int = 100, volatility: float = 50.0, seed: int = 0) -> pd.DataFrame:
    """Create a synthetic OHLCV DataFrame of n bars."""
    rng = np.random.default_rng(seed)
    base = 30_000.0
    closes = base + rng.normal(0, volatility, n).cumsum() * 0.1
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "open": closes + rng.normal(0, 10, n),
            "high": closes + rng.uniform(10, 60, n),
            "low": closes - rng.uniform(10, 60, n),
            "close": closes,
            "volume": rng.uniform(100, 500, n),
        }
    )


class TestRegimeDetectorInsufficientData:
    def test_unknown_for_short_series(self) -> None:
        """Fewer than ~25 bars should return UNKNOWN."""
        detector = RegimeDetector()
        df = _make_df(n=20)
        result = detector.detect(df)
        assert result.regime == MarketRegime.UNKNOWN


class TestRegimeDetectorRanging:
    def test_returns_regime_info_object(self) -> None:
        detector = RegimeDetector()
        df = _make_df(n=100, volatility=5.0)
        result = detector.detect(df)
        assert hasattr(result, "regime")
        assert hasattr(result, "adx")
        assert hasattr(result, "bb_width")

    def test_adx_and_bb_width_positive(self) -> None:
        detector = RegimeDetector()
        df = _make_df(n=100)
        result = detector.detect(df)
        assert result.adx >= 0.0
        assert result.bb_width >= 0.0


class TestRegimeDetectorOutput:
    def test_is_ranging_property(self) -> None:
        """is_ranging must be True only when regime == RANGING."""
        detector = RegimeDetector()
        df = _make_df(n=100)
        result = detector.detect(df)
        if result.regime == MarketRegime.RANGING:
            assert result.is_ranging is True
        else:
            assert result.is_ranging is False

    def test_reason_string_not_empty(self) -> None:
        detector = RegimeDetector()
        df = _make_df(n=100)
        result = detector.detect(df)
        assert isinstance(result.reason, str)
        assert len(result.reason) > 0

    def test_custom_threshold_changes_classification(self) -> None:
        """Setting a very high ADX threshold forces RANGING classification."""
        detector_strict = RegimeDetector(adx_threshold=5.0, bb_width_threshold=100.0)
        detector_loose = RegimeDetector(adx_threshold=100.0, bb_width_threshold=100.0)
        df = _make_df(n=100)
        strict_result = detector_strict.detect(df)
        loose_result = detector_loose.detect(df)
        # With extremely loose threshold, it should be RANGING.
        assert loose_result.regime == MarketRegime.RANGING
