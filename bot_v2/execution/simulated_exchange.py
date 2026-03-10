"""
Simulated Exchange Implementation - Testing Mode

Implements the ExchangeInterface for testing and development.
Uses public Binance API for real market data but simulates order execution.
"""

import logging
import os
import time
from itertools import count
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import ccxt
import ccxt.async_support as ccxt_async
import pandas as pd

from bot_v2.execution.exchange_interface import ExchangeInterface
from bot_v2.execution.market_data_cache import MarketDataCache
from bot_v2.models.enums import TradeSide
from bot_v2.models.exceptions import OrderExecutionError

logger = logging.getLogger(__name__)


async def resilient_call(func, max_retries: int = 3, delay: float = 1.0):
    """
    Execute a function with automatic retry logic.

    Args:
        func: Callable to execute (typically a lambda wrapping an async call)
        max_retries: Maximum number of retry attempts
        delay: Delay between retries in seconds

    Returns:
        Result of the function call

    Raises:
        Exception: If all retries fail
    """
    import asyncio

    for attempt in range(max_retries):
        try:
            return await func()
        except (ccxt_async.NetworkError, ccxt_async.RequestTimeout) as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"LOCAL_SIM: Network error on attempt {attempt + 1}/{max_retries}: {e}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    f"LOCAL_SIM: All {max_retries} attempts failed. Last error: {e}"
                )
                raise
        except ccxt_async.ExchangeError as e:
            logger.error(f"LOCAL_SIM: Exchange error (non-retryable): {e}")
            raise


class SimulatedExchange(ExchangeInterface):
    """
    Simulated exchange for testing and development.

    Features:
    - Uses public Binance API for real market prices
    - Simulates order execution with configurable fees
    - No capital at risk
    - Conservative rate limiting (1000ms)
    """

    def __init__(
        self,
        fee: Decimal,
        cache: Optional[MarketDataCache] = None,
        slippage_pct: float = 0.0,
    ) -> None:
        """
        Initialize simulated exchange.

        Args:
            fee: Trading fee as a decimal (e.g., 0.0004 for 0.04%)
            cache: Optional MarketDataCache instance (created if None)
        """
        self._fee = fee
        self._slippage_pct = slippage_pct
        self.open_sim_orders: Dict[str, Dict[str, Any]] = {}
        self._order_id_counter = count()

        # Initialize cache (Phase 2: Performance Optimization)
        enable_cache = os.getenv("ENABLE_MARKET_DATA_CACHE", "true").lower() == "true"
        cache_ttl = int(os.getenv("MARKET_DATA_CACHE_TTL", "30"))

        if enable_cache:
            self.cache = (
                cache if cache is not None else MarketDataCache(default_ttl=cache_ttl)
            )
            logger.info(f"Market data cache enabled: TTL={cache_ttl}s")
        else:
            self.cache = None
            logger.info("Market data cache disabled")

        self.public_exchange = ccxt_async.binance(
            {
                "options": {"defaultType": "future"},
                "enableRateLimit": True,  # Enable rate limiting for public API
                "rateLimit": 50,  # Fast rate limit for simulation mode (real exchange would use 1000)
            }
        )
        logger.info(f"SimulatedExchange initialized with fee: {fee}")

    def _next_order_id(self, prefix: str) -> str:
        """Generate collision-resistant simulated order IDs."""
        return f"{prefix}-{time.time_ns()}-{next(self._order_id_counter)}"

    async def setup(self) -> bool:
        """
        Initialize the simulated exchange.

        Returns:
            Always True (simulated exchange always ready)
        """
        try:
            # Pre-load markets to avoid latency on first request
            await resilient_call(lambda: self.public_exchange.load_markets())
            logger.info(
                "LOCAL_SIM: Simulated exchange setup complete (markets loaded)."
            )
            return True
        except Exception as e:
            logger.warning(f"LOCAL_SIM: Failed to load markets: {e}")
            return True  # Continue anyway, will try to load on demand

    async def close(self) -> None:
        """Close the public exchange connection."""
        await self.public_exchange.close()
        logger.info("LOCAL_SIM: Simulated exchange connection closed.")

    def format_market_id(self, symbol: str) -> Optional[str]:
        """
        Return symbol as-is for simulated exchange.

        Args:
            symbol: Symbol in standard format (e.g., "BTC/USDT")

        Returns:
            Same symbol (simulated exchange doesn't need conversion)
        """
        return symbol

    async def get_market_price(self, market_id: str) -> Optional[Decimal]:
        """
        Get current market price using public exchange data.
        Uses cache if enabled (Phase 2 optimization).

        Args:
            market_id: Market identifier (symbol)

        Returns:
            Current market price or None if fetch fails
        """
        # Check cache first (Phase 2)
        if self.cache:
            cached_price = self.cache.get_price(market_id)
            if cached_price is not None:
                return cached_price

        # Cache miss or disabled - fetch from exchange
        try:
            ticker = await resilient_call(
                lambda: self.public_exchange.fetch_ticker(market_id)
            )

            if not ticker or "last" not in ticker or ticker["last"] is None:
                logger.warning(
                    f"LOCAL_SIM: Invalid or empty ticker received for {market_id}. "
                    f"Ticker: {ticker}"
                )
                return None

            price = Decimal(str(ticker["last"]))

            # Cache the price (Phase 2)
            if self.cache:
                self.cache.set_price(market_id, price)

            return price

        except (ccxt.NetworkError, ccxt.ExchangeError, ccxt.RequestTimeout) as e:
            logger.error(
                f"LOCAL_SIM: Persistent network/exchange error for {market_id}: {e}"
            )
            return None
        except Exception as e:
            logger.error(
                f"LOCAL_SIM: Unexpected error fetching price for {market_id}: {e}",
                exc_info=True,
            )
            return None

    async def create_market_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Simulate the creation of a market order.

        Args:
            market_id: Market identifier (symbol)
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            params: Additional parameters

        Returns:
            Simulated order response dict with 'status': 'closed' (filled)
        """
        if params is None:
            params = {}

        # Fetch current price for simulation
        price = await self.get_market_price(market_id)
        if price is None:
            raise OrderExecutionError(f"Could not fetch price for {market_id}")

        logger.info(
            f"LOCAL_SIM: Creating simulated market {side.value} order: {amount} @ {price} for {market_id}"
        )

        simulated_order = {
            "info": {"simulated": True},
            "id": self._next_order_id("sim-market"),
            "timestamp": int(time.time() * 1000),
            "datetime": datetime.now(timezone.utc).isoformat(),
            "symbol": market_id,
            "type": "market",
            "side": side.value,
            "price": str(price),
            "amount": str(amount),
            "filled": str(amount),
            "remaining": "0",
            "status": "closed",
            "fee": {
                "cost": str(amount * price * self._fee),
                "currency": "USDT",
            },
        }
        return simulated_order

    async def create_limit_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        price: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Simulate the creation of a limit order.

        Args:
            market_id: Market identifier (symbol)
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            price: Order price
            params: Additional parameters

        Returns:
            Simulated order response dict with 'status': 'open'
        """
        if params is None:
            params = {}

        logger.info(
            f"LOCAL_SIM: Creating simulated limit {side.value} order: {amount} @ {price} for {market_id}"
        )

        simulated_order = {
            "info": {"simulated": True},
            "id": self._next_order_id("sim-limit"),
            "timestamp": int(time.time() * 1000),
            "datetime": datetime.now(timezone.utc).isoformat(),
            "symbol": market_id,
            "type": "limit",
            "side": side.value,
            "price": str(price),
            "amount": str(amount),
            "filled": "0",
            "remaining": str(amount),
            "status": "open",
            "fee": {
                "cost": "0",
                "currency": "USDT",
            },
        }
        # Store for fill simulation
        self.open_sim_orders[simulated_order["id"]] = simulated_order
        return simulated_order

    async def check_fills(self, market_id: str, current_price: Decimal) -> List[str]:
        """
        Check if any open simulated orders should be filled at the current price.
        Returns a list of filled order IDs.
        """
        filled_ids = []
        for oid, order in list(self.open_sim_orders.items()):
            if order["symbol"] != market_id or order["status"] != "open":
                continue

            order_price = Decimal(order["price"])
            side = order["side"].lower()

            should_fill = False
            if side == "buy" and current_price <= order_price:
                should_fill = True
            elif side == "sell" and current_price >= order_price:
                should_fill = True

            if should_fill:
                logger.info(
                    f"LOCAL_SIM: Order FILLED: {oid} {side} @ {order_price} (Market: {current_price})"
                )
                order["status"] = "closed"
                order["filled"] = order["amount"]
                order["remaining"] = "0"
                order["average"] = order["price"]
                # Calculate fee
                cost = Decimal(order["amount"]) * order_price
                order["cost"] = str(cost)
                order["fee"]["cost"] = str(cost * self._fee)
                filled_ids.append(oid)
                # Remove from open tracking
                del self.open_sim_orders[oid]

        return filled_ids

    async def fetch_ohlcv(
        self, market_id: str, timeframe: str, limit: int
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data using public exchange.
        Uses cache if enabled (Phase 2 optimization).

        Args:
            market_id: Market identifier (symbol)
            timeframe: Timeframe string (e.g., '1m', '5m', '1h')
            limit: Number of candles to fetch

        Returns:
            DataFrame with OHLCV data or None if fetch fails
        """
        # Check cache first (Phase 2)
        if self.cache:
            cached_ohlcv = self.cache.get_ohlcv(market_id, timeframe, limit)
            if cached_ohlcv is not None:
                return cached_ohlcv

        # Cache miss or disabled - fetch from exchange
        try:
            ohlcv = await resilient_call(
                lambda: self.public_exchange.fetch_ohlcv(
                    market_id, timeframe, limit=limit
                )
            )

            if not ohlcv:
                logger.warning(f"LOCAL_SIM: No OHLCV data received for {market_id}")
                return None

            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            # Convert price columns to numeric
            for col in df.columns[1:]:
                df[col] = pd.to_numeric(df[col])

            logger.debug(
                f"LOCAL_SIM: Fetched {len(df)} candles for {market_id} ({timeframe})"
            )

            # Store in cache (Phase 2)
            if self.cache:
                self.cache.set_ohlcv(market_id, timeframe, limit, df)

            return df

        except Exception as e:
            logger.warning(f"LOCAL_SIM: Failed to fetch OHLCV for {market_id}: {e}")
            return None
