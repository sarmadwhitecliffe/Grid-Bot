"""
Trade Execution Layer

This package handles order execution and exchange interaction.
Supports multiple exchange types:
- LiveExchange: Real trading via CCXT
- SimulatedExchange: Testing with simulated orders
- OrderManager: Order lifecycle management

Components:
- exchange_interface: Abstract base class for exchanges
- live_exchange: CCXT-based live trading
- simulated_exchange: Testing mode with simulated orders
- order_manager: Order creation and tracking
"""

from bot_v2.execution.exchange_interface import ExchangeInterface
from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.execution.order_manager import OrderManager
from bot_v2.execution.simulated_exchange import SimulatedExchange

__all__ = ["ExchangeInterface", "LiveExchange", "SimulatedExchange", "OrderManager"]
