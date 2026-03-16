"""
Live Exchange Implementation - CCXT-based Real Trading

Implements the ExchangeInterface for live trading using CCXT library.
Includes rate limiting, retry logic, and comprehensive error handling.
"""

import logging
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt_async
import pandas as pd

from config.settings import settings
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
                    f"Network error on attempt {attempt + 1}/{max_retries}: {e}. "
                    f"Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"All {max_retries} attempts failed. Last error: {e}")
                raise
        except ccxt_async.ExchangeError as e:
            logger.error(f"Exchange error (non-retryable): {e}")
            raise


class LiveExchange(ExchangeInterface):
    """
    Live exchange implementation using CCXT.

    Features:
    - Automatic rate limiting (1200ms between requests)
    - Retry logic for network errors
    - Futures trading support
    - Comprehensive error handling
    """

    def __init__(
        self,
        name: str,
        key: Optional[str],
        secret: Optional[str],
        cache: Optional[MarketDataCache] = None,
        order_state_manager: Optional[Any] = None,
    ) -> None:
        """
        Initialize live exchange connection.

        Args:
            name: Exchange name (e.g., 'binance', 'bybit')
            key: API key for authenticated requests
            secret: API secret for authenticated requests
            cache: Optional shared MarketDataCache instance (Phase 2 optimization)
            order_state_manager: Unified state manager

        Raises:
            AttributeError: If exchange name is not found in CCXT
        """
        try:
            self.order_state_manager = order_state_manager
            exchange_class = getattr(ccxt_async, name)
            market_type = settings.MARKET_TYPE.rstrip('s') if settings.MARKET_TYPE.endswith('s') else settings.MARKET_TYPE
            config = {
                "options": {"defaultType": market_type},
                "apiKey": key,
                "secret": secret,
                "enableRateLimit": True,  # Enable automatic rate limiting
                "rateLimit": 50,  # Aggressive rate limit (ms between requests) - relying on CCXT token bucket
            }
            self.exchange = exchange_class(config)

            # Initialize public exchange for market data (no keys) to avoid IP restrictions on read-only data
            public_config = {
                "options": {"defaultType": market_type},
                "enableRateLimit": True,
                "rateLimit": 50,
            }
            self.public_exchange = exchange_class(public_config)

            # Initialize cache (Phase 2 optimization)
            enable_cache = (
                os.getenv("ENABLE_MARKET_DATA_CACHE", "true").lower() == "true"
            )
            if enable_cache:
                cache_ttl = int(os.getenv("MARKET_DATA_CACHE_TTL", "30"))
                self.cache = cache if cache else MarketDataCache(default_ttl=cache_ttl)
                logger.info(
                    f"LiveExchange initialized for {name} with cache (TTL={cache_ttl}s)"
                )
            else:
                self.cache = None
                logger.info(f"LiveExchange initialized for {name} without cache")
        except AttributeError:
            logger.critical(f"Exchange '{name}' not found in CCXT.")
            raise

    async def setup(self) -> bool:
        """
        Initialize the live exchange connection.

        Loads market data and verifies connection.

        Returns:
            True if setup successful, False otherwise
        """
        success = False
        try:
            # Try to load authenticated markets
            await resilient_call(lambda: self.exchange.load_markets())
            success = True
        except Exception as e:
            logger.warning(
                f"Failed to load authenticated markets (IP restriction?): {e}"
            )

        try:
            # Always try to load public markets (fallback)
            await resilient_call(lambda: self.public_exchange.load_markets())
            logger.info(
                f"Live exchange '{self.exchange.id}' initialized successfully (public markets loaded)."
            )
            return True
        except Exception as e:
            logger.critical(
                f"Failed to setup live exchange (public markets): {e}", exc_info=True
            )
            return success  # Return True if at least authenticated worked (unlikely if public failed)

    async def close(self) -> None:
        """Close the exchange connection and cleanup resources."""
        await self.exchange.close()
        await self.public_exchange.close()
        logger.info(f"Live exchange '{self.exchange.id}' connection closed.")

    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a trading pair.

        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")
            leverage: Leverage multiplier (e.g., 1 for 1x, 5 for 5x)

        Returns:
            True if successful, False otherwise
        """
        try:
            market_id = self.format_market_id(symbol)
            if not market_id:
                logger.error(f"Cannot set leverage for invalid symbol: {symbol}")
                return False

            await resilient_call(
                lambda: self.exchange.set_leverage(leverage, market_id)
            )
            logger.info(f"Leverage set to {leverage}x for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Failed to set leverage for {symbol}: {e}", exc_info=True)
            return False

    async def _populate_order_fees(self, order: Dict[str, Any], market_id: str) -> None:
        """
        Populate fee information in order dict by fetching trades.

        Binance Futures does not include fee data in order responses,
        so we need to fetch the actual trades that filled the order.

        Args:
            order: Order dict to populate with fee data
            market_id: Market identifier for the order
        """
        order_id = order.get("id")
        if not order_id:
            return

        try:
            # Fetch recent trades for this symbol
            trades = await resilient_call(
                lambda: self.exchange.fetch_my_trades(market_id, limit=50)
            )

            # Find trades matching this order
            matching_trades = [
                t for t in trades if str(t.get("order")) == str(order_id)
            ]

            if matching_trades:
                # Calculate total fee from all matching trades
                total_fee_cost = sum(
                    (
                        float(t.get("fee", {}).get("cost", 0))
                        if isinstance(t.get("fee"), dict)
                        else 0
                    )
                    for t in matching_trades
                )
                fee_currency = matching_trades[0].get("fee", {}).get("currency", "USDT")

                # Populate order with fee data
                order["fee"] = {"cost": total_fee_cost, "currency": fee_currency}
                order["fees"] = [t.get("fee") for t in matching_trades if t.get("fee")]
                order["trades"] = matching_trades

                logger.debug(
                    f"Populated fee data for order {order_id}: "
                    f"{total_fee_cost} {fee_currency} from {len(matching_trades)} trades"
                )
            else:
                logger.warning(f"No matching trades found for order {order_id}")

        except Exception as e:
            logger.error(f"Failed to fetch trades for order {order_id}: {e}")
            raise

    def format_market_id(self, symbol: str) -> Optional[str]:
        """
        Convert symbol to exchange-specific market ID.

        Args:
            symbol: Symbol in standard format (e.g., "BTC/USDT")

        Returns:
            Exchange-specific market ID or None if symbol invalid
        """
        try:
            # Try authenticated exchange first
            if self.exchange.markets and symbol in self.exchange.markets:
                return self.exchange.market(symbol)["id"]

            # Fallback to public exchange (useful for read-only/restricted IP modes)
            if (
                self.public_exchange
                and self.public_exchange.markets
                and symbol in self.public_exchange.markets
            ):
                return self.public_exchange.market(symbol)["id"]

            # If markets not loaded, try to access anyway (might trigger auto-load if supported, but unlikely in async)
            return self.exchange.market(symbol)["id"]
        except (ccxt_async.BadSymbol, KeyError):
            # Try public exchange as last resort if exception occurred
            try:
                if self.public_exchange:
                    return self.public_exchange.market(symbol)["id"]
            except Exception:
                pass

            logger.warning(f"Invalid symbol for live exchange: {symbol}")
            return None

    async def get_market_price(self, market_id: str) -> Optional[Decimal]:
        """
        Get current market price.
        Uses cache if enabled (Phase 2 optimization).

        Args:
            market_id: Exchange-specific market identifier

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
                logger.warning(f"Invalid ticker received for {market_id}: {ticker}")
                return None

            price = Decimal(str(ticker["last"]))

            # Store in cache (Phase 2)
            if self.cache:
                self.cache.set_price(market_id, price)

            return price
        except Exception as e:
            logger.error(f"Price fetch failed for {market_id}: {e}", exc_info=True)
            return None

    async def create_market_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        params: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Create a market order on the live exchange.

        Args:
            market_id: Exchange-specific market identifier
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            params: Additional exchange-specific parameters

        Returns:
            Order response dict from exchange

        Raises:
            OrderExecutionError: If order creation fails
        """
        if params is None:
            params = {}

        logger.info(f"Creating live market order: {market_id} {side.value} {amount}")

        try:
            # STEP 1: Place order (POST)
            order = await resilient_call(
                lambda: self.exchange.create_market_order(
                    market_id, side.value, float(amount), params
                )
            )

            if not order:
                raise OrderExecutionError(f"Order for {market_id} returned None.")

            return await self._verify_and_populate_order(order, market_id)

        except Exception as e:
            logger.error(f"Order creation failed for {market_id}: {e}", exc_info=True)
            raise OrderExecutionError(
                f"Order for {market_id} failed. Original error: {e}"
            ) from e

    async def create_limit_order(
        self,
        market_id: str,
        side: TradeSide,
        amount: Decimal,
        price: Decimal,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a limit order on the live exchange.

        Args:
            market_id: Exchange-specific market identifier
            side: Order side (BUY or SELL)
            amount: Order amount in base currency
            price: Order price
            params: Additional exchange-specific parameters

        Returns:
            Order response dict from exchange

        Raises:
            OrderExecutionError: If order creation fails
        """
        if params is None:
            params = {}

        logger.info(
            f"Creating live limit order: {market_id} {side.value} {amount} @ {price}"
        )

        try:
            # STEP 1: Place order (POST)
            order = await resilient_call(
                lambda: self.exchange.create_limit_order(
                    market_id, side.value, float(amount), float(price), params
                )
            )

            if not order:
                raise OrderExecutionError(f"Order for {market_id} returned None.")

            return await self._verify_and_populate_order(order, market_id)

        except Exception as e:
            logger.error(
                f"Limit order creation failed for {market_id}: {e}", exc_info=True
            )
            raise OrderExecutionError(
                f"Limit order for {market_id} failed. Original error: {e}"
            ) from e

    async def _verify_and_populate_order(
        self, order: Dict[str, Any], market_id: str
    ) -> Dict[str, Any]:
        """
        Verify order status and populate fee information.
        Helper method shared by market and limit order creation.
        """
        order_id = order.get("id")
        if not order_id:
            logger.error(f"Order response missing 'id' field: {order}")
            raise OrderExecutionError("Order response missing 'id' field")

        # STEP 2: Verify order immediately (GET)
        verify_immediately = (
            os.getenv("VERIFY_ORDER_IMMEDIATELY", "false").lower() == "true"
        )

        if verify_immediately:
            try:
                verified_order = await resilient_call(
                    lambda: self.exchange.fetch_order(order_id, market_id)
                )

                if not verified_order:
                    logger.error(f"Order verification returned None for {order_id}")
                    order["_verification_status"] = "UNVERIFIED"
                    order["_verification_error"] = "Verification returned None"
                else:
                    status = verified_order.get("status", "").upper()
                    if status in ["NEW", "FILLED", "PARTIALLY_FILLED", "CLOSED", "OPEN"]:
                        logger.info(
                            f"Live order VERIFIED: {order_id} - status={status}, "
                            f"filled={verified_order.get('filled', 0)}/{verified_order.get('amount', 0)}"
                        )
                        verified_order["_verification_status"] = "VERIFIED"
                        order = verified_order  # Replace with verified data
                    else:
                        logger.warning(
                            f"Live order verification returned unexpected status: "
                            f"{order_id} status={status}"
                        )
                        order["_verification_status"] = "UNVERIFIED"
                        order["_verification_error"] = f"Unexpected status: {status}"

            except Exception as verify_error:
                logger.error(
                    f"Order verification FAILED for {order_id}: {verify_error}",
                    exc_info=True,
                )
                order["_verification_status"] = "UNVERIFIED"
                order["_verification_error"] = str(verify_error)
        else:
            order["_verification_status"] = "SKIPPED"

        # Fetch actual fee from trades
        fetch_fees = os.getenv("FETCH_TRADES_FOR_FEES", "false").lower() == "true"
        if fetch_fees:
            try:
                await self._populate_order_fees(order, market_id)
            except Exception as e:
                logger.warning(f"Failed to fetch fee data for order {order_id}: {e}")

        return order

    async def get_position_amount(self, market_id: str) -> Optional[Decimal]:
        """
        Query exchange for actual open position amount.

        Args:
            market_id: Exchange-specific market identifier

        Returns:
            Actual position amount on exchange or None if query fails
        """
        try:
            positions = await resilient_call(
                lambda: self.exchange.fetch_positions([market_id])
            )

            for pos in positions:
                if pos.get("symbol") == market_id:
                    # Try multiple fields for position amount (different exchanges use different fields)
                    contracts = float(pos.get("contracts", 0))

                    # If contracts is 0, try checking raw info.positionAmt (Binance specific)
                    if contracts == 0 and "info" in pos:
                        position_amt = float(pos["info"].get("positionAmt", 0))
                        if position_amt != 0:
                            logger.debug(
                                f"[{market_id}] Position found in info.positionAmt: {position_amt} "
                                f"(contracts field was 0)"
                            )
                            return Decimal(str(abs(position_amt)))

                    if contracts != 0:  # Non-zero position exists
                        logger.debug(
                            f"[{market_id}] Position found: {contracts} contracts"
                        )
                        return Decimal(str(abs(contracts)))

            # No position found or position is zero
            logger.debug(f"[{market_id}] No open position found on exchange")
            return Decimal("0")

        except Exception as e:
            logger.error(
                f"Failed to fetch position for {market_id}: {e}", exc_info=True
            )
            return None

    async def fetch_ohlcv(
        self, market_id: str, timeframe: str, limit: int
    ) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV data from the live exchange.
        Uses cache if enabled (Phase 2 optimization).

        Args:
            market_id: Exchange-specific market identifier
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
                logger.warning(f"No OHLCV data received for {market_id}")
                return None

            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

            # Convert price columns to numeric
            for col in df.columns[1:]:
                df[col] = pd.to_numeric(df[col])

            logger.debug(f"Fetched {len(df)} candles for {market_id} ({timeframe})")

            # Store in cache (Phase 2)
            if self.cache:
                self.cache.set_ohlcv(market_id, timeframe, limit, df)

            return df

        except Exception as e:
            logger.error(f"Failed to fetch OHLCV for {market_id}: {e}", exc_info=True)
            return None

    async def check_fills(
        self,
        market_id: str,
        current_price: Optional[Decimal] = None,
        candle_high: Optional[Decimal] = None,
        candle_low: Optional[Decimal] = None,
        candle_timestamp: Optional[int] = None,
    ) -> List[str]:
        """
        Check for filled orders by fetching recent trades from the exchange.

        This method is used by the grid orchestrator to detect fills for live trading.
        It fetches recent trades and extracts the order IDs that were filled.

        Args:
            market_id: Exchange-specific market identifier (symbol)
            current_price: Current market price (unused for live, kept for interface compatibility)
            candle_high: High price of current candle (unused for live)
            candle_low: Low price of current candle (unused for live)
            candle_timestamp: Timestamp of the candle (ms) - unused for live, kept for interface compatibility

        Returns:
            List of filled order IDs that can be matched against grid_order_ids
        """
        filled_ids: List[str] = []

        try:
            # Fetch recent trades for this symbol
            trades = await resilient_call(
                lambda: self.exchange.fetch_my_trades(market_id, limit=100)
            )

            if not trades:
                logger.debug(f"No recent trades found for {market_id}")
                return filled_ids

            # Extract order IDs from filled trades
            for trade in trades:
                order_id = trade.get("order")
                if order_id:
                    filled_ids.append(str(order_id))

            if filled_ids:
                logger.debug(
                    f"[{market_id}] Found {len(filled_ids)} filled order(s) in recent trades"
                )

            return filled_ids

        except Exception as e:
            logger.error(
                f"[{market_id}] Failed to fetch trades for fill detection: {e}",
                exc_info=True,
            )
            return filled_ids
