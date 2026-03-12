"""
src/strategy/grid_calculator.py
--------------------------------
Stateless grid level generator.

Supports arithmetic (fixed $ gap) and geometric (fixed % gap) spacing.
Output prices are quantized to the exchange price_step to avoid rejection.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Union

from src.strategy import GridLevel, GridType

logger = logging.getLogger(__name__)


Number = Union[float, int, str, Decimal]


class GridCalculator:
    """
    Generate buy and sell limit-order price levels around a centre price.

    This class is intentionally stateless — every call to calculate()
    produces a fresh list of GridLevel objects from the supplied inputs.
    """

    def __init__(
        self,
        grid_type: GridType = GridType.GEOMETRIC,
        spacing_pct: Number = 0.01,
        spacing_abs: Number = 50.0,
        num_grids_up: int = 10,
        num_grids_down: int = 10,
        order_size_quote: Number = 100.0,
        price_step: Number = 0.01,
        lower_bound: Optional[Number] = None,
        upper_bound: Optional[Number] = None,
    ) -> None:
        """
        Configure the grid calculator.
        """
        self.grid_type = grid_type
        self.spacing_pct = self._to_decimal(spacing_pct)
        self.spacing_abs = self._to_decimal(spacing_abs)
        self.num_grids_up = num_grids_up
        self.num_grids_down = num_grids_down
        self.order_size_quote = self._to_decimal(order_size_quote)
        self.price_step = self._to_decimal(price_step)
        self.lower_bound = self._to_decimal(lower_bound) if lower_bound is not None else None
        self.upper_bound = self._to_decimal(upper_bound) if upper_bound is not None else None

    def calculate(self, centre_price: Number) -> List[GridLevel]:
        """
        Compute all buy and sell grid levels around the given centre price.
        """
        centre_price_decimal = self._to_decimal(centre_price)

        buys: List[GridLevel] = []
        for i in range(1, self.num_grids_down + 1):
            p = self._price(centre_price_decimal, i, "down")
            if p <= 0:
                continue
            if self.lower_bound is not None and p < self.lower_bound:
                continue
            buys.append(
                GridLevel(
                    price=p,
                    side="buy",
                    level_index=i,
                    order_size_quote=self.order_size_quote,
                )
            )

        sells: List[GridLevel] = []
        for i in range(1, self.num_grids_up + 1):
            p = self._price(centre_price_decimal, i, "up")
            if self.upper_bound is not None and p > self.upper_bound:
                continue
            sells.append(
                GridLevel(
                    price=p,
                    side="sell",
                    level_index=i,
                    order_size_quote=self.order_size_quote,
                )
            )

        levels = sorted(buys + sells, key=lambda lvl: lvl.price)
        logger.debug(
            "Grid calculated: %d buy levels, %d sell levels around %s",
            len(buys),
            len(sells),
            centre_price_decimal,
        )
        return levels

    def order_amount(self, price: Number) -> Decimal:
        """
        Convert USDT order size to base-currency amount.
        """
        price_decimal = self._to_decimal(price)
        if price_decimal == 0:
            return Decimal("0")
        return self.order_size_quote / price_decimal

    def _price(self, centre: Decimal, i: int, direction: str) -> Decimal:
        """
        Calculate the raw price for grid level i in the given direction.
        """
        if self.grid_type == GridType.ARITHMETIC:
            raw = (
                centre + Decimal(i) * self.spacing_abs
                if direction == "up"
                else centre - Decimal(i) * self.spacing_abs
            )
        else:
            # Geometric (compound percentage)
            raw = (
                centre * (Decimal("1") + self.spacing_pct) ** i
                if direction == "up"
                else centre / (Decimal("1") + self.spacing_pct) ** i
            )
        return self._quantize(raw)

    def _quantize(self, price: Decimal) -> Decimal:
        """
        Snap a raw price to the nearest valid exchange price increment.
        """
        if self.price_step <= 0:
            return price

        # Quantize logic using Decimal
        return (price / self.price_step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * self.price_step

    @staticmethod
    def _to_decimal(value: Number) -> Decimal:
        """Normalize numeric inputs to Decimal while preserving string precision."""
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
