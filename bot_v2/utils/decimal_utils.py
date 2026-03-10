"""
Decimal conversion and manipulation utilities.

Consolidates all Decimal-related helper functions into a single module,
fixing the duplicate _to_decimal function issue in the original bot.py.
"""

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation, getcontext
from enum import Enum
from typing import Any, Optional

# Set global precision for all Decimal operations
getcontext().prec = 28

logger = logging.getLogger(__name__)


def to_decimal(
    value: Any, context: str = "value", default: Optional[Decimal] = None
) -> Optional[Decimal]:
    """
    Safely convert a value to Decimal with optional fallback.

    Args:
        value: The value to convert to Decimal (int, float, str, Decimal)
        context: Description of what this value represents (for logging)
        default: Optional fallback value if conversion fails

    Returns:
        Decimal value or default if provided

    Raises:
        InvalidOperation, TypeError, or ValueError if conversion fails and no default

    Examples:
        >>> to_decimal(100, "price")
        Decimal('100')
        >>> to_decimal("1.5", "leverage")
        Decimal('1.5')
        >>> to_decimal(None, "optional_value", Decimal('0'))
        Decimal('0')
    """
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Cannot convert None to Decimal for {context}")

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as e:
        if default is not None:
            logger.warning(
                f"Invalid decimal value '{value}' for {context}. "
                f"Using default: {default}. Error: {e}"
            )
            return default
        logger.error(
            f"Invalid decimal value '{value}' for {context}. "
            f"No default provided. Error: {e}"
        )
        raise


def safe_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """
    Convert to Decimal with a safe default (never raises).

    Args:
        value: The value to convert
        default: Default value if conversion fails (default: 0)

    Returns:
        Decimal value or default

    Examples:
        >>> safe_decimal(100)
        Decimal('100')
        >>> safe_decimal("invalid")
        Decimal('0')
        >>> safe_decimal(None, Decimal('1'))
        Decimal('1')
    """
    try:
        return to_decimal(value, "safe_decimal", default)
    except Exception:
        return default


def decimal_to_str(value: Decimal, precision: int = 8) -> str:
    """
    Convert Decimal to string with specified precision.

    Args:
        value: Decimal value to convert
        precision: Number of decimal places (default: 8)

    Returns:
        String representation with specified precision

    Examples:
        >>> decimal_to_str(Decimal('1.23456789'), 4)
        '1.2346'
        >>> decimal_to_str(Decimal('100'), 2)
        '100.00'
    """
    format_str = f"{{:.{precision}f}}"
    return format_str.format(value)


def compare_decimals(
    a: Decimal, b: Decimal, tolerance: Decimal = Decimal("0.0001")
) -> bool:
    """
    Compare two Decimals with tolerance for floating-point errors.

    Args:
        a: First Decimal
        b: Second Decimal
        tolerance: Maximum difference to consider equal (default: 0.0001)

    Returns:
        True if |a - b| <= tolerance

    Examples:
        >>> compare_decimals(Decimal('1.0'), Decimal('1.00001'), Decimal('0.0001'))
        True
        >>> compare_decimals(Decimal('1.0'), Decimal('1.1'), Decimal('0.01'))
        False
    """
    return abs(a - b) <= tolerance


def percentage_of(
    value: Decimal, total: Decimal, default: Decimal = Decimal("0")
) -> Decimal:
    """
    Calculate percentage safely (handles division by zero).

    Args:
        value: The part value
        total: The total value
        default: Default to return if total is zero

    Returns:
        Percentage as Decimal (0-100)

    Examples:
        >>> percentage_of(Decimal('50'), Decimal('200'))
        Decimal('25')
        >>> percentage_of(Decimal('50'), Decimal('0'))
        Decimal('0')
    """
    if total == 0:
        return default
    return (value / total) * Decimal("100")


def clamp_decimal(
    value: Decimal,
    min_value: Optional[Decimal] = None,
    max_value: Optional[Decimal] = None,
) -> Decimal:
    """
    Clamp a Decimal value between min and max.

    Args:
        value: The value to clamp
        min_value: Minimum allowed value (optional)
        max_value: Maximum allowed value (optional)

    Returns:
        Clamped value

    Examples:
        >>> clamp_decimal(Decimal('150'), Decimal('100'), Decimal('200'))
        Decimal('150')
        >>> clamp_decimal(Decimal('50'), Decimal('100'), Decimal('200'))
        Decimal('100')
        >>> clamp_decimal(Decimal('250'), Decimal('100'), Decimal('200'))
        Decimal('200')
    """
    result = value
    if min_value is not None:
        result = max(result, min_value)
    if max_value is not None:
        result = min(result, max_value)
    return result


class DecimalEncoder(json.JSONEncoder):
    """
    JSON encoder that handles Decimal, datetime, and Enum types.

    Used for serializing bot state to JSON files.
    """

    def default(self, obj: Any) -> Any:
        """
        Convert special types to JSON-serializable format.

        Args:
            obj: Object to serialize

        Returns:
            JSON-serializable representation
        """
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)
