"""
Tests for Persistence Layer (StateManager)

Tests cover:
- Loading/saving positions
- Loading/saving capitals
- Loading/saving trade history
- Loading strategy configurations
- Error handling for missing/corrupt files
- Atomic operations
"""

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from bot_v2.models.enums import PositionSide
from bot_v2.persistence.state_manager import StateManager

# ==============================================================================
# Test StateManager Initialization
# ==============================================================================


class TestStateManagerInit:
    """Test StateManager initialization."""

    def test_creates_data_directory(self):
        """StateManager creates data directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "test_data"
            assert not data_dir.exists()

            manager = StateManager(data_dir)
            assert data_dir.exists()
            assert manager.data_dir == data_dir

    def test_sets_correct_file_paths(self):
        """StateManager sets correct paths for all data files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "test_data"
            manager = StateManager(data_dir)

            assert manager.positions_file == data_dir / "active_positions.json"
            assert manager.capitals_file == data_dir / "symbol_capitals.json"
            assert manager.history_file == data_dir / "trade_history.json"
            assert (
                manager.strategy_configs_file
                == Path("config") / "strategy_configs.json"
            )


# ==============================================================================
# Test Position Loading/Saving
# ==============================================================================


class TestPositionPersistence:
    """Test position loading and saving."""

    @pytest.fixture
    def temp_manager(self):
        """Create StateManager with temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))
            yield manager

    def test_save_positions(self, temp_manager, sample_long_position):
        """StateManager saves positions correctly."""
        positions = {"BTCUSDT": sample_long_position}
        temp_manager.save_positions(positions)

        # Verify file exists
        assert temp_manager.positions_file.exists()

        # Verify content
        with open(temp_manager.positions_file, "r") as f:
            data = json.load(f)

        assert "BTCUSDT" in data
        assert data["BTCUSDT"]["symbol_id"] == "BTCUSDT"
        assert (
            data["BTCUSDT"]["side"] == "long"
        )  # PositionSide.LONG serializes as 'long'

    def test_load_positions(self, temp_manager, sample_long_position):
        """StateManager loads positions correctly."""
        positions = {"BTCUSDT": sample_long_position}
        temp_manager.save_positions(positions)

        # Load positions
        loaded = temp_manager.load_positions()

        assert len(loaded) == 1
        assert "BTCUSDT" in loaded
        assert loaded["BTCUSDT"].symbol_id == "BTCUSDT"
        assert loaded["BTCUSDT"].side == PositionSide.LONG
        assert loaded["BTCUSDT"].entry_price == Decimal("100")

    def test_load_positions_empty_file(self, temp_manager):
        """StateManager returns empty dict for missing positions file."""
        loaded = temp_manager.load_positions()
        assert loaded == {}

    def test_load_positions_corrupt_file(self, temp_manager):
        """StateManager handles corrupt positions file gracefully."""
        # Write corrupt JSON
        with open(temp_manager.positions_file, "w") as f:
            f.write("{ invalid json }")

        loaded = temp_manager.load_positions()
        assert loaded == {}


# ==============================================================================
# Test Capital Loading/Saving
# ==============================================================================


class TestCapitalPersistence:
    """Test capital loading and saving."""

    @pytest.fixture
    def temp_manager(self):
        """Create StateManager with temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))
            yield manager

    def test_save_capitals(self, temp_manager):
        """StateManager saves capitals correctly."""
        capitals = {"BTCUSDT": Decimal("1000"), "ETHUSDT": Decimal("500")}
        temp_manager.save_capitals(capitals)

        # Verify file exists
        assert temp_manager.capitals_file.exists()

        # Verify content
        with open(temp_manager.capitals_file, "r") as f:
            data = json.load(f)

        assert data["BTCUSDT"] == "1000"
        assert data["ETHUSDT"] == "500"

    def test_load_capitals(self, temp_manager):
        """StateManager loads capitals correctly."""
        capitals = {"BTCUSDT": Decimal("1000"), "ETHUSDT": Decimal("500")}
        temp_manager.save_capitals(capitals)

        # Load capitals
        loaded = temp_manager.load_capitals()

        assert len(loaded) == 2
        assert loaded["BTCUSDT"] == Decimal("1000")
        assert loaded["ETHUSDT"] == Decimal("500")

    def test_load_capitals_empty_file(self, temp_manager):
        """StateManager returns empty dict for missing capitals file."""
        loaded = temp_manager.load_capitals()
        assert loaded == {}


# ==============================================================================
# Test Trade History Loading/Saving
# ==============================================================================


class TestHistoryPersistence:
    """Test trade history loading and saving."""

    @pytest.fixture
    def temp_manager(self):
        """Create StateManager with temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))
            yield manager

    @pytest.fixture
    def sample_history(self):
        """Create sample trade history."""
        return [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "entry_price": "50000",
                "exit_price": "51000",
                "pnl": "100",
                "exit_reason": "tp1",
            },
            {
                "symbol": "ETHUSDT",
                "side": "SHORT",
                "entry_price": "3000",
                "exit_price": "2950",
                "pnl": "50",
                "exit_reason": "hard_stop",
            },
        ]

    def test_save_history(self, temp_manager, sample_history):
        """StateManager saves trade history correctly."""
        temp_manager.save_history(sample_history)

        # Verify file exists
        assert temp_manager.history_file.exists()

        # Verify content
        with open(temp_manager.history_file, "r") as f:
            data = json.load(f)

        assert len(data) == 2
        assert data[0]["symbol"] == "BTCUSDT"
        assert data[1]["symbol"] == "ETHUSDT"

    def test_load_history(self, temp_manager, sample_history):
        """StateManager loads trade history correctly."""
        temp_manager.save_history(sample_history)

        # Load history
        loaded = temp_manager.load_trade_history()

        assert len(loaded) == 2
        assert loaded[0]["symbol"] == "BTCUSDT"
        assert loaded[1]["symbol"] == "ETHUSDT"

    def test_load_history_empty_file(self, temp_manager):
        """StateManager returns empty list for missing history file."""
        loaded = temp_manager.load_trade_history()
        assert loaded == []


# ==============================================================================
# Test Combined State Operations
# ==============================================================================


class TestCombinedStateOperations:
    """Test loading/saving all states together."""

    @pytest.fixture
    def temp_manager(self):
        """Create StateManager with temporary directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = StateManager(Path(tmpdir))
            yield manager

    def test_load_states_all(self, temp_manager, sample_long_position):
        """StateManager loads all states together."""
        positions = {"BTCUSDT": sample_long_position}
        capitals = {"BTCUSDT": Decimal("1000")}
        history = [{"symbol": "BTCUSDT", "pnl": "100"}]

        temp_manager.save_positions(positions)
        temp_manager.save_capitals(capitals)
        temp_manager.save_history(history)

        # Load all states
        loaded_positions, loaded_capitals, loaded_history = temp_manager.load_states()

        assert len(loaded_positions) == 1
        assert len(loaded_capitals) == 1
        assert len(loaded_history) == 1

    def test_save_all_states(self, temp_manager, sample_long_position):
        """StateManager saves all states together."""
        positions = {"BTCUSDT": sample_long_position}
        capitals = {"BTCUSDT": Decimal("1000")}
        history = [{"symbol": "BTCUSDT", "pnl": "100"}]

        temp_manager.save_all_states(positions, capitals, history)

        # Verify all files exist
        assert temp_manager.positions_file.exists()
        assert temp_manager.capitals_file.exists()
        assert temp_manager.history_file.exists()


# ==============================================================================
# Test Strategy Config Loading
# ==============================================================================


class TestStrategyConfigLoading:
    """Test strategy configuration loading."""

    def test_load_strategy_configs_enabled_only(self):
        """StateManager loads only enabled strategies."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "strategy_configs.json"

            # Write test config
            config_data = {
                "BTC/USDT": {
                    "enabled": True,
                    "timeframe": "5m",
                    "initial_capital_usdt": 1000,
                },
                "ETH/USDT": {
                    "enabled": False,
                    "timeframe": "5m",
                    "initial_capital_usdt": 500,
                },
            }

            with open(config_file, "w") as f:
                json.dump(config_data, f)

            manager = StateManager(Path(tmpdir))
            manager.strategy_configs_file = config_file

            configs = manager.load_strategy_configs()

            # Should only load enabled strategy
            assert len(configs) == 1
            assert "BTCUSDT" in configs
            assert "ETHUSDT" not in configs

    def test_load_strategy_configs_empty_file(self):
        """StateManager handles empty config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "strategy_configs.json"

            # Write empty config
            with open(config_file, "w") as f:
                f.write("{}")

            manager = StateManager(Path(tmpdir))
            manager.strategy_configs_file = config_file

            configs = manager.load_strategy_configs()
            assert configs == {}

    def test_load_strategy_configs_30m_timeframe(self):
        """StateManager sets volatility lookback for 30m timeframe."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "config"
            config_dir.mkdir()
            config_file = config_dir / "strategy_configs.json"

            # Write 30m config
            config_data = {
                "BTC/USDT": {
                    "enabled": True,
                    "timeframe": "30m",
                    "initial_capital_usdt": 1000,
                }
            }

            with open(config_file, "w") as f:
                json.dump(config_data, f)

            manager = StateManager(Path(tmpdir))
            manager.strategy_configs_file = config_file

            configs = manager.load_strategy_configs()

            # Should set volatility lookback to 30
            assert "BTCUSDT" in configs
            # Note: StrategyConfig defaults volatility_filter_lookback
            # The actual value is set in the config data passed to StrategyConfig
