"""
Exchange Interface - Abstract Base Class

Defines the contract for all exchange implementations (live, simulated, paper).
Extracted from bot.py ExchangeInterface class.
"""

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, Optional

import pandas as pd

from bot_v2.models.enums import TradeSide


class ExchangeInterface(ABC):
    """
    Abstract base class for exchange implementations.

    All exchange types (live, simulated, paper) must implement this interface.
    This ensures consistent behavior across different execution modes.
    """

    @abstractmethod
    async def setup(self) -> bool:
        """
        Initialize the exchange connection.

        Returns:
            True if setup successful, False otherwise
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Close the exchange connection and cleanup resources."""
        raise NotImplementedError

    @abstractmethod
    def format_market_id(self, symbol: str) -> Optional[str]:
        """
        Convert symbol to exchange-specific market ID.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")

        Returns:
            Exchange-specific market ID or None if invalid
        """
        raise NotImplementedError

    @abstractmethod
    async def get_market_price(self, market_id: str) -> Optional[Decimal]:
        """
        Get current market price for a symbol.

        Args:
            market_id: Exchange-specific market identifier

        Returns:
            Current market price or None if unavailable
        """
        raise NotImplementedError

    @abstractmethod
    async def create_market_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a market order.

        Args:
            market_id: Exchange-specific market identifier
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            params: Additional exchange-specific parameters

        Returns:
            Order response dict with id, status, filled, etc.

        Raises:
            OrderExecutionError: If order creation fails
        """
        raise NotImplementedError

    @abstractmethod
    async def create_limit_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        price: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a limit order.

        Args:
            market_id: Exchange-specific market identifier
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            price: Order price
            params: Additional exchange-specific parameters

        Returns:
            Order response dict with id, status, etc.

        Raises:
            OrderExecutionError: If order creation fails
        """
        raise NotImplementedError

    @abstractmethod
    async def fetch_ohlcv(
        self, market_id: str, timeframe: str, limit: int
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV (candlestick) data.

        Args:
            market_id: Exchange-specific market identifier
            timeframe: Candlestick timeframe (e.g., "30m", "1h")
            limit: Number of candles to fetch

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
            or None if fetch fails
        """
        raise NotImplementedError
