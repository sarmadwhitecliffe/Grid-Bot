"""
Symbol format utilities for consistent symbol handling across all components.

STANDARD FORMATS:
- Config format: "UNI/USDT" (with slash, used in strategy_configs.json)
- Market format: "UNIUSDT" (no slash, used for exchange API calls and webhooks)
- CCXT format: "UNI/USDT:USDT" (with slash and settlement currency for futures)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def normalize_to_config_format(symbol: str) -> str:
    """
    Normalize any symbol format to config format (with slash).

    Examples:
        UNIUSDT -> UNI/USDT
        UNI/USDT -> UNI/USDT
        UNI/USDT:USDT -> UNI/USDT
        BTCUSDT -> BTC/USDT

    Args:
        symbol: Symbol in any format

    Returns:
        Symbol in config format (BASE/QUOTE)
    """
    # Remove settlement currency suffix if present
    symbol = symbol.split(":")[0]

    # If already has slash, return as-is
    if "/" in symbol:
        return symbol.upper()

    # No slash - need to split base and quote
    # Handle common quote currencies
    quote_currencies = ["USDT", "BUSD", "USD", "USDC", "BTC", "ETH", "BNB"]

    symbol_upper = symbol.upper()
    for quote in quote_currencies:
        if symbol_upper.endswith(quote):
            base = symbol_upper[: -len(quote)]
            if base:  # Make sure there's something left
                return f"{base}/{quote}"

    # If we can't parse it, return as-is and log warning
    logger.warning(f"Could not parse symbol format: {symbol}")
    return symbol.upper()


def normalize_to_market_format(symbol: str) -> str:
    """
    Normalize any symbol format to market format (no slash).

    Examples:
        UNI/USDT -> UNIUSDT
        UNIUSDT -> UNIUSDT
        UNI/USDT:USDT -> UNIUSDT
        BTC/USDT -> BTCUSDT

    Args:
        symbol: Symbol in any format

    Returns:
        Symbol in market format (BASEQUOTE)
    """
    # Remove settlement currency suffix if present
    symbol = symbol.split(":")[0]

    # Remove slash
    return symbol.replace("/", "").upper()


def normalize_to_ccxt_format(symbol: str, is_futures: bool = True) -> str:
    """
    Normalize symbol to CCXT format.

    Examples (futures):
        UNIUSDT -> UNI/USDT:USDT
        UNI/USDT -> UNI/USDT:USDT

    Examples (spot):
        UNIUSDT -> UNI/USDT
        UNI/USDT -> UNI/USDT

    Args:
        symbol: Symbol in any format
        is_futures: Whether this is for futures markets

    Returns:
        Symbol in CCXT format
    """
    # First normalize to config format
    config_format = normalize_to_config_format(symbol)

    if is_futures:
        # Add settlement currency for futures
        base, quote = config_format.split("/")
        return f"{base}/{quote}:{quote}"
    else:
        return config_format


def match_symbol_format(symbol: str, target_symbols: list) -> Optional[str]:
    """
    Find matching symbol from a list, trying multiple format variations.

    Args:
        symbol: Symbol to match
        target_symbols: List of symbols to search in

    Returns:
        Matched symbol from target list, or None if no match found
    """
    # Generate variations
    variations = [
        symbol,
        normalize_to_config_format(symbol),
        normalize_to_market_format(symbol),
        normalize_to_ccxt_format(symbol, is_futures=True),
        normalize_to_ccxt_format(symbol, is_futures=False),
    ]

    # Try exact match first
    for variation in variations:
        if variation in target_symbols:
            return variation

    # Try case-insensitive match
    symbol_upper = symbol.upper()
    for target in target_symbols:
        if target.upper() == symbol_upper:
            return target

    # Try matching after normalizing both
    normalized_input = normalize_to_config_format(symbol)
    for target in target_symbols:
        if normalize_to_config_format(target) == normalized_input:
            return target

    return None


def validate_symbol_format(symbol: str) -> bool:
    """
    Validate if symbol is in a recognizable format.

    Args:
        symbol: Symbol to validate

    Returns:
        True if valid, False otherwise
    """
    try:
        # Try to normalize it
        normalized = normalize_to_config_format(symbol)
        # Check if it has base and quote
        if "/" in normalized:
            base, quote = normalized.split("/")
            return bool(base and quote)
        return False
    except Exception:
        return False
