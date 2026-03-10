"""
TradingBot V2 - Modular Architecture with Adaptive Risk Management

This is a complete refactoring of the monolithic bot.py into a clean,
testable, maintainable modular architecture.

Key improvements:
- Separated concerns (position, exit, execution, risk, persistence)
- Integrated adaptive risk management
- Hot reload support for configuration changes
- Comprehensive test coverage
- Type safety throughout
"""

__version__ = "2.0.0"
__author__ = "NonML Trading System"
