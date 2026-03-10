"""Price formatting utilities for Grid Bot.

This module provides utilities for formatting prices with exchange-specific precision.
"""


def format_price_with_precision(price: float, precision: int) -> str:
    """Format a price to the correct precision.

    Args:
        price: The price to format
        precision: Number of decimal places (0-8)

    Returns:
        Formatted price string

    Raises:
        ValueError: If precision is negative or price is negative
    """
    if price < 0:
        raise ValueError("Price cannot be negative")
    if precision < 0 or precision > 8:
        raise ValueError("Precision must be between 0 and 8")

    return f"{price:.{precision}f}"


def quantize_price_to_step(price: float, price_step: float) -> float:
    """Quantize a price to the nearest valid exchange step.

    Exchange prices must align with the exchange's price_step (tick size).
    For example, if price_step is 0.01, the price 100.123 becomes 100.12.

    Args:
        price: The original price
        price_step: The exchange's minimum price increment

    Returns:
        Price rounded to nearest valid step

    Raises:
        ValueError: If price_step is zero or negative

    Example:
        >>> quantize_price_to_step(100.123, 0.01)
        100.12
        >>> quantize_price_to_step(50000.56789, 0.1)
        50000.6
    """
    if price_step <= 0:
        raise ValueError("Price step must be positive")

    # Round to nearest step
    return round(price / price_step) * price_step
