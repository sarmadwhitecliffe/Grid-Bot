"""Order validation utilities for Grid Bot."""

from typing import Optional


def validate_order_size(
    size: float,
    min_size: float,
    max_size: float,
    step_size: float,
) -> tuple[bool, Optional[str]]:
    """Validate that an order size meets exchange requirements.

    Args:
        size: The order size to validate.
        min_size: Minimum order size allowed by exchange.
        max_size: Maximum order size allowed by exchange.
        step_size: Minimum size increment (lot size).

    Returns:
        Tuple of (is_valid, error_message).
        - is_valid: True if size is valid, False otherwise.
        - error_message: None if valid, error description if invalid.

    Example:
        >>> validate_order_size(0.5, 0.01, 100.0, 0.01)
        (True, None)
        >>> validate_order_size(0.005, 0.01, 100.0, 0.01)
        (False, "Order size 0.005 is below minimum 0.01")
    """
    if size < min_size:
        return False, f"Order size {size} is below minimum {min_size}"

    if size > max_size:
        return False, f"Order size {size} exceeds maximum {max_size}"

    # Check if size aligns with step_size
    remainder = (size / step_size) % 1
    if remainder > 0.0001:  # Small tolerance for float precision
        return False, f"Order size {size} does not align with step size {step_size}"

    return True, None


def validate_order_price(
    price: float,
    min_price: float,
    max_price: float,
    price_step: float,
) -> tuple[bool, Optional[str]]:
    """Validate that an order price meets exchange requirements.

    Args:
        price: The order price to validate.
        min_price: Minimum price allowed by exchange.
        max_price: Maximum price allowed by exchange.
        price_step: Minimum price increment (tick size).

    Returns:
        Tuple of (is_valid, error_message).
        - is_valid: True if price is valid, False otherwise.
        - error_message: None if valid, error description if invalid.

    Raises:
        ValueError: If price_step is zero or negative.

    Example:
        >>> validate_order_price(50000.0, 0.01, 100000.0, 0.01)
        (True, None)
        >>> validate_order_price(50000.123, 0.01, 100000.0, 0.01)
        (False, "Order price 50000.123 does not align with tick size 0.01")
    """
    if price_step <= 0:
        raise ValueError("price_step must be positive")

    if price < min_price:
        return False, f"Order price {price} is below minimum {min_price}"

    if price > max_price:
        return False, f"Order price {price} exceeds maximum {max_price}"

    # Check if price aligns with price_step
    remainder = round((price / price_step) % 1, 8)
    if remainder > 0.0001:  # Small tolerance for float precision
        return False, f"Order price {price} does not align with tick size {price_step}"

    return True, None
