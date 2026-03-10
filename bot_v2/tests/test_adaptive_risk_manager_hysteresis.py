"""
Unit tests for adaptive risk tier hysteresis behavior.

Tests verify that promotion/demotion rules prevent tier oscillation
at boundaries through configurable buffers and minimum stay requirements.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bot_v2.risk.adaptive_risk_manager import (
    PerformanceMetrics,
    RiskTierClassifier,
    load_risk_tiers_from_config,
)


def make_metrics(**overrides):
    """Helper to create PerformanceMetrics with required fields."""
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
def temp_config_with_hysteresis():
    """Create a temporary config file with hysteresis rules."""
    config = {
        "description": "Test config with hysteresis",
        "version": "1.0-test",
        "settings": {
            "boundary_policy": "min_inclusive_max_exclusive",
            "tier_eval_order": [
                "CHAMPION",
                "AGGRESSIVE",
                "STANDARD",
                "CONSERVATIVE",
                "PROBATION",
            ],
            "lookback_trades": 30,
            "pf_min_trades_for_validation": 15,
            "promotion_rules": {
                "promote_after_trades": 5,
                "promote_buffer_pf": 0.05,
                "min_stay_trades": 10,
            },
            "demotion_rules": {"demote_after_losses": 3, "demote_buffer_pf": 0.05},
        },
        "tiers": [
            {
                "name": "PROBATION",
                "description": "Test probation tier",
                "min_trades": 0,
                "max_trades": 10,
                "min_profit_factor": 0.0,
                "max_profit_factor": None,
                "min_sharpe_ratio": None,
                "min_win_rate": None,
                "max_drawdown": None,
                "max_consecutive_losses": None,
                "capital_allocation_pct": 25.0,
                "max_leverage": 2,
                "max_position_size_usd": 500,
            },
            {
                "name": "CONSERVATIVE",
                "description": "Test conservative tier",
                "min_trades": 11,
                "max_trades": None,
                "min_profit_factor": 1.0,
                "max_profit_factor": 1.2,
                "min_sharpe_ratio": 0.0,
                "min_win_rate": 0.45,
                "max_drawdown": None,
                "max_consecutive_losses": 4,
                "capital_allocation_pct": 35.0,
                "max_leverage": 3,
                "max_position_size_usd": 1000,
            },
            {
                "name": "STANDARD",
                "description": "Test standard tier",
                "min_trades": 15,
                "max_trades": None,
                "min_profit_factor": 1.2,
                "max_profit_factor": 1.5,
                "min_sharpe_ratio": 0.3,
                "min_win_rate": 0.50,
                "max_drawdown": 0.15,
                "max_consecutive_losses": 4,
                "capital_allocation_pct": 50.0,
                "max_leverage": 5,
                "max_position_size_usd": 3000,
            },
            {
                "name": "AGGRESSIVE",
                "description": "Test aggressive tier",
                "min_trades": 20,
                "max_trades": None,
                "min_profit_factor": 1.5,
                "max_profit_factor": 2.0,
                "min_sharpe_ratio": 0.5,
                "min_win_rate": 0.55,
                "max_drawdown": 0.12,
                "max_consecutive_losses": 5,
                "capital_allocation_pct": 60.0,
                "max_leverage": 7,
                "max_position_size_usd": 5000,
            },
            {
                "name": "CHAMPION",
                "description": "Test champion tier",
                "min_trades": 25,
                "max_trades": None,
                "min_profit_factor": 2.0,
                "max_profit_factor": None,
                "min_sharpe_ratio": 0.8,
                "min_win_rate": 0.60,
                "max_drawdown": 0.10,
                "max_consecutive_losses": 3,
                "capital_allocation_pct": 70.0,
                "max_leverage": 10,
                "max_position_size_usd": 8000,
            },
        ],
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(config, f, indent=2)
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    temp_path.unlink()


@pytest.fixture
def classifier(temp_config_with_hysteresis):
    """Load config with hysteresis rules."""
    # Import here to access the module-level ALL_TIERS
    import bot_v2.risk.adaptive_risk_manager as arm

    # Load config to populate SETTINGS and update ALL_TIERS
    tiers = load_risk_tiers_from_config(str(temp_config_with_hysteresis))
    arm.ALL_TIERS = tiers  # Update module-level global

    # RiskTierClassifier is static, just return the class
    return RiskTierClassifier


def test_promotion_blocked_by_min_stay_trades(classifier):
    """Test that promotion is blocked if symbol hasn't stayed in current tier long enough."""
    # Symbol qualifies for STANDARD (PF=1.25 > 1.2, meets all criteria)
    metrics = make_metrics(
        total_trades=20,
        win_rate=0.52,
        profit_factor=1.25,
        sharpe_ratio=0.35,
        max_drawdown=0.12,
        current_consecutive_losses=1,
    )

    # But only 5 trades in CONSERVATIVE (min_stay_trades=10)
    tier_history = {
        "current_tier": "CONSERVATIVE",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 5,
        "consecutive_losses_in_tier": 0,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "PROBATION",
        "last_total_trades": 15,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in CONSERVATIVE due to min_stay_trades
    assert tier.name == "CONSERVATIVE"


def test_promotion_blocked_by_promote_buffer_pf(classifier):
    """Test that promotion is blocked if PF doesn't exceed target tier min + buffer."""
    # Symbol barely qualifies for STANDARD (PF=1.21 > 1.2 min)
    # But with promote_buffer_pf=0.05, needs PF >= 1.25
    metrics = make_metrics(
        total_trades=20,
        win_rate=0.52,
        profit_factor=1.21,  # Just above STANDARD min (1.2) but below buffer threshold
        sharpe_ratio=0.35,
        max_drawdown=0.12,
        current_consecutive_losses=1,
    )

    # Already passed min_stay_trades and promote_after_trades
    tier_history = {
        "current_tier": "CONSERVATIVE",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 12,
        "consecutive_losses_in_tier": 0,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "PROBATION",
        "last_total_trades": 8,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in CONSERVATIVE due to promote_buffer_pf
    assert tier.name == "CONSERVATIVE"


def test_promotion_blocked_by_promote_after_trades(classifier):
    """Test that promotion is blocked if not enough trades since entering current tier."""
    # Symbol qualifies for STANDARD with good buffer
    metrics = make_metrics(
        total_trades=20,
        win_rate=0.52,
        profit_factor=1.30,  # Well above STANDARD min + buffer
        sharpe_ratio=0.35,
        max_drawdown=0.12,
        current_consecutive_losses=1,
    )

    # Met min_stay_trades but only 3 trades in tier (promote_after_trades=5)
    tier_history = {
        "current_tier": "CONSERVATIVE",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 3,
        "consecutive_losses_in_tier": 0,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "PROBATION",
        "last_total_trades": 17,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in CONSERVATIVE due to promote_after_trades
    assert tier.name == "CONSERVATIVE"


def test_demotion_blocked_by_demote_after_losses(classifier):
    """Test that demotion is blocked if consecutive losses < demote_after_losses threshold."""
    # Symbol no longer qualifies for STANDARD (PF dropped to 1.15)
    # BUT it is within the demotion buffer (1.2 - 0.05 = 1.15), so it should be saved by buffer.
    metrics = make_metrics(
        total_trades=25,
        win_rate=0.48,
        profit_factor=1.15,  # Below STANDARD min (1.2)
        sharpe_ratio=0.25,
        max_drawdown=0.16,
        current_consecutive_losses=2,  # Only 2 consecutive losses (demote_after_losses=3)
    )

    tier_history = {
        "current_tier": "STANDARD",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 15,
        "consecutive_losses_in_tier": 2,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "CONSERVATIVE",
        "last_total_trades": 10,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in STANDARD because of buffer (PF 1.15 >= 1.15)
    assert tier.name == "STANDARD"


def test_demotion_blocked_by_demote_buffer_pf(classifier):
    """Test that demotion is blocked if PF hasn't fallen below current tier min - buffer."""
    # Symbol at edge of STANDARD (PF=1.18)
    # With demote_buffer_pf=0.05, needs to fall below 1.15 (1.2 - 0.05) to demote
    metrics = make_metrics(
        total_trades=25,
        win_rate=0.48,
        profit_factor=1.18,  # Below STANDARD min (1.2) but above demotion threshold
        sharpe_ratio=0.25,
        max_drawdown=0.16,
        current_consecutive_losses=4,  # Exceeded demote_after_losses
    )

    tier_history = {
        "current_tier": "STANDARD",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 15,
        "consecutive_losses_in_tier": 4,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "CONSERVATIVE",
        "last_total_trades": 10,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in STANDARD because PF 1.18 > 1.15 (threshold)
    assert tier.name == "STANDARD"


def test_successful_promotion_after_meeting_all_conditions(classifier):
    """Test that promotion succeeds when all hysteresis conditions are met."""
    # Symbol strongly qualifies for STANDARD
    metrics = make_metrics(
        total_trades=30,
        win_rate=0.55,
        profit_factor=1.35,  # Well above STANDARD min (1.2) + buffer (0.05) = 1.25
        sharpe_ratio=0.40,
        max_drawdown=0.12,
        current_consecutive_losses=1,
    )

    # Met all promotion requirements
    tier_history = {
        "current_tier": "CONSERVATIVE",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 15,  # > min_stay_trades (10) and promote_after_trades (5)
        "consecutive_losses_in_tier": 0,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "PROBATION",
        "last_total_trades": 15,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should promote to STANDARD
    assert tier.name == "STANDARD"


def test_successful_demotion_after_grace_period(classifier):
    """Test that demotion succeeds when grace period expires and PF falls below buffer."""
    # Symbol clearly no longer qualifies for STANDARD
    metrics = make_metrics(
        total_trades=30,
        win_rate=0.46,  # Above CONSERVATIVE min (0.45)
        profit_factor=1.10,  # Below STANDARD min (1.2) - buffer (0.05) = 1.15
        sharpe_ratio=0.10,  # Above CONSERVATIVE min (0.0)
        max_drawdown=0.18,
        current_consecutive_losses=5,  # > demote_after_losses (3)
        max_consecutive_losses=3,  # Below CONSERVATIVE max (4)
    )

    tier_history = {
        "current_tier": "STANDARD",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 20,
        "consecutive_losses_in_tier": 5,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "CONSERVATIVE",
        "last_total_trades": 10,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should demote to CONSERVATIVE
    assert tier.name == "CONSERVATIVE"


def test_first_classification_without_history(classifier):
    """Test that classification works correctly when tier_history is None (first time)."""
    # New symbol qualifies for CONSERVATIVE
    metrics = make_metrics(
        total_trades=12,
        win_rate=0.48,
        profit_factor=1.15,
        sharpe_ratio=0.10,
        max_drawdown=0.18,
        current_consecutive_losses=2,
    )

    # No tier history (first classification)
    tier = classifier.classify(metrics, tier_history=None)

    # Should classify to CONSERVATIVE without hysteresis
    assert tier.name == "CONSERVATIVE"


def test_promotion_from_probation_no_history_required(classifier):
    """Test that promotion from PROBATION doesn't require tier history."""
    # Symbol qualifies for CONSERVATIVE
    metrics = make_metrics(
        total_trades=12,
        win_rate=0.48,
        profit_factor=1.15,
        sharpe_ratio=0.10,
        max_drawdown=0.18,
        current_consecutive_losses=2,
    )

    # Currently in PROBATION with minimal history
    tier_history = {
        "current_tier": "PROBATION",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 12,
        "consecutive_losses_in_tier": 0,
        "last_transition_time": None,
        "previous_tier": None,
        "last_total_trades": 0,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should promote to CONSERVATIVE (hysteresis doesn't block promotions from PROBATION)
    assert tier.name == "CONSERVATIVE"


def test_multi_tier_jump_blocked_by_hysteresis(classifier):
    """Test that hysteresis prevents jumping multiple tiers at once."""
    # Symbol technically qualifies for AGGRESSIVE
    metrics = make_metrics(
        total_trades=30,
        win_rate=0.58,
        profit_factor=1.65,  # Qualifies for AGGRESSIVE (1.5-2.0)
        sharpe_ratio=0.60,
        max_drawdown=0.10,
        current_consecutive_losses=1,
    )

    # Currently in CONSERVATIVE with limited trades
    tier_history = {
        "current_tier": "CONSERVATIVE",
        "tier_entry_time": datetime.now(timezone.utc).isoformat(),
        "trades_in_tier": 8,  # < min_stay_trades (10)
        "consecutive_losses_in_tier": 0,
        "last_transition_time": datetime.now(timezone.utc).isoformat(),
        "previous_tier": "PROBATION",
        "last_total_trades": 22,
    }

    tier = classifier.classify(metrics, tier_history)

    # Should stay in CONSERVATIVE due to min_stay_trades, not jump to AGGRESSIVE
    assert tier.name == "CONSERVATIVE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
