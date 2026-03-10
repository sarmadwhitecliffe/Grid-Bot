"""
Tests for utils.decimal_utils module.
"""

from decimal import Decimal, InvalidOperation

import pytest

from bot_v2.utils.decimal_utils import (
    clamp_decimal,
    compare_decimals,
    decimal_to_str,
    percentage_of,
    safe_decimal,
    to_decimal,
)


class TestToDecimal:
    """Test to_decimal conversion function."""

    def test_convert_int(self):
        """Test converting integer to Decimal."""
        result = to_decimal(100, "test")
        assert result == Decimal("100")
        assert isinstance(result, Decimal)

    def test_convert_float(self):
        """Test converting float to Decimal."""
        result = to_decimal(1.5, "test")
        assert result == Decimal("1.5")

    def test_convert_string(self):
        """Test converting string to Decimal."""
        result = to_decimal("250.50", "test")
        assert result == Decimal("250.50")

    def test_convert_decimal(self):
        """Test converting Decimal to Decimal (pass-through)."""
        original = Decimal("100.25")
        result = to_decimal(original, "test")
        assert result == original

    def test_none_with_default(self):
        """Test None with default returns default."""
        result = to_decimal(None, "test", Decimal("0"))
        assert result == Decimal("0")

    def test_none_without_default_raises(self):
        """Test None without default raises ValueError."""
        with pytest.raises(ValueError):
            to_decimal(None, "test")

    def test_invalid_with_default(self):
        """Test invalid value with default returns default."""
        result = to_decimal("invalid", "test", Decimal("0"))
        assert result == Decimal("0")

    def test_invalid_without_default_raises(self):
        """Test invalid value without default raises exception."""
        with pytest.raises((InvalidOperation, ValueError)):
            to_decimal("not_a_number", "test")


class TestSafeDecimal:
    """Test safe_decimal function."""

    def test_valid_value(self):
        """Test safe_decimal with valid value."""
        result = safe_decimal(100)
        assert result == Decimal("100")

    def test_invalid_value_returns_default(self):
        """Test invalid value returns default."""
        result = safe_decimal("invalid")
        assert result == Decimal("0")

    def test_none_returns_default(self):
        """Test None returns default."""
        result = safe_decimal(None)
        assert result == Decimal("0")

    def test_custom_default(self):
        """Test custom default value."""
        result = safe_decimal("invalid", Decimal("999"))
        assert result == Decimal("999")


class TestDecimalToStr:
    """Test decimal_to_str formatting function."""

    def test_default_precision(self):
        """Test default 8 decimal places."""
        result = decimal_to_str(Decimal("1.23456789"))
        assert result == "1.23456789"

    def test_custom_precision(self):
        """Test custom precision."""
        result = decimal_to_str(Decimal("1.23456789"), precision=4)
        assert result == "1.2346"  # Rounded

    def test_integer_value(self):
        """Test integer value gets decimal places."""
        result = decimal_to_str(Decimal("100"), precision=2)
        assert result == "100.00"

    def test_rounding(self):
        """Test rounding behavior."""
        result = decimal_to_str(Decimal("1.555"), precision=2)
        assert result == "1.56"  # Rounds up


class TestCompareDecimals:
    """Test compare_decimals function."""

    def test_equal_values(self):
        """Test comparing equal values."""
        assert compare_decimals(Decimal("1.0"), Decimal("1.0"))

    def test_within_tolerance(self):
        """Test values within tolerance are considered equal."""
        assert compare_decimals(Decimal("1.0"), Decimal("1.00005"), Decimal("0.0001"))

    def test_outside_tolerance(self):
        """Test values outside tolerance are not equal."""
        assert not compare_decimals(Decimal("1.0"), Decimal("1.1"), Decimal("0.01"))

    def test_negative_difference(self):
        """Test handles negative differences correctly."""
        assert compare_decimals(Decimal("1.1"), Decimal("1.0"), Decimal("0.2"))


class TestPercentageOf:
    """Test percentage_of calculation function."""

    def test_basic_percentage(self):
        """Test basic percentage calculation."""
        result = percentage_of(Decimal("50"), Decimal("200"))
        assert result == Decimal("25")

    def test_zero_total_returns_default(self):
        """Test zero total returns default."""
        result = percentage_of(Decimal("50"), Decimal("0"))
        assert result == Decimal("0")

    def test_custom_default(self):
        """Test custom default for zero total."""
        result = percentage_of(Decimal("50"), Decimal("0"), Decimal("100"))
        assert result == Decimal("100")

    def test_100_percent(self):
        """Test 100% calculation."""
        result = percentage_of(Decimal("100"), Decimal("100"))
        assert result == Decimal("100")


class TestClampDecimal:
    """Test clamp_decimal function."""

    def test_value_within_range(self):
        """Test value within range is unchanged."""
        result = clamp_decimal(Decimal("150"), Decimal("100"), Decimal("200"))
        assert result == Decimal("150")

    def test_value_below_min(self):
        """Test value below min is clamped to min."""
        result = clamp_decimal(Decimal("50"), Decimal("100"), Decimal("200"))
        assert result == Decimal("100")

    def test_value_above_max(self):
        """Test value above max is clamped to max."""
        result = clamp_decimal(Decimal("250"), Decimal("100"), Decimal("200"))
        assert result == Decimal("200")

    def test_no_min(self):
        """Test clamping with no minimum."""
        result = clamp_decimal(Decimal("50"), None, Decimal("100"))
        assert result == Decimal("50")

    def test_no_max(self):
        """Test clamping with no maximum."""
        result = clamp_decimal(Decimal("250"), Decimal("100"), None)
        assert result == Decimal("250")

    def test_no_limits(self):
        """Test no clamping with no limits."""
        result = clamp_decimal(Decimal("150"), None, None)
        assert result == Decimal("150")
