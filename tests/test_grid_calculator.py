"""
tests/test_grid_calculator.py
------------------------------
Unit tests for src/strategy/grid_calculator.py.

Tests cover:
  - Arithmetic and geometric level generation.
  - Price quantization.
  - Correct buy/sell side assignment.
  - Boundary filtering.
"""

import pytest

from config.settings import GridBotSettings
from src.strategy import GridLevel, GridType
from src.strategy.grid_calculator import GridCalculator


def make_calculator(
    grid_type: GridType = GridType.GEOMETRIC,
    spacing_pct: float = 0.01,
    spacing_abs: float = 100.0,
    num_grids_up: int = 5,
    num_grids_down: int = 5,
    order_size_quote: float = 100.0,
    lower_bound: float = 20_000.0,
    upper_bound: float = 60_000.0,
    price_step: float = 0.01,
) -> GridCalculator:
    return GridCalculator(
        grid_type=grid_type,
        spacing_pct=spacing_pct,
        spacing_abs=spacing_abs,
        num_grids_up=num_grids_up,
        num_grids_down=num_grids_down,
        order_size_quote=order_size_quote,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        price_step=price_step,
    )


class TestGeometricLevels:
    def test_correct_number_of_levels(self) -> None:
        calc = make_calculator(num_grids_up=5, num_grids_down=5)
        levels = calc.calculate(30_000.0)
        assert len(levels) == 10

    def test_levels_sorted_ascending(self) -> None:
        calc = make_calculator()
        levels = calc.calculate(30_000.0)
        prices = [lv.price for lv in levels]
        assert prices == sorted(prices)

    def test_buy_levels_below_centre(self) -> None:
        calc = make_calculator()
        levels = calc.calculate(30_000.0)
        centre = 30_000.0
        for lv in levels:
            if lv.side == "buy":
                assert lv.price < centre

    def test_sell_levels_above_centre(self) -> None:
        calc = make_calculator()
        levels = calc.calculate(30_000.0)
        centre = 30_000.0
        for lv in levels:
            if lv.side == "sell":
                assert lv.price > centre

    def test_geometric_spacing_ratio(self) -> None:
        """Successive levels should be ~1% apart (geometric)."""
        calc = make_calculator(spacing_pct=0.01)
        levels = calc.calculate(30_000.0)
        for i in range(1, len(levels)):
            ratio = levels[i].price / levels[i - 1].price
            assert pytest.approx(ratio, abs=0.01) in [1.01, 1.0201]

    def test_order_size_quote_propagated(self) -> None:
        calc = make_calculator(order_size_quote=200.0)
        levels = calc.calculate(30_000.0)
        for lv in levels:
            assert lv.order_size_quote == 200.0


class TestArithmeticLevels:
    def test_arithmetic_fixed_spacing(self) -> None:
        """Successive arithmetic levels should differ by exactly spacing_abs."""
        calc = make_calculator(grid_type=GridType.ARITHMETIC, spacing_abs=300.0)
        levels = calc.calculate(30_000.0)
        for i in range(1, len(levels)):
            diff = abs(levels[i].price - levels[i - 1].price)
            assert pytest.approx(diff, abs=1.0) in [300.0, 600.0]


class TestBoundaryFiltering:
    def test_levels_respect_lower_bound(self) -> None:
        calc = make_calculator(lower_bound=29_000.0)
        levels = calc.calculate(29_100.0)
        for lv in levels:
            assert lv.price >= 29_000.0

    def test_levels_respect_upper_bound(self) -> None:
        calc = make_calculator(upper_bound=31_000.0)
        levels = calc.calculate(30_900.0)
        for lv in levels:
            assert lv.price <= 31_000.0


class TestPriceQuantization:
    def test_prices_are_quantized(self) -> None:
        calc = make_calculator(price_step=10.0)
        levels = calc.calculate(30_000.0)
        for lv in levels:
            assert round(lv.price % 10.0, 5) == 0.0
