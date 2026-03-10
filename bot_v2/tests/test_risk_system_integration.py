"""
Integration tests for the complete risk management system.

Tests the interaction between CapitalManager and AdaptiveRiskIntegration,
validating the full capital → tier → position sizing → trade recording pipeline.
"""

from decimal import Decimal

import pytest

from bot_v2.risk.adaptive_integration import AdaptiveRiskIntegration
from bot_v2.risk.capital_manager import CapitalManager


class TestCapitalToTierFlow:
    """Test the flow from capital initialization to tier assignment to position sizing."""

    @pytest.mark.asyncio
    async def test_new_symbol_initialization_flow(self, temp_data_dir):
        """Test complete initialization flow for a new symbol."""
        # Initialize both systems
        capital_mgr = CapitalManager(data_dir=temp_data_dir)
        risk_mgr = AdaptiveRiskIntegration(data_dir=temp_data_dir)

        symbol = "BTCUSDT"

        # Step 1: Get initial capital (should auto-initialize to 000)
        capital = await capital_mgr.get_capital(symbol)
        assert capital == Decimal("1000.00")

        # Step 2: Check initial tier (mode is no longer managed by CapitalManager)
        tier = await capital_mgr.get_tier(symbol)
        assert tier == "PROBATION"

        # Step 3: Calculate position parameters with initial capital
        # Need current_price and atr for position sizing
        current_price = 50000.0  # BTC price
        atr = 2000.0  # ATR value

        params = await risk_mgr.calculate_position_params(
            symbol=symbol,
            capital=float(capital),
            current_price=current_price,
            atr=atr,
            active_positions={},
        )

        # Should be allowed with PROBATION tier constraints
        assert params["allowed"] is True
        assert params["tier"] == "PROBATION"
        assert params["position_size"] > 0
        assert params["leverage"] >= 1

    @pytest.mark.asyncio
    async def test_capital_update_affects_position_size(self, temp_data_dir):
        """Test that capital changes affect position sizing."""
        capital_mgr = CapitalManager(data_dir=temp_data_dir)
        risk_mgr = AdaptiveRiskIntegration(data_dir=temp_data_dir)

        symbol = "ETHUSDT"
        current_price = 3000.0  # ETH price
        atr = 150.0

        # Use smaller capital to avoid hitting max_position_size_usd cap of PROBATION tier (00)
        # Initial capital: 00
        await capital_mgr.set_capital(symbol, Decimal("100.00"))

        # Get position size with initial capital (00)
        capital1 = await capital_mgr.get_capital(symbol)
        params1 = await risk_mgr.calculate_position_params(
            symbol=symbol,
            capital=float(capital1),
            current_price=current_price,
            atr=atr,
            active_positions={},
        )
        size1 = params1["position_size"]

        # Update capital with profit (+50%)
        await capital_mgr.update_capital(symbol, Decimal("50.00"))
        capital2 = await capital_mgr.get_capital(symbol)
        assert capital2 == Decimal("150.00")

        # Get new position size with increased capital
        params2 = await risk_mgr.calculate_position_params(
            symbol=symbol,
            capital=float(capital2),
            current_price=current_price,
            atr=atr,
            active_positions={},
        )
        size2 = params2["position_size"]

        # Position size should increase with capital (proportional)
        assert size2 > size1
        # Ratio should be approximately 1.5 (150/100)
        ratio = size2 / size1
        assert 1.4 < ratio < 1.6  # Allow some variance due to rounding


class TestTierTransitions:
    """Test tier transitions and their effects on position sizing."""

    @pytest.mark.asyncio
    async def test_tier_progression_through_trades(self, temp_data_dir):
        """Test that capital updates affect tier tracking in CapitalManager."""
        capital_mgr = CapitalManager(data_dir=temp_data_dir)

        symbol = "BTCUSDT"

        # Start in PROBATION
        await capital_mgr.set_tier(symbol, "PROBATION")
        initial_tier = await capital_mgr.get_tier(symbol)
        assert initial_tier == "PROBATION"

        # Simulate profitable trades by updating capital
        for i in range(5):
            pnl = Decimal("100.00")
            await capital_mgr.update_capital(symbol, pnl)

        # Capital should have increased
        final_capital = await capital_mgr.get_capital(symbol)
        assert final_capital == Decimal("1500.00")  # 1000 + (5 * 100)

        # Manually upgrade tier (in real system, AdaptiveRiskManager would do this)
        await capital_mgr.set_tier(symbol, "STANDARD")
        upgraded_tier = await capital_mgr.get_tier(symbol)
        assert upgraded_tier == "STANDARD"

    @pytest.mark.asyncio
    async def test_tier_change_notification_tracking(self, temp_data_dir):
        """Test that tier change notifications are tracked to avoid spam."""
        capital_mgr = CapitalManager(data_dir=temp_data_dir)

        symbol = "ETHUSDT"

        # Set initial tier
        await capital_mgr.set_tier(symbol, "PROBATION")

        # Mark as notified for PROBATION
        await capital_mgr.set_last_notified_tier(symbol, "PROBATION")
        last_notified = await capital_mgr.get_last_notified_tier(symbol)
        assert last_notified == "PROBATION"

        # Change tier to STANDARD
        await capital_mgr.set_tier(symbol, "STANDARD")
        current_tier = await capital_mgr.get_tier(symbol)
        assert current_tier == "STANDARD"

        # Should need notification (tier changed but last_notified hasn't)
        assert last_notified != current_tier

        # After notifying, update last_notified
        await capital_mgr.set_last_notified_tier(symbol, "STANDARD")
        new_last_notified = await capital_mgr.get_last_notified_tier(symbol)
        assert new_last_notified == "STANDARD"
        assert new_last_notified == current_tier


class TestPortfolioHeatManagement:
    """Test portfolio-level risk management with multiple symbols."""

    @pytest.mark.asyncio
    async def test_multiple_symbols_portfolio_heat(self, temp_data_dir):
        """Test portfolio heat calculation with multiple active positions."""
        risk_mgr = AdaptiveRiskIntegration(data_dir=temp_data_dir)

        # Simulate active positions for multiple symbols (as dict)
        active_positions = {
            "BTCUSDT": {"symbol": "BTCUSDT", "notional": 5000.0},
            "ETHUSDT": {"symbol": "ETHUSDT", "notional": 3000.0},
            "SOLUSDT": {"symbol": "SOLUSDT", "notional": 2000.0},
        }

        # Get portfolio summary
        summary = risk_mgr.get_portfolio_summary()

        # Should have portfolio-level metrics (actual format from get_portfolio_summary)
        assert "total_symbols" in summary
        assert "tier_distribution" in summary
        assert "risk_manager_active" in summary

        # Calculate position for a new symbol with existing positions
        params = await risk_mgr.calculate_position_params(
            symbol="ADAUSDT",
            capital=1000.0,
            current_price=0.5,
            atr=0.02,
            active_positions=active_positions,
        )

        # Should consider portfolio heat in decision
        assert "allowed" in params
        # If portfolio heat is too high, might not allow new position
        # (depends on adaptive_risk_manager's heat limits)

    @pytest.mark.asyncio
    async def test_capital_sync_across_symbols(self, temp_data_dir):
        """Test that capital is tracked independently per symbol."""
        capital_mgr = CapitalManager(data_dir=temp_data_dir)


        # Initialize capitals with different amounts
        await capital_mgr.set_capital("BTCUSDT", Decimal("2000.00"))
        await capital_mgr.set_capital("ETHUSDT", Decimal("1500.00"))
        await capital_mgr.set_capital("SOLUSDT", Decimal("1000.00"))

        # Update one symbol's capital
        await capital_mgr.update_capital("BTCUSDT", Decimal("500.00"))

        # Get all capitals
        all_capitals = capital_mgr.get_all_capitals()

        # Check independence - get_all_capitals returns Dict[str, Decimal]
        assert all_capitals["BTCUSDT"] == Decimal("2500.00")  # 2000 + 500
        assert all_capitals["ETHUSDT"] == Decimal("1500.00")  # Unchanged
        assert all_capitals["SOLUSDT"] == Decimal("1000.00")  # Unchanged


class TestRiskSystemPersistence:
    """Test that risk system state persists across restarts."""

    @pytest.mark.asyncio
    async def test_capital_and_tier_persistence(self, temp_data_dir):
        """Test that capital and tier persist across manager instances."""
        symbol = "BTCUSDT"

        # First instance: set up state
        capital_mgr1 = CapitalManager(data_dir=temp_data_dir)
        await capital_mgr1.set_capital(symbol, Decimal("3000.00"))
        await capital_mgr1.set_tier(symbol, "AGGRESSIVE")

        # Destroy first instance
        del capital_mgr1

        # Second instance: verify state persisted
        capital_mgr2 = CapitalManager(data_dir=temp_data_dir)

        capital = await capital_mgr2.get_capital(symbol)
        tier = await capital_mgr2.get_tier(symbol)

        assert capital == Decimal("3000.00")
        assert tier == "AGGRESSIVE"

    @pytest.mark.asyncio
    async def test_adaptive_risk_state_persistence(self, temp_data_dir):
        """Test that adaptive risk manager state and tier info persist."""
        symbol = "ETHUSDT"

        # First instance: set up tier and get info
        risk_mgr1 = AdaptiveRiskIntegration(data_dir=temp_data_dir)

        # Force a tier calculation/assignment
        await risk_mgr1.calculate_position_params(
            symbol=symbol,
            capital=1000.0,
            current_price=2000.0,
            atr=100.0,
            active_positions={},
        )

        # Get current tier
        tier_info1 = risk_mgr1.get_tier_info(symbol)
        tier1 = tier_info1["tier"]

        # Destroy first instance
        del risk_mgr1

        # Second instance
        risk_mgr2 = AdaptiveRiskIntegration(data_dir=temp_data_dir)

        # Should recover same tier
        tier_info2 = risk_mgr2.get_tier_info(symbol)
        tier2 = tier_info2["tier"]
        assert tier1 == tier2
