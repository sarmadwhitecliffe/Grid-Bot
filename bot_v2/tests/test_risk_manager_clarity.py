from unittest.mock import patch

import pytest

from bot_v2.risk.adaptive_risk_manager import (
    SETTINGS,
    PerformanceMetrics,
    RiskTier,
    RiskTierClassifier,
)


# Helper to create metrics
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


@pytest.fixture
def mock_logger():
    with patch("bot_v2.risk.adaptive_risk_manager.logger") as mock:
        yield mock


def test_demotion_consecutive_losses_only(mock_logger):
    """TASK-004: Test demotion when only consecutive losses exceed max."""
    # Setup a tier with max consecutive losses = 3
    tier = RiskTier(
        name="TEST_TIER",
        min_trades=10,
        max_trades=None,
        profit_factor_min=1.0,
        profit_factor_max=None,
        sharpe_ratio_min=0.0,
        win_rate_min=0.0,
        max_drawdown_max=None,
        consecutive_losses_max=3,
        capital_allocation=0.5,
        min_leverage=1,
        max_leverage=5,
        max_position_size_usd=None,
        description="Test",
    )

    # Metrics that pass everything except consecutive losses
    metrics = make_metrics(
        profit_factor=1.5,
        max_consecutive_losses=4,  # Exceeds 3
        current_consecutive_losses=4,
    )

    # We need to inject this tier into ALL_TIERS for classify to work properly
    # Or we can just test _check_criteria_detailed directly for the failure reason
    passed, failure = RiskTierClassifier._check_criteria_detailed(metrics, tier)

    assert not passed
    assert failure["criterion"] == "consecutive_losses_max"
    assert failure["value"] == 4
    assert failure["threshold"] == 3
    assert "Max Consec Losses 4 > 3" in failure["msg"]


def test_buffer_demotion_pf(mock_logger):
    """TASK-005: Test buffer demotion when PF < (threshold - buffer)."""
    # Setup settings for demotion buffer
    original_demote_buffer = SETTINGS.get("demotion_rules", {}).get(
        "demote_buffer_pf", 0.0
    )
    SETTINGS["demotion_rules"] = {"demote_buffer_pf": 0.1}

    try:
        # Current tier requires PF 1.5
        current_tier = RiskTier(
            name="HIGH_TIER",
            min_trades=10,
            max_trades=None,
            profit_factor_min=1.5,
            profit_factor_max=None,
            sharpe_ratio_min=0.0,
            win_rate_min=0.0,
            max_drawdown_max=None,
            consecutive_losses_max=None,
            capital_allocation=0.5,
            min_leverage=1,
            max_leverage=5,
            max_position_size_usd=None,
            description="High",
        )

        # Lower tier
        lower_tier = RiskTier(
            name="LOW_TIER",
            min_trades=10,
            max_trades=None,
            profit_factor_min=1.0,
            profit_factor_max=None,
            sharpe_ratio_min=0.0,
            win_rate_min=0.0,
            max_drawdown_max=None,
            consecutive_losses_max=None,
            capital_allocation=0.2,
            min_leverage=1,
            max_leverage=5,
            max_position_size_usd=None,
            description="Low",
        )

        # Mock ALL_TIERS
        with patch(
            "bot_v2.risk.adaptive_risk_manager.ALL_TIERS", [current_tier, lower_tier]
        ):
            # Metrics with PF 1.35 (below 1.5 - 0.1 = 1.4)
            metrics = make_metrics(profit_factor=1.35)

            tier_history = {
                "current_tier": "HIGH_TIER",
                "trades_in_tier": 100,
                "consecutive_losses_in_tier": 0,
            }

            result_tier = RiskTierClassifier.classify(metrics, tier_history)

            # Should demote to LOW_TIER
            assert result_tier.name == "LOW_TIER"

            # Verify logs
            # We expect a log about buffer demotion
            # "Buffer demotion HIGH_TIER → LOW_TIER (PF 1.35 < 1.40, ...)"
            found_log = False
            for call in mock_logger.info.call_args_list:
                msg = call[0][0]
                if "Buffer demotion HIGH_TIER → LOW_TIER" in msg:
                    found_log = True
                    assert "PF 1.35 <" in msg
                    assert "current_consecutive_losses_lookback=" in msg
                    break
            assert found_log, "Did not find buffer demotion log message"

    finally:
        # Restore settings
        SETTINGS["demotion_rules"]["demote_buffer_pf"] = original_demote_buffer


def test_pf_stability_gating_logging(mock_logger):
    """TASK-003/VAL-003: Test PF stability gating logging."""
    original_pf_min_trades = SETTINGS.get("pf_min_trades_for_validation")
    SETTINGS["pf_min_trades_for_validation"] = 50

    try:
        # Tier requiring PF stability check (PF min >= 1.2)
        high_tier = RiskTier(
            name="HIGH_TIER",
            min_trades=10,
            max_trades=None,
            profit_factor_min=1.5,
            profit_factor_max=None,
            sharpe_ratio_min=0.0,
            win_rate_min=0.0,
            max_drawdown_max=None,
            consecutive_losses_max=None,
            capital_allocation=0.5,
            min_leverage=1,
            max_leverage=5,
            max_position_size_usd=None,
            description="High",
        )

        # Metrics with high PF but low trades
        metrics = make_metrics(
            profit_factor=2.0,
            lookback_trades=20,
            total_trades=20,  # < 50 required
        )

        passed, failure = RiskTierClassifier._check_criteria_detailed(
            metrics, high_tier
        )

        assert not passed
        assert failure["criterion"] == "pf_stability"
        assert failure["pf_stability_gated"] is True
        assert failure["value"] == 20
        assert failure["threshold"] == 50

        # Now test classify logging
        with patch("bot_v2.risk.adaptive_risk_manager.ALL_TIERS", [high_tier]):
            RiskTierClassifier.classify(metrics)

            # Should see debug log about gating
            found_log = False
            for call in mock_logger.debug.call_args_list:
                msg = call[0][0]
                if "gated by PF stability" in msg:
                    found_log = True
                    assert "required 50 trades" in msg
                    assert "has 20" in msg
                    break
            assert found_log, "Did not find PF stability gating log"

    finally:
        SETTINGS["pf_min_trades_for_validation"] = original_pf_min_trades
