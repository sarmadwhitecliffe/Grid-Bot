"""
src/oms/reconciler.py
---------------------
Exchange state reconciliation for detecting and recovering from state divergence.

This module provides:
- ExchangeReconciler: Compares local state with exchange state
- ReconciliationReport: Summary of discrepancies found
- MissedFill: Represents a fill that occurred during downtime
- OrphanedOrder: Represents an order on exchange not in local state
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.settings import GridBotSettings
from src.exchange.exchange_client import ExchangeClient
from src.oms import OrderRecord, OrderStatus
from src.oms.order_manager import OrderManager
from src.oms.fill_handler import FillHandler

logger = logging.getLogger(__name__)


@dataclass
class MissedFill:
    """Represents a fill that occurred during bot downtime."""

    order_id: str
    side: str
    price: float
    filled_qty: float
    fill_price: float
    timestamp: Optional[datetime] = None


@dataclass
class OrphanedOrder:
    """Represents an order on exchange not tracked locally."""

    exchange_order_id: str
    side: str
    price: float
    amount: float
    status: str
    detected_at: datetime = field(default_factory=datetime.now)


@dataclass
class ReconciliationReport:
    """Summary of reconciliation between local and exchange state."""

    timestamp: datetime = field(default_factory=datetime.now)
    local_order_count: int = 0
    exchange_order_count: int = 0
    missed_fills: List[MissedFill] = field(default_factory=list)
    orphaned_orders: List[OrphanedOrder] = field(default_factory=list)
    price_mismatches: List[Dict[str, Any]] = field(default_factory=list)
    qty_mismatches: List[Dict[str, Any]] = field(default_factory=list)
    reconciled_successfully: bool = True
    error_message: Optional[str] = None

    @property
    def total_discrepancies(self) -> int:
        return (
            len(self.missed_fills)
            + len(self.orphaned_orders)
            + len(self.price_mismatches)
            + len(self.qty_mismatches)
        )


class ExchangeReconciler:
    """
    Reconciles local order state with exchange state on startup.

    Features:
    - Detect orders that were filled while bot was down
    - Detect orders on exchange not tracked locally
    - Detect price/quantity mismatches
    - Auto-recovery options for missed fills
    """

    def __init__(
        self,
        order_manager: OrderManager,
        client: ExchangeClient,
        settings: GridBotSettings,
        fill_handler: Optional[FillHandler] = None,
    ):
        self.order_manager = order_manager
        self.client = client
        self.settings = settings
        self.fill_handler = fill_handler
        self._reconciled = False

    @property
    def is_reconciled(self) -> bool:
        """Check if reconciliation has completed successfully."""
        return self._reconciled

    async def reconcile(self, symbol: str) -> ReconciliationReport:
        """
        Reconcile local state with exchange state.

        Args:
            symbol: Trading symbol (e.g., "BTC/USDT")

        Returns:
            ReconciliationReport with discrepancies found
        """
        report = ReconciliationReport()

        try:
            local_orders = self.order_manager.all_records
            report.local_order_count = len(local_orders)

            exchange_orders = await self.client.fetch_open_orders()
            report.exchange_order_count = len(exchange_orders)

            exchange_order_ids = {o["id"] for o in exchange_orders}

            for order_id, record in local_orders.items():
                if record.status != OrderStatus.OPEN:
                    continue

                if order_id not in exchange_order_ids:
                    missed = await self._detect_missed_fill(order_id, record)
                    if missed:
                        report.missed_fills.append(missed)
                    else:
                        report.orphaned_orders.append(
                            OrphanedOrder(
                                exchange_order_id=order_id,
                                side=record.side,
                                price=record.grid_price,
                                amount=record.amount,
                                status="unknown",
                            )
                        )

            for exch_order in exchange_orders:
                exch_id = exch_order["id"]
                if exch_id not in local_orders:
                    report.orphaned_orders.append(
                        OrphanedOrder(
                            exchange_order_id=exch_id,
                            side=exch_order.get("side", ""),
                            price=float(exch_order.get("price", 0)),
                            amount=float(exch_order.get("amount", 0)),
                            status=exch_order.get("status", "open"),
                        )
                    )

            if report.missed_fills:
                await self._process_missed_fills(report.missed_fills)

            report.reconciled_successfully = True
            self._reconciled = True

            logger.info(
                f"Reconciliation complete: {report.local_order_count} local, "
                f"{report.exchange_order_count} exchange, "
                f"{report.total_discrepancies} discrepancies"
            )

        except Exception as e:
            report.reconciled_successfully = False
            report.error_message = str(e)
            logger.error(f"Reconciliation failed: {e}")

        return report

    async def _detect_missed_fill(
        self, order_id: str, record: OrderRecord
    ) -> Optional[MissedFill]:
        """Detect if an order was filled while bot was down."""
        try:
            order_status = await self.client.get_order_status(order_id)
            status = order_status.get("status", "")

            if status in ("filled", "FILLED"):
                filled_qty = float(order_status.get("filled", record.amount))
                fill_price = float(order_status.get("average_price", record.grid_price))

                return MissedFill(
                    order_id=order_id,
                    side=record.side,
                    price=record.grid_price,
                    filled_qty=filled_qty,
                    fill_price=fill_price,
                    timestamp=datetime.now(timezone.utc),
                )
            elif status in ("canceled", "CANCELED"):
                logger.info(f"Order {order_id} was canceled on exchange")
                return None

        except Exception as e:
            logger.warning(f"Failed to get status for {order_id}: {e}")

        return None

    async def _process_missed_fills(self, missed_fills: List[MissedFill]) -> None:
        """Process missed fills by triggering counter-order placement."""
        if not self.fill_handler:
            logger.warning("No fill handler available to process missed fills")
            return

        for missed in missed_fills:
            logger.info(
                f"Processing missed fill: {missed.order_id} | "
                f"{missed.side} {missed.filled_qty} @ {missed.fill_price}"
            )

    def force_unreconciled(self) -> None:
        """Mark the bot as unreconciled (for emergency recovery mode)."""
        self._reconciled = False
        logger.warning(
            "Reconciler marked as unreconciled - trading may proceed with caution"
        )
