"""
Custom exceptions for the trading bot.

Provides a clear exception hierarchy for different error scenarios,
making error handling more precise and debugging easier.
"""


class TradingBotError(Exception):
    """Base exception for all trading bot errors."""

    pass


class ConfigurationError(TradingBotError):
    """Raised when configuration is invalid or missing."""

    pass


class ExchangeError(TradingBotError):
    """Raised when exchange API operations fail."""

    pass


class PositionError(TradingBotError):
    """Raised when position operations fail."""

    pass


class KillSwitchActiveError(TradingBotError):
    """
    Raised when attempting to trade a symbol with active kill switch.

    This indicates the symbol has been disabled due to poor performance
    (typically 30%+ drawdown) by the adaptive risk manager.
    """

    pass


class InsufficientCapitalError(TradingBotError):
    """Raised when insufficient capital is available for a trade."""

    pass


class OrderExecutionError(ExchangeError):
    """Raised when order execution fails."""

    pass


class StateLoadError(TradingBotError):
    """Raised when loading state from disk fails."""

    pass


class StateSaveError(TradingBotError):
    """Raised when saving state to disk fails."""

    pass


class ValidationError(TradingBotError):
    """Raised when data validation fails."""

    pass
