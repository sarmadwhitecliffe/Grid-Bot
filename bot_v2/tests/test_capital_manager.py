"""
Tests for Capital Manager

Test Coverage:
- Capital initialization and retrieval
- Capital updates (profit/loss)
- Mode management (simulation/live)
- Tier tracking
- Thread safety
- Legacy format migration
- Edge cases (negative capital, concurrent access)
"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from bot_v2.risk.capital_manager import CapitalManager


class TestCapitalInitialization:
    """Test capital initialization and defaults."""

    @pytest.mark.asyncio
    async def test_new_symbol_gets_default_capital(self, temp_data_dir):
        """New symbols should get $1000 default capital."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        capital = await manager.get_capital("BTCUSDT")

        assert capital == Decimal("1000.00")
        # Mode is no longer stored in CapitalManager (read from strategy_configs.json)
        assert await manager.get_tier("BTCUSDT") == "PROBATION"

    @pytest.mark.asyncio
    async def test_multiple_symbols_independent(self, temp_data_dir):
        """Multiple symbols should have independent capital."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        btc_capital = await manager.get_capital("BTCUSDT")
        eth_capital = await manager.get_capital("ETHUSDT")

        assert btc_capital == Decimal("1000.00")
        assert eth_capital == Decimal("1000.00")

        # Update one should not affect the other
        await manager.update_capital("BTCUSDT", Decimal("500"))

        assert await manager.get_capital("BTCUSDT") == Decimal("1500.00")
        assert await manager.get_capital("ETHUSDT") == Decimal("1000.00")


class TestCapitalUpdates:
    """Test capital updates after trades."""

    @pytest.mark.asyncio
    async def test_profit_increases_capital(self, temp_data_dir):
        """Profit should increase capital."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        initial = await manager.get_capital("BTCUSDT")
        new_capital = await manager.update_capital("BTCUSDT", Decimal("150.50"))

        assert new_capital == initial + Decimal("150.50")
        assert await manager.get_capital("BTCUSDT") == Decimal("1150.50")

    @pytest.mark.asyncio
    async def test_loss_decreases_capital(self, temp_data_dir):
        """Loss should decrease capital."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        initial = await manager.get_capital("BTCUSDT")
        new_capital = await manager.update_capital("BTCUSDT", Decimal("-75.25"))

        assert new_capital == initial - Decimal("75.25")
        assert await manager.get_capital("BTCUSDT") == Decimal("924.75")

    @pytest.mark.asyncio
    async def test_negative_capital_clamped_to_zero(self, temp_data_dir):
        """Capital should never go below zero."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Set capital to $100
        await manager.set_capital("BTCUSDT", Decimal("100"))

        # Try to lose $200 (more than available)
        new_capital = await manager.update_capital("BTCUSDT", Decimal("-200"))

        assert new_capital == Decimal("0")
        assert await manager.get_capital("BTCUSDT") == Decimal("0")

    @pytest.mark.asyncio
    async def test_sequential_updates(self, temp_data_dir):
        """Sequential capital updates should compound."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        await manager.update_capital("BTCUSDT", Decimal("100"))  # 1000 + 100 = 1100
        await manager.update_capital("BTCUSDT", Decimal("50"))  # 1100 + 50 = 1150
        await manager.update_capital("BTCUSDT", Decimal("-30"))  # 1150 - 30 = 1120

        assert await manager.get_capital("BTCUSDT") == Decimal("1120.00")


class TestTierManagement:
    """Test risk tier tracking."""

    @pytest.mark.asyncio
    async def test_default_tier_is_probation(self, temp_data_dir):
        """New symbols should start at PROBATION tier."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        tier = await manager.get_tier("BTCUSDT")

        assert tier == "PROBATION"

    @pytest.mark.asyncio
    async def test_set_tier(self, temp_data_dir):
        """Should be able to update tier."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        await manager.set_tier("BTCUSDT", "STANDARD")

        assert await manager.get_tier("BTCUSDT") == "STANDARD"

    @pytest.mark.asyncio
    async def test_tier_progression(self, temp_data_dir):
        """Should track tier changes."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Progress through tiers
        await manager.set_tier("BTCUSDT", "STANDARD")
        assert await manager.get_tier("BTCUSDT") == "STANDARD"

        await manager.set_tier("BTCUSDT", "AGGRESSIVE")
        assert await manager.get_tier("BTCUSDT") == "AGGRESSIVE"

        await manager.set_tier("BTCUSDT", "CHAMPION")
        assert await manager.get_tier("BTCUSDT") == "CHAMPION"

    @pytest.mark.asyncio
    async def test_last_notified_tier(self, temp_data_dir):
        """Should track last notified tier (prevent spam)."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Set tier but haven't notified yet
        await manager.set_tier("BTCUSDT", "STANDARD")
        assert await manager.get_last_notified_tier("BTCUSDT") == "PROBATION"

        # Mark as notified
        await manager.set_last_notified_tier("BTCUSDT", "STANDARD")
        assert await manager.get_last_notified_tier("BTCUSDT") == "STANDARD"


class TestPersistence:
    """Test data persistence and reloading."""

    @pytest.mark.asyncio
    async def test_capital_persists_across_instances(self, temp_data_dir):
        """Capital should persist when manager is recreated."""
        # First instance
        manager1 = CapitalManager(data_dir=Path(temp_data_dir))
        await manager1.set_capital("BTCUSDT", Decimal("2500"))
        await manager1.set_tier("BTCUSDT", "AGGRESSIVE")

        # Create new instance (simulates bot restart)
        manager2 = CapitalManager(data_dir=Path(temp_data_dir))

        # Should load saved values
        assert await manager2.get_capital("BTCUSDT") == Decimal("2500")
        # Mode is read from strategy_configs.json, not stored in CapitalManager
        assert await manager2.get_tier("BTCUSDT") == "AGGRESSIVE"

    @pytest.mark.asyncio
    async def test_legacy_format_migration(self, temp_data_dir):
        """Should migrate old format {"symbol": "1000.00"} to new format."""
        data_dir = Path(temp_data_dir)
        capitals_file = data_dir / "symbol_capitals.json"

        # Write legacy format
        legacy_data = {"BTCUSDT": "1500.00", "ETHUSDT": "2000.00"}
        with open(capitals_file, "w") as f:
            json.dump(legacy_data, f)

        # Load with new manager
        manager = CapitalManager(data_dir=data_dir)

        # Should auto-migrate
        assert await manager.get_capital("BTCUSDT") == Decimal("1500.00")
        assert await manager.get_capital("ETHUSDT") == Decimal("2000.00")
        # Mode is no longer stored in CapitalManager (read from strategy_configs.json)
        assert await manager.get_tier("BTCUSDT") == "PROBATION"

    @pytest.mark.asyncio
    async def test_empty_file_starts_fresh(self, temp_data_dir):
        """Missing file should start with empty state."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Should work fine without existing file
        capital = await manager.get_capital("BTCUSDT")
        assert capital == Decimal("1000.00")


class TestManualCapitalSetting:
    """Test manual capital override."""

    @pytest.mark.asyncio
    async def test_set_capital_directly(self, temp_data_dir):
        """Should be able to manually set capital."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        await manager.set_capital("BTCUSDT", Decimal("5000"))

        assert await manager.get_capital("BTCUSDT") == Decimal("5000")

    @pytest.mark.asyncio
    async def test_set_capital_when_graduating_to_live(self, temp_data_dir):
        """Common workflow: sim → live with capital reset."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Simulate some profits in sim mode
        await manager.update_capital("BTCUSDT", Decimal("500"))  # $1500
        assert await manager.get_capital("BTCUSDT") == Decimal("1500")

        # Graduate to live: reset capital (mode is managed in strategy_configs.json)
        await manager.set_capital("BTCUSDT", Decimal("100"))  # Reset to $100
        await manager.set_tier("BTCUSDT", "STANDARD")

        assert await manager.get_capital("BTCUSDT") == Decimal("100")
        # Mode is read from strategy_configs.json, not from CapitalManager
        assert await manager.get_tier("BTCUSDT") == "STANDARD"


class TestBulkOperations:
    """Test bulk operations for monitoring/reporting."""

    def test_get_all_capitals(self, temp_data_dir):
        """Should be able to get all capitals at once."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Create some capitals (synchronously for simplicity)
        asyncio.run(manager.get_capital("BTCUSDT"))
        asyncio.run(manager.get_capital("ETHUSDT"))
        asyncio.run(manager.get_capital("SOLUSDT"))

        all_capitals = manager.get_all_capitals()

        assert len(all_capitals) == 3
        assert all_capitals["BTCUSDT"] == Decimal("1000.00")
        assert all_capitals["ETHUSDT"] == Decimal("1000.00")
        assert all_capitals["SOLUSDT"] == Decimal("1000.00")

    # Note: get_all_modes() test removed - mode is now in strategy_configs.json
    # Mode management is no longer part of CapitalManager's responsibilities


class TestThreadSafety:
    """Test concurrent access safety."""

    @pytest.mark.asyncio
    async def test_concurrent_updates_same_symbol(self, temp_data_dir):
        """Concurrent updates to same symbol should not corrupt state."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Run 10 concurrent updates
        updates = [manager.update_capital("BTCUSDT", Decimal("10")) for _ in range(10)]
        await asyncio.gather(*updates)

        # Should have applied all 10 updates
        capital = await manager.get_capital("BTCUSDT")
        assert capital == Decimal("1100.00")  # 1000 + (10 * 10)

    @pytest.mark.asyncio
    async def test_concurrent_updates_different_symbols(self, temp_data_dir):
        """Concurrent updates to different symbols should work."""
        manager = CapitalManager(data_dir=Path(temp_data_dir))

        # Update multiple symbols concurrently
        updates = [
            manager.update_capital("BTCUSDT", Decimal("100")),
            manager.update_capital("ETHUSDT", Decimal("50")),
            manager.update_capital("SOLUSDT", Decimal("25")),
        ]
        await asyncio.gather(*updates)

        assert await manager.get_capital("BTCUSDT") == Decimal("1100.00")
        assert await manager.get_capital("ETHUSDT") == Decimal("1050.00")
        assert await manager.get_capital("SOLUSDT") == Decimal("1025.00")
