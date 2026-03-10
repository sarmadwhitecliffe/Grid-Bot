"""
tests/test_settings.py
-----------------------
Unit tests for config/settings.py — Pydantic BaseSettings loading and
validation. No live exchange calls are made.
"""

from pathlib import Path

import pytest

from config.settings import GridBotSettings


class TestGridBotSettingsDefaults:
    def test_grid_type_default(self, base_settings: GridBotSettings) -> None:
        """GRID_TYPE must default to 'geometric' from YAML."""
        assert base_settings.GRID_TYPE == "geometric"

    def test_num_grids_positive(self, base_settings: GridBotSettings) -> None:
        """NUM_GRIDS_UP and NUM_GRIDS_DOWN must be positive integers."""
        assert base_settings.NUM_GRIDS_UP > 0
        assert base_settings.NUM_GRIDS_DOWN > 0

    def test_grid_spacing_positive(self, base_settings: GridBotSettings) -> None:
        """GRID_SPACING_PCT must be a non-zero positive float."""
        assert base_settings.GRID_SPACING_PCT > 0.0

    def test_order_size_quote_positive(self, base_settings: GridBotSettings) -> None:
        """ORDER_SIZE_QUOTE must be positive."""
        assert base_settings.ORDER_SIZE_QUOTE > 0.0

    def test_adx_threshold_reasonable(self, base_settings: GridBotSettings) -> None:
        """ADX_THRESHOLD should be in the range (0, 100)."""
        assert 0 < base_settings.ADX_THRESHOLD < 100

    def test_max_drawdown_fractional(self, base_settings: GridBotSettings) -> None:
        """MAX_DRAWDOWN_PCT must be expressed as a fraction (0, 1)."""
        assert 0 < base_settings.MAX_DRAWDOWN_PCT < 1

    def test_state_file_is_path(self, base_settings: GridBotSettings) -> None:
        """STATE_FILE must be a Path object."""
        assert isinstance(base_settings.STATE_FILE, Path)

    def test_ohlcv_cache_dir_is_path(self, base_settings: GridBotSettings) -> None:
        """OHLCV_CACHE_DIR must be a Path object."""
        assert isinstance(base_settings.OHLCV_CACHE_DIR, Path)


class TestGridBotSettingsValidation:
    def test_invalid_market_type_raises(self) -> None:
        """Market type other than 'spot'/'futures' should raise ValueError."""
        with pytest.raises(Exception):
            GridBotSettings(
                EXCHANGE_ID="binance",
                MARKET_TYPE="options",  # invalid
                API_KEY="k",
                API_SECRET="s",
            )

    def test_invalid_grid_type_raises(self) -> None:
        """Grid type other than 'geometric'/'arithmetic' should raise."""
        with pytest.raises(Exception):
            GridBotSettings(
                EXCHANGE_ID="binance",
                MARKET_TYPE="spot",
                API_KEY="k",
                API_SECRET="s",
                GRID_TYPE="random",  # invalid
            )

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variable should override YAML default."""
        monkeypatch.setenv("NUM_GRIDS_UP", "25")
        s = GridBotSettings(
            EXCHANGE_ID="binance",
            MARKET_TYPE="spot",
            API_KEY="k",
            API_SECRET="s",
        )
        assert s.NUM_GRIDS_UP == 25
