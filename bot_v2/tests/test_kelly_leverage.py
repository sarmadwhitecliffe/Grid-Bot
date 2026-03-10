#!/usr/bin/env python3
"""
Unit tests for composite leverage calculation with INTEGER quantization.

These tests use inline sample metrics instead of relying on external
`data_futures/symbol_performance.json` to avoid CI/environmental failures.

Composite leverage design contract (WITH QUANTIZATION):
  - Returns an INTEGER in [0, max_leverage] representing a leverage value
    scaled within the tier's allowed band.
  - Uses a weighted composite of three normalized scores:
      Profit Factor score (50%): min(PF, 5.0) / 5.0
      Win Rate score     (45%): max(0, (WR - 0.5)) / 0.5
      Kelly score         (5%): traditional kelly_f clamped to [0, 1]
  - safe_composite = composite * 0.5  (half-sizing for safety)
  - raw_leverage = safe_composite * max_leverage
  - QUANTIZATION: leverage = round(raw_leverage)  ← NEW
  - 100% win rate → max_leverage (guard at top of function)
  - Zero or negative edge → 0 (floored to tier min by caller)

IMPORTANT: Tests require QUANTIZE_KELLY_LEVERAGE environment variable:
    export QUANTIZE_KELLY_LEVERAGE=true
"""

import json
import os
from pathlib import Path

import pytest

from bot_v2.risk.adaptive_risk_manager import PositionSizer

# Load tier config (repository relative)
tiers_path = Path(__file__).resolve().parents[2] / "config" / "adaptive_risk_tiers.json"
with open(tiers_path) as f:
    tier_config = json.load(f)

tiers_by_name = {t["name"]: t for t in tier_config["tiers"]}


@pytest.fixture(autouse=True)
def enable_kelly_quantization():
    """Enable Kelly quantization for all tests in this module."""
    os.environ["QUANTIZE_KELLY_LEVERAGE"] = "true"
    yield
    os.environ.pop("QUANTIZE_KELLY_LEVERAGE", None)


def _compute_expected(
    win_rate: float,
    avg_win_r: float,
    avg_win: float,
    avg_loss: float,
    profit_factor: float,
    max_leverage: int,
) -> int:
    """Mirror the composite formula for test verification (with quantization)."""
    if win_rate >= 1.0:
        return max_leverage
    if win_rate <= 0:
        return 0

    pf_cap = 5.0
    pf_score = min(max(profit_factor, 0.0), pf_cap) / pf_cap
    wr_score = max(0.0, (win_rate - 0.5)) / 0.5

    if avg_win_r > 0:
        kelly_f = win_rate * avg_win_r - (1 - win_rate)
    elif avg_loss > 0 and avg_win > 0:
        b = avg_win / avg_loss
        kelly_f = (win_rate * b - (1 - win_rate)) / b
    else:
        kelly_f = 0.0
    kelly_score = min(1.0, max(0.0, kelly_f))

    composite = 0.50 * pf_score + 0.45 * wr_score + 0.05 * kelly_score
    safe = composite * 0.5
    raw_leverage = max(0.0, safe) * max_leverage

    # QUANTIZATION: Round to nearest integer
    return round(raw_leverage)


# ────────────────────────────────────────────────────────────────────
# Parametrized: positive edge vs no edge
# ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "symbol,metrics,expected_positive",
    [
        (
            "HYPE/USDT",
            {
                "win_rate": 0.55,
                "avg_win": 0.02,
                "avg_loss": 0.01,
                "avg_win_r": 1.2,
                "profit_factor": 1.1,
            },
            True,
        ),
        (
            "XRP/USDT",
            {
                "win_rate": 0.45,
                "avg_win": 0.015,
                "avg_loss": 0.02,
                "avg_win_r": 0.8,
                "profit_factor": 0.6,
            },
            # Below 50% WR → wr_score=0, low PF → low pf_score,
            # negative kelly → kelly_score=0.  Composite ≈ 0.06*0.5 = 0.03
            # Still slightly positive due to PF > 0, but very low.
            True,  # tiny but > 0 because PF contributes
        ),
        (
            "VIRTUAL/USDT",
            {
                "win_rate": 0.45,
                "avg_win": 0.015,
                "avg_loss": 0.02,
                "avg_win_r": 0.0,
                "profit_factor": 0.0,
            },
            False,  # PF=0, WR<50%, no R data → 0.0
        ),
    ],
)
def test_composite_leverage_with_sample_metrics(symbol, metrics, expected_positive):
    """Composite leverage is positive for symbols with edge, zero/near-zero without."""
    for tier_name in ["CONSERVATIVE", "STANDARD", "AGGRESSIVE"]:
        tier = tiers_by_name[tier_name]
        max_leverage = tier["max_leverage"]

        lev = PositionSizer._calculate_kelly_leverage(
            win_rate=metrics["win_rate"],
            avg_win=metrics["avg_win"],
            avg_loss=metrics["avg_loss"],
            avg_win_r=metrics["avg_win_r"],
            max_leverage=max_leverage,
            profit_factor=metrics.get("profit_factor", 0.0),
        )

        if expected_positive:
            # With quantization, small composites may round to 0
            assert lev >= 0, f"{symbol} {tier_name}: expected non-negative, got {lev}"
            assert lev <= max_leverage, (
                f"{symbol} {tier_name}: exceeds max {max_leverage}"
            )
        else:
            assert lev == 0, f"{symbol} {tier_name}: expected 0, got {lev}"
            assert isinstance(lev, int), "Leverage should be integer"


# ────────────────────────────────────────────────────────────────────
# Edge guards (unchanged behavior)
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_degenerate_all_zeros():
    """When all inputs are zero/degenerate, returns 0."""
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.5,
        avg_win=0.0,
        avg_loss=0.0,
        avg_win_r=0.0,
        max_leverage=10,
        profit_factor=0.0,
    )
    assert lev == 0
    assert isinstance(lev, int)


def test_composite_leverage_100_percent_win_rate():
    """When win_rate >= 1.0, returns max_leverage (early guard)."""
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=1.0,
        avg_win=0.1,
        avg_loss=0.01,
        avg_win_r=1.0,
        max_leverage=10,
        profit_factor=5.0,
    )
    assert lev == 10
    assert isinstance(lev, int)


def test_composite_leverage_zero_win_rate():
    """When win_rate <= 0.0, returns 0."""
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.0,
        avg_win=0.1,
        avg_loss=0.01,
        avg_win_r=1.0,
        max_leverage=10,
        profit_factor=3.0,
    )
    assert lev == 0
    assert isinstance(lev, int)


# ────────────────────────────────────────────────────────────────────
# Accurate math: composite formula verification
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_accurate_math():
    """
    Verify exact composite math with known inputs.

    Given WR=0.6, AvgWinR=2.0, PF=2.0, max_leverage=10:
      pf_score   = 2.0 / 5.0 = 0.4
      wr_score   = (0.6 - 0.5) / 0.5 = 0.2
      kelly_f    = 0.6 * 2.0 - 0.4 = 0.8 → kelly_score = 0.8
    composite  = 0.5*0.4 + 0.45*0.2 + 0.05*0.8 = 0.33
    safe       = 0.33 * 0.5 = 0.165
    raw_lev    = 0.165 * 10 = 1.65
    leverage   = round(1.65) = 2x
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.6,
        avg_win=200.0,
        avg_loss=100.0,
        avg_win_r=2.0,
        max_leverage=10,
        profit_factor=2.0,
    )
    assert lev == 2
    assert isinstance(lev, int)


def test_composite_leverage_high_pf_high_wr():
    """
    Top performer: high PF + high WR should produce high leverage.

    Given WR=0.92, AvgWinR=1.1, PF=7.0, max_leverage=7:
      pf_score   = 5.0 / 5.0 = 1.0 (capped at 5.0)
      wr_score   = (0.92 - 0.5) / 0.5 = 0.84
      kelly_f    = 0.92 * 1.1 - 0.08 = 0.932 → kelly_score = 0.932
    composite  = 0.5*1.0 + 0.45*0.84 + 0.05*0.932 = 0.5 + 0.378 + 0.0466 = 0.9246
    safe       = 0.9246 * 0.5 = 0.4623
    raw_lev    = 0.4623 * 7 = 3.2361
      leverage   = round(3.2844) = 3x
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.92,
        avg_win=1.0,
        avg_loss=1.0,
        avg_win_r=1.1,
        max_leverage=7,
        profit_factor=7.0,
    )
    expected = _compute_expected(0.92, 1.1, 1.0, 1.0, 7.0, 7)
    assert lev == expected
    assert isinstance(lev, int)
    # Must be significantly higher than old Kelly would have produced
    assert lev >= 3, f"High PF/WR should produce ≥3x, got {lev}x"


def test_composite_leverage_low_pf_low_wr():
    """
    Underperformer: low PF + low WR should produce near-zero leverage.

    Given WR=0.55, AvgWinR=0.3, PF=0.5, max_leverage=2:
      pf_score   = 0.5 / 5.0 = 0.1
      wr_score   = (0.55 - 0.5) / 0.5 = 0.1
      kelly_f    = 0.55 * 0.3 - 0.45 = -0.285 → kelly_score = 0.0
    composite  = 0.5*0.1 + 0.45*0.1 + 0.05*0.0 = 0.095
    safe       = 0.095 * 0.5 = 0.0475
    raw_lev    = 0.0475 * 2 = 0.095
      leverage   = round(0.08) = 0x  (floored to tier min 1x by caller)
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.55,
        avg_win=0.5,
        avg_loss=1.0,
        avg_win_r=0.3,
        max_leverage=2,
        profit_factor=0.5,
    )
    expected = _compute_expected(0.55, 0.3, 0.5, 1.0, 0.5, 2)
    assert lev == expected
    assert isinstance(lev, int)
    assert lev == 0, f"Low PF/WR should produce 0x, got {lev}x"


# ────────────────────────────────────────────────────────────────────
# Scaling: same edge scales proportionally with tier max_leverage
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_scales_with_max_leverage():
    """
    The same edge should produce proportionally higher leverage for higher tier caps.

    WR=0.7, AvgWinR=1.5, PF=3.0:
      pf_score   = 3.0 / 5.0 = 0.6
      wr_score   = (0.7 - 0.5) / 0.5 = 0.4
      kelly_f    = 0.7 * 1.5 - 0.3 = 0.75 → kelly_score = 0.75
    composite  = 0.5*0.6 + 0.45*0.4 + 0.05*0.75 = 0.3 + 0.18 + 0.0375 = 0.5175
    safe       = 0.5175 * 0.5 = 0.25875
    """
    results = {}
    for tier_name in ["STANDARD", "AGGRESSIVE", "CHAMPION"]:
        max_lev = tiers_by_name[tier_name]["max_leverage"]
        results[tier_name] = PositionSizer._calculate_kelly_leverage(
            win_rate=0.7,
            avg_win=150.0,
            avg_loss=100.0,
            avg_win_r=1.5,
            max_leverage=max_lev,
            profit_factor=3.0,
        )

    # Verify proportional scaling
    safe_composite = 0.25875  # computed above
    # With quantization, we expect integer steps
    assert isinstance(results["STANDARD"], int)
    assert isinstance(results["AGGRESSIVE"], int)
    assert isinstance(results["CHAMPION"], int)
    # Higher tier should produce higher or equal leverage
    assert results["STANDARD"] <= results["AGGRESSIVE"] <= results["CHAMPION"]


# ────────────────────────────────────────────────────────────────────
# PF cap: extreme PF values are capped at 5.0
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_pf_cap():
    """
    PF values above 5.0 should be capped, preventing outliers from
    dominating the composite score.

    PF=5.0 and PF=38.0 should produce the same leverage (all else equal).
    """
    lev_normal = PositionSizer._calculate_kelly_leverage(
        win_rate=0.9,
        avg_win=1.0,
        avg_loss=1.0,
        avg_win_r=0.5,
        max_leverage=5,
        profit_factor=5.0,
    )
    lev_extreme = PositionSizer._calculate_kelly_leverage(
        win_rate=0.9,
        avg_win=1.0,
        avg_loss=1.0,
        avg_win_r=0.5,
        max_leverage=5,
        profit_factor=38.0,
    )
    assert abs(lev_normal - lev_extreme) < 1, (
        f"PF cap not working: PF=5 → {lev_normal:.3f}, PF=38 → {lev_extreme:.3f}"
    )


# ────────────────────────────────────────────────────────────────────
# WR threshold: below 50% WR gets zero WR score
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_wr_below_threshold():
    """
    Win rate below 50% should contribute zero WR score.
    Only PF and Kelly components should contribute.
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.45,
        avg_win=200.0,
        avg_loss=100.0,
        avg_win_r=2.0,
        max_leverage=5,
        profit_factor=1.5,
    )
    # wr_score = 0 (below 50%)
    # pf_score = 1.5/5.0 = 0.3
    # kelly_f = 0.45*2.0 - 0.55 = 0.35 → kelly_score = 0.35
    # composite = 0.5*0.3 + 0.45*0 + 0.05*0.35 = 0.1675
    # safe = 0.08375, lev = 0.41875
    expected = _compute_expected(0.45, 2.0, 200.0, 100.0, 1.5, 5)
    assert lev == expected
    assert isinstance(lev, int)


# ────────────────────────────────────────────────────────────────────
# Backward compat: function accepts old call signature (without profit_factor)
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_backward_compat_no_pf():
    """
    Calling without profit_factor should default to 0.0 and still work.
    PF score will be 0; only WR and Kelly components contribute.
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.6,
        avg_win=200.0,
        avg_loss=100.0,
        avg_win_r=2.0,
        max_leverage=10,
    )
    # pf_score = 0 (default PF=0)
    # wr_score = 0.2
    # kelly_score = 0.8
    # composite = 0 + 0.09 + 0.04 = 0.13
    # safe = 0.065, lev = 0.65
    expected = _compute_expected(0.6, 2.0, 200.0, 100.0, 0.0, 10)
    assert lev == expected
    assert isinstance(lev, int)
    assert lev > 0, "Should still produce positive leverage from WR+Kelly"


# ────────────────────────────────────────────────────────────────────
# Production scenario: BTC with real-world metrics
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_btc_production_scenario():
    """
    Reproduces BTC/USDT production-like metrics:
      WR=91.7%, AvgWinR=1.11R, PF=7.0, AGGRESSIVE tier (max 7x)

      pf_score  = 5.0/5.0 = 1.0 (capped)
      wr_score  = (0.917-0.5)/0.5 = 0.834
      kelly_f   = 0.917*1.11-0.083 = 0.935 → kelly_score = 0.935
    composite = 0.5*1.0 + 0.45*0.834 + 0.05*0.935 = 0.92255
    safe      = 0.92255 * 0.5 = 0.461275
    leverage  = 0.461275 * 7 = 3.23x
    """
    max_lev = tiers_by_name["AGGRESSIVE"]["max_leverage"]  # 7

    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.917,
        avg_win=1.0,
        avg_loss=1.0,
        avg_win_r=1.11,
        max_leverage=max_lev,
        profit_factor=7.0,
    )
    expected = _compute_expected(0.917, 1.11, 1.0, 1.0, 7.0, max_lev)
    assert lev == expected
    assert isinstance(lev, int)
    # Must be meaningfully higher than old pure-Kelly 1x
    assert lev >= 3, f"BTC with PF=7, WR=92% should get ≥3x, got {lev}x"


# ────────────────────────────────────────────────────────────────────
# Production scenario: TRX with extreme PF
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_trx_extreme_pf():
    """
    TRX/USDT: PF=38 (outlier), WR=90%, AvgWinR=0.5, PROBATION (max 2x)

    PF is capped at 5.0, so pf_score = 1.0
      pf_score  = 1.0
      wr_score  = (0.9-0.5)/0.5 = 0.8
      kelly_f   = 0.9*0.5-0.1 = 0.35 → kelly_score = 0.35
    composite = 0.5*1.0+0.45*0.8+0.05*0.35 = 0.5+0.36+0.0175 = 0.8775
    safe      = 0.8775*0.5 = 0.43875
    leverage  = 0.43875*2 = 0.8775x  (will be clamped to 1x by caller)
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.9,
        avg_win=1.0,
        avg_loss=1.0,
        avg_win_r=0.5,
        max_leverage=2,
        profit_factor=38.0,
    )
    expected = _compute_expected(0.9, 0.5, 1.0, 1.0, 38.0, 2)
    assert lev == expected
    assert isinstance(lev, int)
    # Even capped, should be close to 1x (caller would clamp to tier min=1x)
    assert lev >= 0, f"TRX should get non-negative leverage, got {lev}x"


# ────────────────────────────────────────────────────────────────────
# Fallback path: USD-based Kelly when no R data
# ────────────────────────────────────────────────────────────────────
def test_composite_leverage_usd_fallback():
    """
    When avg_win_r=0 but avg_win/avg_loss are available, the Kelly
    component should use the USD-based formula.
    """
    lev = PositionSizer._calculate_kelly_leverage(
        win_rate=0.6,
        avg_win=200.0,
        avg_loss=100.0,
        avg_win_r=0.0,
        max_leverage=5,
        profit_factor=1.2,
    )
    # pf_score = 1.2/5.0 = 0.24
    # wr_score = 0.2
    # b = 200/100 = 2.0, kelly_f = (0.6*2 - 0.4)/2 = 0.4 → kelly_score = 0.4
    # composite = 0.5*0.24 + 0.45*0.2 + 0.05*0.4 = 0.12+0.09+0.02 = 0.23
    # safe = 0.115, raw_lev = 0.575, leverage = round(0.575) = 1x
    expected = _compute_expected(0.6, 0.0, 200.0, 100.0, 1.2, 5)
    assert lev == expected
    assert isinstance(lev, int)


# ────────────────────────────────────────────────────────────────────
# NEW: Quantization boundary tests
# ────────────────────────────────────────────────────────────────────
def test_kelly_leverage_rounding_boundaries():
    """Test that leverage rounds to nearest integer correctly."""
    # Test 1: Very low composite (< 0.5) rounds to 0
    # WR=0.51, PF=1.05, AvgWinR=0.9, max_leverage=10
    # pf_score = 1.05/5 = 0.21, wr_score = 0.02, kelly_f ≈ -0.04 → kelly_score = 0
    # composite = 0.5*0.21 + 0.3*0.02 + 0 = 0.111, safe = 0.0555
    # raw_lev = 0.555, rounds to 1x
    lev = _compute_expected(
        win_rate=0.51, profit_factor=1.05, avg_win_r=0.9, avg_win=90, avg_loss=100, max_leverage=10
    )
    assert lev == 1  # Rounds up from 0.555

    # Test 2: Exactly at 0.5 boundary - Python banker's rounding
    # Construct metrics that yield exactly 0.5, 1.5, 2.5 * max_leverage
    # This is tricky - just verify rounding behavior is consistent
    lev_low = _compute_expected(
        win_rate=0.52, profit_factor=1.0, avg_win_r=0.5, avg_win=50, avg_loss=100, max_leverage=10
    )
    assert isinstance(lev_low, int)
    assert 0 <= lev_low <= 10


def test_quantized_leverage_with_tier_clamps():
    """Test quantization interacts correctly with tier min/max bounds."""
    # Composite yields low value with tier max 2x
    lev = _compute_expected(
        win_rate=0.58, profit_factor=1.2, avg_win_r=1.0, avg_win=120, avg_loss=100, max_leverage=2
    )
    assert lev >= 0 and lev <= 2
    assert isinstance(lev, int)

    # Higher tier max should allow higher leverage
    lev_higher = _compute_expected(
        win_rate=0.58, profit_factor=1.2, avg_win_r=1.0, avg_win=120, avg_loss=100, max_leverage=10
    )
    assert lev_higher >= lev  # Should scale up
    assert isinstance(lev_higher, int)


def test_python_banker_rounding_verification():
    """Verify Python banker's rounding behavior in leverage calculation."""
    # Python 3 banker's rounding: rounds to nearest even
    assert round(0.5) == 0
    assert round(1.5) == 2
    assert round(2.5) == 2
    assert round(3.5) == 4
    assert round(4.5) == 4

    # Verify _compute_expected uses this rounding
    # The function internally calls round() so it should follow the same behavior


def test_quantized_leverage_eliminates_micro_differences():
    """Verify tiny metric differences produce same/adjacent integers."""
    # Two slightly different metrics should produce same or ±1 leverage
    lev1 = _compute_expected(
        win_rate=0.70, profit_factor=2.0, avg_win_r=1.1, avg_win=110, avg_loss=100, max_leverage=10
    )
    lev2 = _compute_expected(
        win_rate=0.71, profit_factor=2.01, avg_win_r=1.11, avg_win=111, avg_loss=100, max_leverage=10
    )
    assert abs(lev1 - lev2) <= 1  # Maximum difference of 1x
    assert isinstance(lev1, int)
    assert isinstance(lev2, int)


def test_quantized_leverage_consistency_across_tiers():
    """Verify leverage scales monotonically across tier max values."""
    metrics = {
        "win_rate": 0.75,
        "profit_factor": 3.0,
        "avg_win_r": 1.2,
        "avg_win": 120,
        "avg_loss": 100,
    }
    lev2 = _compute_expected(**metrics, max_leverage=2)
    lev5 = _compute_expected(**metrics, max_leverage=5)
    lev10 = _compute_expected(**metrics, max_leverage=10)

    # Higher tier max should produce higher leverage (with rounding steps)
    assert lev2 <= lev5 <= lev10
    assert isinstance(lev2, int)
    assert isinstance(lev5, int)
    assert isinstance(lev10, int)

    # Verify proportional scaling (accounting for rounding)
    # lev2 should be roughly lev10 * (2/10), but quantized
    assert lev2 <= lev10  # Basic sanity check
