from pathlib import Path

import pytest

from bot_v2.risk.adaptive_risk_manager import (
    PerformanceMetrics,
    RiskTier,
    RiskTierClassifier,
    PortfolioRiskMonitor,
    SETTINGS,
)


def make_metrics(**overrides):
    base = dict(
        symbol="TEST",
        total_trades=20,
        lookback_trades=20,
        profit_factor=1.3,
        sharpe_ratio=1.0,
        win_rate=0.5,
        max_drawdown=0.10,
        avg_win=100.0,
        avg_loss=50.0,
        avg_win_r=1.0,
        avg_r_multiple=0.5,
        expectancy_r=0.1,
        max_consecutive_losses=2,
        current_consecutive_losses=0,
        std_dev_returns=1.0,
        current_equity=1200.0,
        peak_equity=1200.0,
        last_calculated="2025-11-07T00:00:00Z",
        first_trade_date="2025-10-01T00:00:00Z",
        current_drawdown_pct=0.0,
        recovery_factor=1.0,
    )
    base.update(overrides)
    return PerformanceMetrics(**base)


def test_profit_factor_max_exclusive_boundary_meets_criteria():
    # STANDARD-like tier: PF in [1.2, 1.49)
    tier = RiskTier(
        name="STANDARD",
        min_trades=15,
        max_trades=None,
        profit_factor_min=1.2,
        profit_factor_max=1.49,
        sharpe_ratio_min=0.5,
        win_rate_min=0.40,
        max_drawdown_max=0.25,
        consecutive_losses_max=None,
        level_allocation_ratio=0.80,
        leverage_multiplier=1.0,
        max_leverage_cap=20,
        description="Test tier",
    )

    # Exactly at max must fail (max-exclusive)
    m_eq_max = make_metrics(
        total_trades=20,
        profit_factor=1.49,
        sharpe_ratio=0.6,
        win_rate=0.5,
        max_drawdown=0.10,
    )
    assert not RiskTierClassifier._meets_criteria(m_eq_max, tier)

    # Just below max must pass
    m_below = make_metrics(
        total_trades=20,
        profit_factor=1.489,
        sharpe_ratio=0.6,
        win_rate=0.5,
        max_drawdown=0.10,
    )
    assert RiskTierClassifier._meets_criteria(m_below, tier)


def test_max_trades_exclusive_boundary():
    # PROBATION-like tier with max_trades=10 uses max-exclusive rule
    tier = RiskTier(
        name="PROBATION",
        min_trades=0,
        max_trades=10,
        profit_factor_min=0.0,
        profit_factor_max=None,
        sharpe_ratio_min=-999,
        win_rate_min=None,
        max_drawdown_max=None,
        consecutive_losses_max=None,
        level_allocation_ratio=0.50,
        leverage_multiplier=0.5,
        max_leverage_cap=10,
        description="Test probation",
    )

    m_equal = make_metrics(total_trades=10, profit_factor=0.9)
    assert not RiskTierClassifier._meets_criteria(m_equal, tier)

    m_below = make_metrics(total_trades=9, profit_factor=0.9)
    assert RiskTierClassifier._meets_criteria(m_below, tier)


def test_tier_portfolio_caps_enforced(tmp_path: Path):
    # Use the proposed config to load tier caps (CHAMPION: 40%)
    pytest.skip(
        "Skipping: config/adaptive_risk_tiers.proposed.json not available in workspace."
    )


def test_portfolio_heat_dynamic_denominator():
    # Setup test configuration
    SETTINGS["max_portfolio_heat_pct"] = 8.0  # 8% max heat

    # Test with hardcoded fallback (if capital_manager is not present, logic handles it in caller)
    # The check_portfolio_heat takes active_positions, proposed_risk, and total_capital.
    # We will just verify check_portfolio_heat itself here.

    # 10 active positions each with $1000 capital, 1% risk = $10 risk per position = $100 total current risk
    active_positions = [
        {"initial_amount": 0.01, "initial_risk_atr": 1000.0}  # $10 risk each
        for _ in range(10)
    ]

    # Total capital = $10,000. Max heat = 8% = $800.
    # Proposed risk = $50.
    # Total risk = $100 + $50 = $150.
    # Heat ratio = 150 / 10000 = 1.5% <= 8%. Should be optimal.
    allowed, heat_mult, heat_reason = PortfolioRiskMonitor.check_portfolio_heat(
        active_positions, 50.0, 10000.0
    )
    assert allowed is True
    assert heat_mult == 1.0
    assert "optimal" in heat_reason.lower()

    # Now let's push it near the cap. Current risk = $100.
    # Proposed risk = $650. Total risk = $750.
    # Heat ratio = 750 / 10000 = 7.5%.
    # Soft band is 75% of max heat (0.08 * 0.75 = 0.06).
    # Since 7.5% > 6.0%, it should scale down.
    allowed, heat_mult, heat_reason = PortfolioRiskMonitor.check_portfolio_heat(
        active_positions, 650.0, 10000.0
    )
    assert allowed is True
    assert heat_mult < 1.0  # Should be scaled
    assert "elevated" in heat_reason.lower()

    # Push it over the cap. Current risk = $100.
    # Proposed risk = $800. Total risk = $900.
    # Heat ratio = 900 / 10000 = 9%. (Over 8%)
    allowed, heat_mult, heat_reason = PortfolioRiskMonitor.check_portfolio_heat(
        active_positions, 800.0, 10000.0
    )
    assert allowed is True
    assert heat_mult < 1.0
    assert "high" in heat_reason.lower()
