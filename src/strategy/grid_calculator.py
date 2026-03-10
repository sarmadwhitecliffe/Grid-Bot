"""
src/strategy/grid_calculator.py
--------------------------------
Stateless grid level generator.

Supports arithmetic (fixed $ gap) and geometric (fixed % gap) spacing.
Output prices are quantized to the exchange price_step to avoid rejection.
"""

import logging
from typing import List, Optional

from src.strategy import GridLevel, GridType

logger = logging.getLogger(__name__)


class GridCalculator:
    """
    Generate buy and sell limit-order price levels around a centre price.

    This class is intentionally stateless — every call to calculate()
    produces a fresh list of GridLevel objects from the supplied inputs.
    """

    def __init__(
        self,
        grid_type: GridType = GridType.GEOMETRIC,
        spacing_pct: float = 0.01,
        spacing_abs: float = 50.0,
        num_grids_up: int = 10,
        num_grids_down: int = 10,
        order_size_quote: float = 100.0,
        price_step: float = 0.01,
        lower_bound: Optional[float] = None,
        upper_bound: Optional[float] = None,
    ) -> None:
        """
        Configure the grid calculator.

        Args:
            grid_type:        ARITHMETIC or GEOMETRIC spacing mode.
            spacing_pct:      Percentage gap between levels (geometric mode).
            spacing_abs:      Absolute price gap between levels (arithmetic mode).
            num_grids_up:     Number of sell levels to place above centre.
            num_grids_down:   Number of buy levels to place below centre.
            order_size_quote: USDT value per grid level order.
            price_step:       Exchange minimum price increment for quantization.
            lower_bound:      Optional hard lower price boundary; buy levels
                              below this price are filtered out.
            upper_bound:      Optional hard upper price boundary; sell levels
                              above this price are filtered out.
        """
        self.grid_type = grid_type
        self.spacing_pct = spacing_pct
        self.spacing_abs = spacing_abs
        self.num_grids_up = num_grids_up
        self.num_grids_down = num_grids_down
        self.order_size_quote = order_size_quote
        self.price_step = price_step
        self.lower_bound = lower_bound
        self.upper_bound = upper_bound

    def calculate(self, centre_price: float) -> List[GridLevel]:
        """
        Compute all buy and sell grid levels around the given centre price.

        Levels below lower_bound or above upper_bound are excluded.
        The returned list is sorted ascending by price.

        Args:
            centre_price: The market price around which to centre the grid.

        Returns:
            list[GridLevel]: All valid grid levels sorted by price.
        """
        buys: List[GridLevel] = []
        for i in range(1, self.num_grids_down + 1):
            p = self._price(centre_price, i, "down")
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
            p = self._price(centre_price, i, "up")
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
            "Grid calculated: %d buy levels, %d sell levels around %.4f",
            len(buys),
            len(sells),
            centre_price,
        )
        return levels

    def order_amount(self, price: float) -> float:
        """
        Convert USDT order size to base-currency amount.

        Args:
            price: Limit price of the order in quote currency.

        Returns:
            float: Base-currency quantity to order.
        """
        return self.order_size_quote / price

    def _price(self, centre: float, i: int, direction: str) -> float:
        """
        Calculate the raw price for grid level i in the given direction.

        Args:
            centre:    Centre price of the grid.
            i:         Grid level index (1 = closest to centre).
            direction: 'up' for sell levels, 'down' for buy levels.

        Returns:
            float: Quantized price for this grid level.
        """
        if self.grid_type == GridType.ARITHMETIC:
            raw = (
                centre + i * self.spacing_abs
                if direction == "up"
                else centre - i * self.spacing_abs
            )
        else:
            # Geometric (compound percentage)
            raw = (
                centre * (1 + self.spacing_pct) ** i
                if direction == "up"
                else centre / (1 + self.spacing_pct) ** i
            )
        return self._quantize(raw)

    def _quantize(self, price: float) -> float:
        """
        Snap a raw price to the nearest valid exchange price increment.

        Args:
            price: Raw calculated price.

        Returns:
            float: Price rounded to the nearest price_step.
        """
        if self.price_step <= 0:
            return price
        return round(round(price / self.price_step) * self.price_step, 10)
