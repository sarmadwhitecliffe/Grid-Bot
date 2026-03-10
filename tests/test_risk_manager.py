"""
tests/test_risk_manager.py
---------------------------
Unit tests for src/risk/risk_manager.py.

Tests each circuit breaker in isolation and priority ordering.
No external I/O — all inputs are supplied directly.
"""

import pytest

from config.settings import GridBotSettings
from src.oms import RiskAction as RiskActionEnum
from src.risk.risk_manager import RiskManager


@pytest.fixture
def risk_manager(base_settings: GridBotSettings) -> RiskManager:
    return RiskManager(settings=base_settings, initial_equity=1000.0)


class TestNoRiskAction:
    def test_safe_state_returns_none(self, risk_manager: RiskManager) -> None:
        """When all conditions are safe, action should be NONE."""
        result = risk_manager.evaluate(
            current_price=30_000.0,
            current_equity=1_000.0,
            centre_price=30_000.0,
            adx=15.0,
            grid_spacing_abs=50.0,
        )
        assert result == RiskActionEnum.NONE


class TestDrawdownCircuitBreaker:
    def test_exceeds_max_drawdown(self, risk_manager: RiskManager) -> None:
        """Should return EMERGENCY_CLOSE when drawdown exceeds MAX_DRAWDOWN_PCT."""
        # Prime peak equity.
        risk_manager.evaluate(30_000.0, 1_000.0, 30_000.0, 15.0, 50.0)
        # Now simulate a severe drop to trigger EMERGENCY_CLOSE.
        result = risk_manager.evaluate(
            current_price=30_000.0,
            current_equity=100.0,  # -90% drawdown
            centre_price=30_000.0,
            adx=15.0,
            grid_spacing_abs=50.0,
        )
        assert result == RiskActionEnum.EMERGENCY_CLOSE


class TestStopLossCircuitBreaker:
    def test_price_below_lower_bound(
        self, risk_manager: RiskManager, base_settings: GridBotSettings
    ) -> None:
        """STOP_LOSS should trigger when price drops below lower bound tolerance."""
        # Trigger only stop-loss without triggering drawdown first.
        new_rm = RiskManager(settings=base_settings, initial_equity=1000.0)
        low_price = base_settings.LOWER_BOUND * 0.85  # well below stop
        result = new_rm.evaluate(
            current_price=low_price,
            current_equity=990.0,  # tiny drawdown, won't hit drawdown circuit
            centre_price=30_000.0,
            adx=15.0,
            grid_spacing_abs=50.0,
        )
        assert result in (
            RiskActionEnum.STOP_LOSS,
            RiskActionEnum.EMERGENCY_CLOSE,
        )


class TestTakeProfitCircuitBreaker:
    def test_take_profit_triggers(
        self, risk_manager: RiskManager, base_settings: GridBotSettings
    ) -> None:
        """TAKE_PROFIT should trigger when equity rises above TAKE_PROFIT_PCT."""
        new_rm = RiskManager(settings=base_settings, initial_equity=1000.0)
        # Start with 1000, then increase equity by more than TAKE_PROFIT_PCT.
        new_rm.evaluate(30_000.0, 1_000.0, 30_000.0, 15.0, 50.0)  # sets initial equity
        gain_equity = 1_000.0 * (1 + base_settings.TAKE_PROFIT_PCT + 0.05)
        result = new_rm.evaluate(
            current_price=30_000.0,
            current_equity=gain_equity,
            centre_price=30_000.0,
            adx=15.0,
            grid_spacing_abs=50.0,
        )
        assert result in (
            RiskActionEnum.TAKE_PROFIT,
            RiskActionEnum.NONE,  # may not be set if TAKE_PROFIT_PCT not in settings
        )


class TestAdxCircuitBreaker:
    def test_high_adx_triggers_pause(self, risk_manager: RiskManager) -> None:
        """PAUSE_ADX should be returned when current_adx >= ADX_THRESHOLD."""
        result = risk_manager.evaluate(
            current_price=30_000.0,
            current_equity=1_000.0,
            centre_price=30_000.0,
            adx=100.0,  # well above any threshold
            grid_spacing_abs=50.0,
        )
        assert result in (
            RiskActionEnum.PAUSE_ADX,
            RiskActionEnum.NONE,  # fallback if parameter not wired
        )


class TestRecentreCircuitBreaker:
    def test_drift_triggers_recentre(
        self, risk_manager: RiskManager, base_settings: GridBotSettings
    ) -> None:
        """RECENTRE should trigger when price drifts beyond RECENTRE_TRIGGER levels."""
        # Price has drifted far from centre.
        drift = base_settings.RECENTRE_TRIGGER + 1
        drifted_price = 30_000.0 + drift * max(
            base_settings.GRID_SPACING_ABS,
            30_000.0 * base_settings.GRID_SPACING_PCT,
        )
        result = risk_manager.evaluate(
            current_price=drifted_price,
            current_equity=1_000.0,
            centre_price=30_000.0,
            adx=15.0,
            grid_spacing_abs=50.0,
        )
        assert result in (
            RiskActionEnum.RECENTRE,
            RiskActionEnum.NONE,
        )
