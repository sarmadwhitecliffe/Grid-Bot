import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
import tempfile
import json

from bot_v2.risk.adaptive_risk_manager import AdaptiveRiskManager, RiskTier
from bot_v2.risk.capital_manager import CapitalManager

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)

def create_mock_tier(name="STANDARD"):
    tier = MagicMock()
    tier.name = name
    tier.max_leverage = 5
    tier.position_size_pct = 0.02
    tier.capital_allocation = 1.0
    tier.min_leverage = 1
    tier.max_position_size_usd = None
    return tier

def create_mock_metrics():
    metrics = MagicMock()
    metrics.total_trades = 10
    metrics.profit_factor = 1.5
    metrics.win_rate = 0.5
    metrics.current_consecutive_losses = 0
    metrics.current_drawdown_pct = 0.0
    metrics.avg_win_r = 1.0
    metrics.avg_win = 10.0
    metrics.avg_loss = 5.0
    return metrics

@pytest.mark.asyncio
async def test_kill_switch_trigger_returns_dict(temp_dir, mocker):
    capital_manager = CapitalManager(temp_dir)
    await capital_manager.set_tier("BTCUSDT", "STANDARD")
    
    standard_tier = create_mock_tier("STANDARD")
    arm = AdaptiveRiskManager(data_dir=temp_dir, capital_manager=capital_manager)
    arm.tier_cache["BTCUSDT"] = standard_tier
    
    mock_metrics = create_mock_metrics()
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.PerformanceAnalyzer.calculate_metrics',
        return_value=mock_metrics
    )    
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.KillSwitch.check_triggers',
        return_value=(True, "Max drawdown exceeded")
    )
    
    result = arm.calculate_position_parameters(
        "BTCUSDT", 1000.0, 50000.0, 100.0, [], []
    )
    
    assert result["allowed"] is False
    assert result["tier"] == "STANDARD"
    assert result["kill_switch_active"] is True
    assert "Kill switch triggered" in result["reason"]

@pytest.mark.asyncio
async def test_kill_switch_active_returns_dict(temp_dir, mocker):
    capital_manager = CapitalManager(temp_dir)
    
    standard_tier = create_mock_tier("STANDARD")
    arm = AdaptiveRiskManager(data_dir=temp_dir, capital_manager=capital_manager)
    arm.kill_switch_active["BTCUSDT"] = True
    arm.tier_cache["BTCUSDT"] = standard_tier
    
    result = arm.calculate_position_parameters(
        "BTCUSDT", 1000.0, 50000.0, 100.0, [], []
    )
    
    assert result["allowed"] is False
    assert result["tier"] == "STANDARD"
    assert result["kill_switch_active"] is True
    assert result["reason"] == "Kill switch active"

@pytest.mark.asyncio
async def test_set_tier_kill_switch_logs_error_defaults_probation(temp_dir, caplog):
    capital_manager = CapitalManager(temp_dir)
    
    await capital_manager.set_tier("BTCUSDT", "KILL_SWITCH")
    
    tier = await capital_manager.get_tier("BTCUSDT")
    assert tier == "PROBATION"
    assert "Invalid tier 'KILL_SWITCH'" in caplog.text

@pytest.mark.asyncio
async def test_reset_kill_switch_preserves_tier(temp_dir, mocker):
    capital_manager = CapitalManager(temp_dir)
    await capital_manager.set_tier("BTCUSDT", "STANDARD")
    
    standard_tier = create_mock_tier("STANDARD")
    arm = AdaptiveRiskManager(data_dir=temp_dir, capital_manager=capital_manager)
    arm.tier_cache["BTCUSDT"] = standard_tier
    
    # 1. Trigger kill switch
    mock_metrics = create_mock_metrics()
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.PerformanceAnalyzer.calculate_metrics',
        return_value=mock_metrics
    )
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.KillSwitch.check_triggers',
        return_value=(True, "Max drawdown exceeded")
    )
    
    result1 = arm.calculate_position_parameters(
        "BTCUSDT", 1000.0, 50000.0, 100.0, [], []
    )
    assert result1["allowed"] is False
    assert result1["tier"] == "STANDARD"
    
    # 2. Reset kill switch
    arm.reset_kill_switch("BTCUSDT")
    
    # 3. Next signal should use STANDARD tier (assuming it classifies as such)
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.KillSwitch.check_triggers',
        return_value=(False, "")
    )
    mocker.patch(
        'bot_v2.risk.adaptive_risk_manager.RiskTierClassifier.classify',
        return_value=standard_tier
    )
    
    result2 = arm.calculate_position_parameters(
        "BTCUSDT", 1000.0, 50000.0, 100.0, [], []
    )
    
    assert result2["allowed"] is True
    assert result2["tier"] == "STANDARD"
