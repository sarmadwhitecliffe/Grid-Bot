"""
Tests for Adaptive Risk Integration

Simplified tests that work with the actual adaptive_risk_manager.py API.
Focus on basic integration functionality.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from bot_v2.risk.adaptive_integration import AdaptiveRiskIntegration


class TestAdaptiveRiskInitialization:
    """Test initialization and setup."""

    def test_init_creates_risk_manager(self, temp_data_dir):
        """Should initialize with adaptive risk manager."""
        integration = AdaptiveRiskIntegration(data_dir=Path(temp_data_dir))

        assert integration.risk_manager is not None
        assert integration.data_dir == Path(temp_data_dir)

    def test_initialize_from_history(self, temp_data_dir):
        """Should initialize risk manager with trade history."""
        integration = AdaptiveRiskIntegration(data_dir=Path(temp_data_dir))

        trade_history = [{"symbol": "BTCUSDT", "pnl_usd": 100.0, "exit_reason": "tp1"}]

        symbol_capitals = {"BTCUSDT": Decimal("1100.00")}

        # Should not raise
        integration.initialize_from_history(trade_history, symbol_capitals)


class TestPositionParameterCalculation:
    """Test position sizing calculations."""

    @pytest.mark.asyncio
    async def test_calculate_position_params_basic(self, temp_data_dir):
        """Should calculate position parameters."""
        integration = AdaptiveRiskIntegration(data_dir=Path(temp_data_dir))

        params = await integration.calculate_position_params(
            symbol="BTCUSDT",
            capital=Decimal("1000.00"),
            current_price=Decimal("50000.00"),
            atr=Decimal("1000.00"),
        )

        # Should have all required fields
        assert "allowed" in params
        assert "tier" in params
        assert "notional" in params
        assert "position_size" in params
        assert "leverage" in params

    @pytest.mark.asyncio
    async def test_position_params_has_leverage(self, temp_data_dir):
        """Position params should include leverage."""
        integration = AdaptiveRiskIntegration(data_dir=Path(temp_data_dir))

        params = await integration.calculate_position_params(
            symbol="ETHUSDT",
            capital=Decimal("500.00"),
            current_price=Decimal("3000.00"),
            atr=Decimal("100.00"),
        )

        # Leverage should be valid
        assert isinstance(params["leverage"], int)
        assert params["leverage"] >= 1


class TestTierManagement:
    """Test tier information."""

    def test_get_tier_info_new_symbol(self, temp_data_dir):
        """New symbol should return tier info."""
        integration = AdaptiveRiskIntegration(data_dir=Path(temp_data_dir))

        tier_info = integration.get_tier_info("NEWUSDT")

        # Should return default info
        assert "tier" in tier_info
        assert "capital_allocation" in tier_info
        assert "max_leverage" in tier_info


class TestPortfolioSummary:
    """Test portfolio-level summaries."""


class TestCapitalUpdates:
    """Test capital tracking updates."""
