"""
src/exchange/exchange_client.py
-------------------------------
Async ccxt wrapper for all exchange I/O.
Supports Spot and Futures (linear perpetuals) markets.
All calls use enableRateLimit=True and exponential backoff retry.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import ccxt.async_support as ccxt

from config.settings import GridBotSettings

logger = logging.getLogger(__name__)

# Exponential backoff delays in seconds (3 attempts total).
RETRY_DELAYS: List[int] = [1, 2, 5]


class ExchangeClient:
    """
    Async ccxt wrapper for limit order operations.

    Provides strongly-typed methods for all exchange interactions needed
    by the Grid Bot: place, cancel, poll, fetch ticker, fetch balance,
    and fetch historical OHLCV.
    """

    def __init__(self, settings: GridBotSettings) -> None:
        """
        Initialise the ccxt exchange instance.

        Args:
            settings: Validated GridBotSettings containing credentials
                      and exchange configuration.
        """
        exchange_class = getattr(ccxt, settings.EXCHANGE_ID)
        params: Dict[str, Any] = {
            "apiKey": settings.API_KEY,
            "secret": settings.API_SECRET,
            "enableRateLimit": True,
        }
        if settings.MARKET_TYPE == "futures":
            params.setdefault("options", {})["defaultType"] = "future"

        if settings.TESTNET:
            params.setdefault("options", {})["testnet"] = True

        self.exchange: ccxt.Exchange = exchange_class(params)
        self.symbol: str = settings.SYMBOL

    async def load_markets(self) -> None:
        """
        Load market metadata from the exchange.

        Must be called once before any trading operations so that
        price_step and amount_step precision data are available.
        """
        await self._retry(self.exchange.load_markets)

    async def get_ticker(self) -> Dict[str, Any]:
        """
        Fetch latest ticker for the configured symbol.

        Returns:
            dict: ccxt ticker containing 'last', 'bid', 'ask', etc.
        """
        return await self._retry(self.exchange.fetch_ticker, self.symbol)

    async def place_limit_order(
        self,
        side: str,
        price: float,
        amount: float,
        position_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Place a limit order on the exchange.

        Args:
            side:          'buy' or 'sell'
            price:         Limit price in quote currency
            amount:        Quantity in base currency
            position_side: 'LONG' or 'SHORT' (for futures hedge mode)

        Returns:
            dict: ccxt order dict containing 'id', 'status', 'price', 'amount'.
        """
        logger.info(
            "Placing %s limit order (%s): %.6f @ %.4f",
            side,
            position_side or "N/A",
            amount,
            price,
        )
        params = {}
        if position_side:
            params["positionSide"] = position_side

        return await self._retry(
            self.exchange.create_limit_order,
            self.symbol,
            side,
            amount,
            price,
            params,
        )

    async def set_leverage(self, leverage: int) -> Dict[str, Any]:
        """
        Set futures leverage for the configured symbol.

        Args:
            leverage: Leverage multiplier (e.g. 3 for 3x).
        """
        logger.info("Setting leverage to %dx", leverage)
        return await self._retry(self.exchange.set_leverage, leverage, self.symbol)

    async def set_margin_mode(self, margin_mode: str) -> Dict[str, Any]:
        """
        Set futures margin mode (ISOLATED or CROSS).

        Args:
            margin_mode: 'isolated' or 'cross'.
        """
        logger.info("Setting margin mode to %s", margin_mode.upper())
        return await self._retry(
            self.exchange.set_margin_mode, margin_mode.upper(), self.symbol
        )

    async def enable_hedge_mode(self) -> Dict[str, Any]:
        """
        Enable dual-side (hedge mode) for futures positions.
        """
        logger.info("Enabling hedge mode (dual-side positions)")
        try:
            return await self._retry(self.exchange.set_position_mode, True, self.symbol)
        except Exception as exc:
            logger.warning(
                "Could not set hedge mode (it might already be enabled): %s", exc
            )
            return {}

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an open limit order.

        Args:
            order_id: The exchange-assigned order ID.

        Returns:
            dict: ccxt cancel response.
        """
        logger.info("Canceling order %s", order_id)
        return await self._retry(self.exchange.cancel_order, order_id, self.symbol)

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Fetch current status of a specific order.

        Args:
            order_id: The exchange-assigned order ID.

        Returns:
            dict: ccxt order dict with current 'status' field.
        """
        return await self._retry(self.exchange.fetch_order, order_id, self.symbol)

    async def fetch_open_orders(self) -> List[Dict[str, Any]]:
        """
        Fetch all currently open orders for the configured symbol.

        Returns:
            list[dict]: List of ccxt open order dicts.
        """
        return await self._retry(self.exchange.fetch_open_orders, self.symbol)

    async def fetch_balance(self) -> Dict[str, Any]:
        """
        Fetch the full account balance.

        Returns:
            dict: {currency: {free: X, used: Y, total: Z}}
        """
        return await self._retry(self.exchange.fetch_balance)

    async def fetch_ohlcv(
        self, timeframe: str = "1h", limit: int = 200
    ) -> List[List[Any]]:
        """
        Fetch historical OHLCV candles.

        Args:
            timeframe: Candle interval string, e.g. '1h', '4h', '1d'.
            limit:     Number of candles to fetch.

        Returns:
            list[list]: Each entry is [timestamp_ms, open, high, low, close, volume].
        """
        return await self._retry(
            self.exchange.fetch_ohlcv,
            self.symbol,
            timeframe,
            limit=limit,
        )

    async def close(self) -> None:
        """Gracefully close the ccxt async HTTP session."""
        await self.exchange.close()

    async def _retry(self, func, *args, **kwargs) -> Any:
        """
        Retry a coroutine with exponential backoff on transient network errors.

        Retries on ccxt.NetworkError and ccxt.RequestTimeout only.
        Non-retryable exchange errors are re-raised immediately.

        Args:
            func:     The ccxt coroutine function to call.
            *args:    Positional arguments forwarded to func.
            **kwargs: Keyword arguments forwarded to func.

        Returns:
            The return value of func on success.

        Raises:
            ccxt.ExchangeError: On non-retryable exchange errors.
            ccxt.NetworkError:  After all retry attempts are exhausted.
        """
        last_exc: Optional[Exception] = None
        for attempt, delay in enumerate(RETRY_DELAYS):
            try:
                return await func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                last_exc = exc
                logger.warning(
                    "Attempt %d failed (%s). Retrying in %ds...",
                    attempt + 1,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            except ccxt.ExchangeError as exc:
                logger.error("Exchange error (non-retryable): %s", exc)
                raise

        # Final attempt with no more retries.
        try:
            return await func(*args, **kwargs)
        except (ccxt.NetworkError, ccxt.RequestTimeout):
            logger.error("All %d retry attempts exhausted.", len(RETRY_DELAYS))
            raise last_exc
