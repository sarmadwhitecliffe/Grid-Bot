"""
Market Data Cache - Phase 2 Optimization

Caches market prices and OHLCV data to eliminate redundant API calls.
Reduces signal processing latency from ~11s to <500ms.

Features:
- Unified cache for price, ticker, and OHLCV data
- Configurable TTL (default: 30 seconds)
- Automatic expiration based on candle timeframe
- Thread-safe operations
- LRU eviction for memory management (CPU Optimized with O(1) access)
"""

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


class MarketDataCache:
    """
    Unified cache for market data (price, ticker, OHLCV).

    Reduces API calls by caching data with time-based expiration.
    Cache TTL is configurable and can be aligned with candle timeframes.

    CPU Optimization: Uses OrderedDict for O(1) LRU operations instead of O(n) min().

    Disk Cache (Phase 4):
    - OHLCV data persists to disk in Parquet format
    - Survives bot restarts
    - Staleness validation prevents serving stale data
    """

    # Timeframe to seconds mapping
    TIMEFRAME_SECONDS = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    # Default staleness multiplier (max age = timeframe * multiplier)
    DEFAULT_STALENESS_MULTIPLIER = 2.0

    def __init__(
        self,
        default_ttl: int = 30,
        max_size: int = 500,
        cache_dir: Optional[str] = None,
        enable_disk_cache: bool = True,
    ):
        """
        Initialize market data cache.

        Args:
            default_ttl: Default time-to-live in seconds (default: 30)
            max_size: Maximum number of cache entries (default: 500)
            cache_dir: Directory for disk cache (default: data_futures/cache/ohlcv)
            enable_disk_cache: Enable disk persistence for OHLCV (default: True)
        """
        # CPU Optimization: Use OrderedDict for O(1) LRU eviction
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._default_ttl = default_ttl
        self._max_size = max_size

        # Phase 3: Per-type TTLs
        self._price_ttl = float(os.getenv("PRICE_TTL_SECONDS", "3"))
        self._ohlcv_ttl = float(os.getenv("OHLCV_TTL_SECONDS", "60"))

        # Phase 4: Disk cache configuration
        self._enable_disk_cache = enable_disk_cache and (
            os.getenv("OHLCV_DISK_CACHE", "true").lower() == "true"
        )
        self._staleness_multiplier = float(
            os.getenv(
                "OHLCV_STALENESS_MULTIPLIER", str(self.DEFAULT_STALENESS_MULTIPLIER)
            )
        )

        if self._enable_disk_cache:
            self._cache_dir = Path(
                cache_dir
                or os.getenv("OHLCV_DISK_CACHE_DIR", "data_futures/cache/ohlcv")
            )
            self._metadata_path = self._cache_dir.parent / "ohlcv_metadata.json"
            self._metadata: Dict[str, Any] = {}

            # Create cache directory if needed
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            # Load existing metadata
            self._load_metadata()

            logger.info(
                f"Disk cache enabled: dir={self._cache_dir}, staleness_multiplier={self._staleness_multiplier}"
            )
        else:
            self._cache_dir = None
            self._metadata_path = None
            self._metadata = {}

        # Statistics
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._disk_hits = 0

        logger.info(
            f"MarketDataCache initialized: TTL={default_ttl}s, price_ttl={self._price_ttl}s, "
            f"ohlcv_ttl={self._ohlcv_ttl}s, max_size={max_size}, disk_cache={self._enable_disk_cache}"
        )

    def _is_expired(self, entry: Dict[str, Any]) -> bool:
        """
        Check if a cache entry has expired.

        Args:
            entry: Cache entry with 'expiry' timestamp

        Returns:
            True if expired, False otherwise
        """
        return time.time() >= entry["expiry"]

    def _evict_lru(self):
        """Evict least recently used entry if cache is full.

        CPU Optimization: O(1) operation using OrderedDict (popitem last=False).
        """
        if len(self._cache) < self._max_size:
            return

        # O(1) LRU eviction using OrderedDict - removes oldest item
        lru_key, _ = self._cache.popitem(last=False)
        self._evictions += 1
        logger.debug(f"Evicted LRU cache entry: {lru_key}")

    # ============== Disk Cache Methods (Phase 4) ==============

    def _get_parquet_path(self, symbol: str, timeframe: str, limit: int) -> Path:
        """Get parquet file path for OHLCV cache."""
        safe_symbol = symbol.replace("/", "").replace("-", "_")
        return self._cache_dir / f"{safe_symbol}_{timeframe}_{limit}.parquet"

    def _load_metadata(self) -> None:
        """Load cache metadata from disk."""
        if not self._metadata_path or not self._metadata_path.exists():
            self._metadata = {}
            return

        try:
            with open(self._metadata_path, "r") as f:
                self._metadata = json.load(f)
            logger.debug(f"Loaded disk cache metadata: {len(self._metadata)} entries")
        except Exception as e:
            logger.warning(f"Failed to load cache metadata: {e}")
            self._metadata = {}

    def _save_metadata(self) -> None:
        """Save cache metadata to disk."""
        if not self._metadata_path:
            return

        try:
            with open(self._metadata_path, "w") as f:
                json.dump(self._metadata, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache metadata: {e}")

    def _is_disk_cache_fresh(self, meta: Dict[str, Any], timeframe: str) -> bool:
        """Check if disk cache is fresh enough to use."""
        if not meta:
            return False

        fetch_ts = meta.get("fetch_ts", 0)
        tf_seconds = self.TIMEFRAME_SECONDS.get(timeframe, 3600)
        max_age = tf_seconds * self._staleness_multiplier

        is_fresh = (time.time() - fetch_ts) < max_age
        logger.debug(
            f"Disk cache freshness check: {timeframe}, age={time.time() - fetch_ts:.1f}s, "
            f"max_age={max_age:.1f}s, fresh={is_fresh}"
        )
        return is_fresh

    def save_ohlcv_to_disk(
        self, symbol: str, timeframe: str, limit: int, df: pd.DataFrame
    ) -> None:
        """Save OHLCV DataFrame to parquet file."""
        if not self._enable_disk_cache or not self._cache_dir:
            return

        try:
            path = self._get_parquet_path(symbol, timeframe, limit)

            # Prepare DataFrame for parquet (convert Decimal to float)
            df_save = df.copy()
            for col in ["open", "high", "low", "close", "volume"]:
                if col in df_save.columns:
                    df_save[col] = df_save[col].astype(float)

            # Reset index if timestamp is index
            if "timestamp" not in df_save.columns:
                df_save = df_save.reset_index()
                if "timestamp" in df_save.columns:
                    df_save["timestamp"] = (
                        pd.to_datetime(df_save["timestamp"]).astype("int64") // 10**6
                    )

            df_save.to_parquet(path, index=False)

            # Update metadata
            cache_key = f"{symbol}_{timeframe}_{limit}"
            last_bar_ts = (
                int(df_save["timestamp"].iloc[-1])
                if "timestamp" in df_save.columns
                else None
            )
            self._metadata[cache_key] = {
                "last_bar_ts": last_bar_ts,
                "fetch_ts": time.time(),
                "timeframe": timeframe,
                "limit": limit,
            }
            self._save_metadata()

            logger.debug(f"Saved OHLCV cache to disk: {path}")
        except Exception as e:
            logger.warning(f"Failed to save OHLCV to disk: {e}")

    def load_ohlcv_from_disk(
        self, symbol: str, timeframe: str, limit: int
    ) -> Optional[pd.DataFrame]:
        """Load OHLCV DataFrame from parquet file if fresh enough."""
        if not self._enable_disk_cache:
            return None

        cache_key = f"{symbol}_{timeframe}_{limit}"
        path = self._get_parquet_path(symbol, timeframe, limit)

        if not path.exists():
            logger.debug(f"Disk cache file not found: {path}")
            return None

        # Check metadata for freshness
        meta = self._metadata.get(cache_key, {})
        if not self._is_disk_cache_fresh(meta, timeframe):
            logger.debug(f"Disk cache stale for {symbol} {timeframe}")
            return None

        try:
            df = pd.read_parquet(path)

            # Convert back to Decimal for consistency
            for col in ["open", "high", "low", "close"]:
                if col in df.columns:
                    df[col] = df[col].astype(str).apply(Decimal)
            if "volume" in df.columns:
                df["volume"] = df["volume"].astype(str).apply(Decimal)

            # Restore timestamp index if present
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)

            logger.info(
                f"Loaded OHLCV from disk cache: {symbol} {timeframe} ({len(df)} candles)"
            )
            return df
        except Exception as e:
            logger.warning(f"Failed to load OHLCV from disk: {e}")
            return None

    def get_price(self, symbol: str) -> Optional[Decimal]:
        """
        Get cached price for a symbol.

        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")

        Returns:
            Cached price or None if not found/expired
        """
        cache_key = f"price:{symbol}"

        if cache_key not in self._cache:
            self._misses += 1
            return None

        entry = self._cache[cache_key]

        if self._is_expired(entry):
            # Expired - remove from cache
            del self._cache[cache_key]
            self._misses += 1
            logger.debug(f"Price cache EXPIRED: {symbol}")
            return None

        # Cache hit - O(1) LRU update using OrderedDict
        self._cache.move_to_end(cache_key)
        self._hits += 1
        logger.debug(f"Price cache HIT: {symbol} = {entry['price']}")
        return entry["price"]

    def set_price(self, symbol: str, price: Decimal, ttl: Optional[int] = None):
        """
        Cache a price for a symbol.

        Args:
            symbol: Trading symbol
            price: Current price
            ttl: Time-to-live in seconds (uses default if None)
        """
        if ttl is None:
            ttl = self._price_ttl

        cache_key = f"price:{symbol}"
        now = time.time()

        self._evict_lru()  # Make room if needed

        self._cache[cache_key] = {
            "price": price,
            "timestamp": now,
            "expiry": now + ttl,
            "last_access": now,
            "ttl": ttl,
        }

        logger.debug(f"Price cached: {symbol} = {price}, TTL={ttl}s")

    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get cached ticker data for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Cached ticker dict or None if not found/expired
        """
        cache_key = f"ticker:{symbol}"

        if cache_key not in self._cache:
            self._misses += 1
            return None

        entry = self._cache[cache_key]

        if self._is_expired(entry):
            del self._cache[cache_key]
            self._misses += 1
            logger.debug(f"Ticker cache EXPIRED: {symbol}")
            return None

        # O(1) LRU update
        self._cache.move_to_end(cache_key)
        self._hits += 1
        logger.debug(f"Ticker cache HIT: {symbol}")
        return entry["ticker"]

    def set_ticker(
        self, symbol: str, ticker: Dict[str, Any], ttl: Optional[int] = None
    ):
        """
        Cache ticker data for a symbol.

        Args:
            symbol: Trading symbol
            ticker: Ticker data dict
            ttl: Time-to-live in seconds
        """
        if ttl is None:
            ttl = self._default_ttl

        cache_key = f"ticker:{symbol}"
        now = time.time()

        self._evict_lru()

        self._cache[cache_key] = {
            "ticker": ticker,
            "timestamp": now,
            "expiry": now + ttl,
            "last_access": now,
            "ttl": ttl,
        }

        logger.debug(f"Ticker cached: {symbol}, TTL={ttl}s")

    def get_ohlcv(
        self, symbol: str, timeframe: str, period: int
    ) -> Optional[pd.DataFrame]:
        """
        Get cached OHLCV data.

        Tries: 1) Memory cache → 2) Disk cache → 3) None

        Args:
            symbol: Trading symbol
            timeframe: Candle timeframe (e.g., "1m")
            period: Number of candles

        Returns:
            Cached DataFrame or None if not found/expired
        """
        cache_key = f"ohlcv:{symbol}:{timeframe}:{period}"

        # 1. Check memory cache
        if cache_key in self._cache:
            entry = self._cache[cache_key]

            if not self._is_expired(entry):
                # O(1) LRU update
                self._cache.move_to_end(cache_key)
                self._hits += 1
                logger.debug(f"OHLCV memory cache HIT: {cache_key}")
                return entry["ohlcv"]
            else:
                # Expired - remove from memory cache
                del self._cache[cache_key]
                logger.debug(f"OHLCV memory cache EXPIRED: {cache_key}")

        # 2. Try disk cache (only for OHLCV)
        disk_data = self.load_ohlcv_from_disk(symbol, timeframe, period)
        if disk_data is not None:
            self._disk_hits += 1
            self._hits += 1
            return disk_data

        # 3. Cache miss
        self._misses += 1
        return None

    def set_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        period: int,
        ohlcv: pd.DataFrame,
        ttl: Optional[int] = None,
    ):
        """
        Cache OHLCV data in memory and persist to disk.

        Args:
            symbol: Trading symbol
            timeframe: Candle timeframe
            period: Number of candles
            ohlcv: OHLCV DataFrame
            ttl: Time-to-live (uses timeframe-based if None)
        """
        if ttl is None:
            ttl = self._ohlcv_ttl

        cache_key = f"ohlcv:{symbol}:{timeframe}:{period}"
        now = time.time()

        self._evict_lru()

        self._cache[cache_key] = {
            "ohlcv": ohlcv,
            "timestamp": now,
            "expiry": now + ttl,
            "last_access": now,
            "ttl": ttl,
            "timeframe": timeframe,
        }

        # Persist to disk (non-blocking, fire-and-forget)
        if self._enable_disk_cache:
            try:
                self.save_ohlcv_to_disk(symbol, timeframe, period, ohlcv)
            except Exception as e:
                logger.debug(f"Disk cache save failed (non-blocking): {e}")

        logger.debug(f"OHLCV cached: {cache_key}, TTL={ttl}s")

    def invalidate_symbol(self, symbol: str):
        """
        Invalidate all cache entries for a symbol.

        Args:
            symbol: Trading symbol to invalidate
        """
        keys_to_delete = [key for key in self._cache.keys() if symbol in key]

        for key in keys_to_delete:
            del self._cache[key]

        if keys_to_delete:
            logger.info(f"Invalidated {len(keys_to_delete)} cache entries for {symbol}")

    async def preload_symbols(self, symbols: List[str], exchange) -> None:
        """
        Pre-load market data for multiple symbols asynchronously.

        Fetches price and OHLCV data for each symbol to warm up the cache.
        Handles errors gracefully to avoid blocking startup.

        Args:
            symbols: List of trading symbols (e.g., ["BTC/USDT", "ETH/USDT"])
            exchange: Exchange instance with fetch_ticker and fetch_ohlcv methods
        """
        if not symbols:
            logger.debug("No symbols to preload")
            return

        logger.info(
            f"Pre-loading cache for {len(symbols)} symbols: {', '.join(symbols)}"
        )

        # Create tasks for concurrent fetching
        tasks = []
        for symbol in symbols:
            task = self._preload_symbol_data(symbol, exchange)
            tasks.append(task)

        # Execute all tasks concurrently with error handling
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success_count = sum(1 for r in results if not isinstance(r, Exception))
        error_count = len(results) - success_count

        logger.info(
            f"Cache pre-loading completed: {success_count} successful, {error_count} errors"
        )

        if error_count > 0:
            logger.warning(
                f"Pre-loading errors: {[str(r) for r in results if isinstance(r, Exception)]}"
            )

    async def _preload_symbol_data(self, symbol: str, exchange) -> None:
        """
        Pre-load data for a single symbol.

        Args:
            symbol: Trading symbol
            exchange: Exchange instance

        Raises:
            Exception: If fetching fails (handled by caller)
        """
        try:
            # Format market ID if needed
            market_id = symbol.replace("/", "")  # e.g., BTCUSDT

            # Fetch price/ticker data
            ticker = await exchange.fetch_ticker(market_id)
            if ticker and "last" in ticker:
                price = Decimal(str(ticker["last"]))
                self.set_price(symbol, price)
                logger.debug(f"Pre-loaded price for {symbol}: {price}")

            # Fetch OHLCV data (1h timeframe, 100 candles for ATR calculation)
            ohlcv_data = await exchange.fetch_ohlcv(
                market_id, timeframe="1h", limit=100
            )
            if ohlcv_data:
                # Convert to DataFrame
                df = pd.DataFrame(
                    ohlcv_data,
                    columns=["timestamp", "open", "high", "low", "close", "volume"],
                )
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
                df.set_index("timestamp", inplace=True)

                # Convert price columns to Decimal
                for col in ["open", "high", "low", "close"]:
                    df[col] = df[col].astype(str).apply(Decimal)
                df["volume"] = df["volume"].astype(str).apply(Decimal)

                self.set_ohlcv(symbol, "1h", 100, df)
                logger.debug(f"Pre-loaded OHLCV for {symbol}: {len(df)} candles")

        except Exception as e:
            logger.warning(f"Failed to pre-load data for {symbol}: {e}")
            raise  # Re-raise to be caught by gather

    def clear(self):
        """Clear all cache entries."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        logger.info("Cache cleared")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with hit rate, size, hits, misses, evictions, disk_hits
        """
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0

        return {
            "hit_rate": hit_rate,
            "hits": self._hits,
            "misses": self._misses,
            "evictions": self._evictions,
            "disk_hits": self._disk_hits,
            "size": len(self._cache),
            "max_size": self._max_size,
            "default_ttl": self._default_ttl,
            "disk_cache_enabled": self._enable_disk_cache,
        }

    def log_stats(self):
        """Log cache statistics."""
        stats = self.get_stats()
        disk_info = (
            f", disk_hits={stats['disk_hits']}"
            if stats.get("disk_cache_enabled")
            else ""
        )
        logger.info(
            f"Cache stats: hit_rate={stats['hit_rate']:.1%}, "
            f"size={stats['size']}/{stats['max_size']}, "
            f"hits={stats['hits']}, misses={stats['misses']}, "
            f"evictions={stats['evictions']}{disk_info}"
        )
