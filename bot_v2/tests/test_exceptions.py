"""
Tests for models.exceptions module.
"""

import pytest

from bot_v2.models.exceptions import (
    ConfigurationError,
    ExchangeError,
    InsufficientCapitalError,
    KillSwitchActiveError,
    OrderExecutionError,
    PositionError,
    StateLoadError,
    StateSaveError,
    TradingBotError,
    ValidationError,
)


class TestExceptionHierarchy:
    """Test exception inheritance hierarchy."""

    def test_base_exception(self):
        """Test TradingBotError is the base exception."""
        error = TradingBotError("test error")
        assert isinstance(error, Exception)
        assert str(error) == "test error"

    def test_configuration_error_inherits_base(self):
        """Test ConfigurationError inherits from TradingBotError."""
        error = ConfigurationError("config missing")
        assert isinstance(error, TradingBotError)
        assert isinstance(error, Exception)

    def test_exchange_error_inherits_base(self):
        """Test ExchangeError inherits from TradingBotError."""
        error = ExchangeError("API failed")
        assert isinstance(error, TradingBotError)

    def test_position_error_inherits_base(self):
        """Test PositionError inherits from TradingBotError."""
        error = PositionError("position not found")
        assert isinstance(error, TradingBotError)

    def test_kill_switch_error_inherits_base(self):
        """Test KillSwitchActiveError inherits from TradingBotError."""
        error = KillSwitchActiveError("BTCUSDT disabled")
        assert isinstance(error, TradingBotError)

    def test_insufficient_capital_inherits_base(self):
        """Test InsufficientCapitalError inherits from TradingBotError."""
        error = InsufficientCapitalError("not enough capital")
        assert isinstance(error, TradingBotError)


class TestExceptionRaising:
    """Test exceptions can be raised and caught properly."""

    def test_raise_configuration_error(self):
        """Test raising ConfigurationError."""
        with pytest.raises(ConfigurationError) as exc_info:
            raise ConfigurationError("Missing API key")
        assert "Missing API key" in str(exc_info.value)

    def test_raise_kill_switch_error(self):
        """Test raising KillSwitchActiveError."""
        with pytest.raises(KillSwitchActiveError) as exc_info:
            raise KillSwitchActiveError("BTCUSDT kill switch active")
        assert "kill switch" in str(exc_info.value).lower()

    def test_catch_base_exception(self):
        """Test catching specific exceptions as base TradingBotError."""
        with pytest.raises(TradingBotError):
            raise ConfigurationError("test")

    def test_order_execution_error_inherits_exchange_error(self):
        """Test OrderExecutionError inherits from ExchangeError."""
        error = OrderExecutionError("order failed")
        assert isinstance(error, ExchangeError)
        assert isinstance(error, TradingBotError)


class TestStateExceptions:
    """Test state persistence exceptions."""

    def test_state_load_error(self):
        """Test StateLoadError."""
        with pytest.raises(StateLoadError):
            raise StateLoadError("Failed to load state")

    def test_state_save_error(self):
        """Test StateSaveError."""
        with pytest.raises(StateSaveError):
            raise StateSaveError("Failed to save state")


class TestValidationError:
    """Test ValidationError."""

    def test_validation_error(self):
        """Test ValidationError can be raised."""
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("Invalid position size")
        assert "Invalid position size" in str(exc_info.value)
