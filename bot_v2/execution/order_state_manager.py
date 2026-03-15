"""
Order State Manager - Persistent Order Tracking

Maintains a persistent cache of all live orders synchronized with the exchange.
Provides reconciliation capabilities to detect missing/stale orders.
"""

import asyncio
import json
import logging
import os
import tempfile
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from bot_v2.utils.symbol_utils import normalize_to_market_format

logger = logging.getLogger(__name__)


class OrderRecordStatus(Enum):
    """Order record status for tracking."""

    NEW = "NEW"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNVERIFIED = "UNVERIFIED"
    STALE = "STALE"


@dataclass
class OrderRecord:
    """
    Canonical local order record.

    Tracks both local state and exchange state for reconciliation.
    """

    local_id: str  # Bot-generated unique ID
    exchange_order_id: Optional[str] = None  # Exchange order ID
    symbol: str = ""
    side: str = "BUY"  # BUY or SELL
    quantity: str = "0"  # Use string for Decimal serialization
    leverage: int = 1
    order_type: str = "MARKET"
    status: str = "NEW"
    created_at: Optional[str] = None  # ISO timestamp
    verified_at: Optional[str] = None  # ISO timestamp
    filled_qty: str = "0"
    avg_price: Optional[str] = None
    fees: str = "0"
    pnl: Optional[str] = None
    mode: str = "local_sim"  # local_sim, live
    grid_id: Optional[str] = None  # To associate with a specific grid session
    level_index: Optional[int] = None  # To identify the level in the grid
    metadata: Optional[Dict[str, Any]] = None  # For generic extension
    verification_status: Optional[str] = None  # VERIFIED, UNVERIFIED
    verification_error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None  # Raw exchange response

    @classmethod
    def from_exchange_response(
        cls, local_id: str, exchange_response: Dict[str, Any], mode: str = "live"
    ) -> "OrderRecord":
        """Create OrderRecord from exchange API response."""
        # Safely extract fee cost (fee can be None or dict)
        fee = exchange_response.get("fee")
        fee_cost = "0"
        if fee and isinstance(fee, dict):
            fee_cost = str(fee.get("cost", 0))
        elif fee is not None:
            # Handle case where fee is a number
            try:
                fee_cost = str(float(fee))
            except (ValueError, TypeError):
                fee_cost = "0"

        return cls(
            local_id=local_id,
            exchange_order_id=str(exchange_response.get("id", "")),
            symbol=exchange_response.get("symbol", ""),
            side=exchange_response.get("side", "BUY").upper(),
            quantity=str(exchange_response.get("amount", 0)),
            leverage=1,  # TODO: Extract from params if available
            order_type=exchange_response.get("type", "MARKET").upper(),
            status=exchange_response.get("status", "NEW").upper(),
            created_at=datetime.now(timezone.utc).isoformat(),
            verified_at=(
                datetime.now(timezone.utc).isoformat()
                if exchange_response.get("_verification_status") == "VERIFIED"
                else None
            ),
            filled_qty=str(exchange_response.get("filled", 0)),
            avg_price=(
                str(exchange_response.get("price", 0))
                if exchange_response.get("price")
                else None
            ),
            fees=fee_cost,
            mode=mode,
            verification_status=exchange_response.get("_verification_status"),
            verification_error=exchange_response.get("_verification_error"),
            raw_response=exchange_response,
        )


class OrderStateManager:
    async def periodic_reconcile(self, exchange_fetch_func, interval_sec: int = 300):
        """
        Periodically reconcile orders with exchange and auto-resolve orphans.
        Args:
            exchange_fetch_func: async function to fetch open orders from exchange
            interval_sec: seconds between reconciliations
        """
        import asyncio

        while True:
            report = await self.reconcile_orders(exchange_fetch_func)
            # Attempt to auto-resolve orphaned positions
            for orphan in report.get("unexpected_on_exchange", []):
                exchange_order_id = orphan.get("exchange_order_id")
                symbol = orphan.get("symbol")
                # Attempt to cancel/close orphaned order (requires exchange API)
                logger.warning(
                    f"Attempting to auto-resolve orphaned order {exchange_order_id} for {symbol}"
                )
                # You would call exchange.cancel_order(exchange_order_id, symbol) here if available
            await asyncio.sleep(interval_sec)

    """
    Manages persistent order state and reconciliation.
    
    Features:
    - Atomic writes (temp file + rename)
    - Thread-safe operations
    - Reconciliation with exchange
    - STALE/UNVERIFIED detection
    """

    def __init__(self, data_dir: Path):
        """
        Initialize order state manager.

        Args:
            data_dir: Directory for persistence files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.orders_file = self.data_dir / "orders_state.json"
        self._lock = asyncio.Lock()
        self._retention_hours = self._read_positive_int_env(
            "BOTV2_ORDER_STATE_RETENTION_HOURS",
            24,
        )
        self._prune_statuses = self._read_prune_statuses_env(
            "BOTV2_ORDER_STATE_PRUNE_STATUSES",
            {
                "FILLED",
                "CANCELLED",
                "CLOSED",
                "REJECTED",
                "EXPIRED",
                "STALE",
            },
        )
        self._archive_strip_raw_response = self._read_bool_env(
            "BOTV2_ARCHIVE_STRIP_RAW_RESPONSE",
            True,
        )

        # In-memory state
        self._orders: Dict[str, Dict[str, Any]] = {}

        # Memory management - max in-memory orders before pruning
        self._max_in_memory_orders = self._read_positive_int_env(
            "BOTV2_ORDER_STATE_MAX_IN_MEMORY",
            5000,
        )

        # CPU Optimization: Incremental fill detection
        self._last_known_order_ids: set = set()
        self._last_reconcile_time: Optional[float] = None

        # Load existing state
        self._load()

    @staticmethod
    def _read_positive_int_env(key: str, default: int) -> int:
        """Read a positive integer from env var with safe fallback."""
        raw_value = os.getenv(key)
        if raw_value is None:
            return default

        try:
            parsed = int(raw_value)
            if parsed > 0:
                return parsed
        except ValueError:
            pass

        logger.warning("Invalid %s=%r, using default=%s", key, raw_value, default)
        return default

    @staticmethod
    def _read_prune_statuses_env(key: str, default: set[str]) -> set[str]:
        """Read comma-separated order statuses for archival eligibility."""
        raw_value = os.getenv(key)
        if raw_value is None:
            return default

        statuses = {
            item.strip().upper() for item in raw_value.split(",") if item.strip()
        }
        if statuses:
            return statuses

        logger.warning("Invalid %s=%r, using default statuses", key, raw_value)
        return default

    @staticmethod
    def _read_bool_env(key: str, default: bool) -> bool:
        """Read a bool-like env var value with fallback."""
        raw_value = os.getenv(key)
        if raw_value is None:
            return default

        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False

        logger.warning("Invalid %s=%r, using default=%s", key, raw_value, default)
        return default

    @staticmethod
    def _parse_iso_datetime(timestamp: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime strings, including trailing Z notation."""
        if not timestamp:
            return None

        normalized = timestamp
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _load(self) -> None:
        """Load orders from disk."""
        if not self.orders_file.exists():
            logger.info("No existing orders_state.json, starting fresh")
            self._orders = {"orders": {}, "metadata": {"last_updated": None}}
            return

        try:
            with open(self.orders_file, "r") as f:
                data = json.load(f)

            # Validate structure
            if "orders" not in data:
                logger.warning("orders_state.json missing 'orders' key, resetting")
                self._orders = {"orders": {}, "metadata": {"last_updated": None}}
            else:
                self._orders = data
                order_count = len(self._orders.get("orders", {}))
                logger.info(f"Loaded {order_count} orders from state")

        except Exception as e:
            logger.error(f"Error loading orders_state.json: {e}", exc_info=True)
            self._orders = {"orders": {}, "metadata": {"last_updated": None}}

    async def _save(self) -> None:
        """Save orders to disk with atomic write in a background thread."""
        async with self._lock:
            snapshot = self._snapshot_locked()

        await asyncio.to_thread(self._save_sync, snapshot)

    def _snapshot_locked(self) -> Dict[str, Any]:
        """Build a stable in-memory snapshot while the caller holds the lock."""
        self._orders.setdefault("orders", {})
        self._orders.setdefault("metadata", {})
        self._orders["metadata"]["last_updated"] = datetime.now(
            timezone.utc
        ).isoformat()
        return deepcopy(self._orders)

    def _save_sync(self, snapshot: Dict[str, Any]) -> None:
        """The actual synchronous file IO for saving orders."""
        try:
            self.orders_file.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: unique temp file + rename to avoid concurrent collisions.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.orders_file.parent),
                prefix=f"{self.orders_file.stem}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                json.dump(snapshot, f, indent=2, default=str)
                temp_path = Path(f.name)

            # Atomic rename
            temp_path.replace(self.orders_file)

        except Exception as e:
            logger.error(f"Error saving orders_state.json: {e}", exc_info=True)

    async def add_order(self, order: OrderRecord) -> None:
        """
        Add or update an order in state.

        Args:
            order: OrderRecord to persist
        """
        order_dict = asdict(order)
        async with self._lock:
            self._orders["orders"][order.local_id] = order_dict
            snapshot = self._snapshot_locked()

        await asyncio.to_thread(self._save_sync, snapshot)
        logger.debug(
            f"Order {order.local_id} added to state (exchange_id={order.exchange_order_id})"
        )

    def get_order(self, local_id: str) -> Optional[OrderRecord]:
        """
        Retrieve an order by local ID.

        Args:
            local_id: Local order ID

        Returns:
            OrderRecord if found, None otherwise
        """
        order_dict = self._orders["orders"].get(local_id)
        if not order_dict:
            return None

        return OrderRecord(**order_dict)

    def get_order_by_exchange_id(self, exchange_id: str) -> Optional[OrderRecord]:
        """
        Retrieve an order by exchange ID.

        Args:
            exchange_id: Exchange order ID

        Returns:
            OrderRecord if found, None otherwise
        """
        for order_dict in self._orders["orders"].values():
            if order_dict.get("exchange_order_id") == exchange_id:
                return OrderRecord(**order_dict)
        return None

    def get_all_orders(self) -> List[OrderRecord]:
        """Get all orders in state."""
        return [OrderRecord(**o) for o in self._orders["orders"].values()]

    def get_orders_by_symbol(self, symbol: str) -> List[OrderRecord]:
        """Get all orders for a specific symbol.

        Handles both 'BTC/USDT' and 'BTCUSDT' symbol formats using normalize_to_market_format.
        """
        normalized = normalize_to_market_format(symbol)
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if normalize_to_market_format(o.get("symbol", "")) == normalized
        ]

    def get_open_orders(self) -> List[OrderRecord]:
        """Get all orders with open status (NEW, PARTIALLY_FILLED)."""
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if str(o.get("status", "")).upper() in ["NEW", "PARTIALLY_FILLED", "OPEN"]
        ]

    def get_open_orders_by_symbol(self, symbol: str) -> List[OrderRecord]:
        """Get all open orders for a specific symbol.

        Handles both 'BTC/USDT' and 'BTCUSDT' symbol formats using normalize_to_market_format.
        """
        normalized = normalize_to_market_format(symbol)
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if normalize_to_market_format(o.get("symbol", "")) == normalized
            and str(o.get("status", "")).upper() in ["NEW", "PARTIALLY_FILLED", "OPEN"]
        ]

    def get_unverified_orders(self) -> List[OrderRecord]:
        """Get all orders that failed verification."""
        return [
            OrderRecord(**o)
            for o in self._orders["orders"].values()
            if o.get("verification_status") == "UNVERIFIED"
        ]

    async def update_order_status(
        self,
        local_id: str,
        status: str,
        filled_qty: Optional[str] = None,
        avg_price: Optional[str] = None,
    ) -> None:
        """
        Update order status and optionally fill information.

        Args:
            local_id: Local order ID
            status: New status
            filled_qty: Filled quantity (if applicable)
            avg_price: Average fill price (if applicable)
        """
        async with self._lock:
            if local_id not in self._orders["orders"]:
                logger.warning(f"Cannot update non-existent order {local_id}")
                return

            self._orders["orders"][local_id]["status"] = status
            if filled_qty is not None:
                self._orders["orders"][local_id]["filled_qty"] = filled_qty
            if avg_price is not None:
                self._orders["orders"][local_id]["avg_price"] = avg_price

            snapshot = self._snapshot_locked()

        await asyncio.to_thread(self._save_sync, snapshot)
        logger.info(f"Order {local_id} status updated to {status}")

    async def mark_as_stale(
        self, local_id: str, reason: str = "Not found on exchange"
    ) -> None:
        """
        Mark an order as STALE.

        Args:
            local_id: Local order ID
            reason: Reason for marking stale
        """
        async with self._lock:
            if local_id not in self._orders["orders"]:
                logger.warning(f"Cannot mark non-existent order {local_id} as stale")
                return

            self._orders["orders"][local_id]["status"] = "STALE"
            self._orders["orders"][local_id]["verification_error"] = reason
            snapshot = self._snapshot_locked()

        await asyncio.to_thread(self._save_sync, snapshot)
        logger.warning(f"Order {local_id} marked as STALE: {reason}")

    async def reconcile_orders(
        self, exchange_fetch_func, fetch_order_func=None
    ) -> Dict[str, Any]:
        """
        Reconcile local order state with exchange.

        Args:
            exchange_fetch_func: Async function to fetch open orders from exchange
                                 Should accept symbol and return list of order dicts
            fetch_order_func: Optional async function to fetch a single order by ID.
                              If provided, used to verify orders missing from open orders.
                              Should accept (order_id, symbol) and return order dict or None.

        Returns:
            Reconciliation report with discrepancies
        """
        logger.info("Starting order reconciliation...")

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "local_open_count": 0,
            "exchange_open_count": 0,
            "verified_count": 0,
            "missing_on_exchange": [],
            "unexpected_on_exchange": [],
            "status_mismatches": [],
            "filled_orders": [],
        }

        try:
            # Get local open orders
            local_open = self.get_open_orders()
            report["local_open_count"] = len(local_open)

            # Fetch all open orders from exchange (grouped by symbol)
            symbols = set(o.symbol for o in local_open)
            exchange_orders = {}

            for symbol in symbols:
                try:
                    orders = await exchange_fetch_func(symbol)
                    for order in orders:
                        exchange_orders[str(order["id"])] = order
                except Exception as e:
                    logger.error(f"Failed to fetch orders for {symbol}: {e}")

            report["exchange_open_count"] = len(exchange_orders)

            # Check local orders against exchange
            for local_order in local_open:
                if not local_order.exchange_order_id:
                    logger.warning(
                        f"Local order {local_order.local_id} missing exchange_order_id"
                    )
                    continue

                exchange_order = exchange_orders.get(local_order.exchange_order_id)

                if not exchange_order:
                    exchange_status = None
                    verified_order = None

                    if fetch_order_func:
                        try:
                            verified_order = await fetch_order_func(
                                local_order.exchange_order_id, local_order.symbol
                            )
                            if verified_order:
                                exchange_status = verified_order.get(
                                    "status", ""
                                ).upper()
                        except Exception as e:
                            logger.debug(
                                f"Could not fetch order {local_order.exchange_order_id}: {e}"
                            )

                    if verified_order and exchange_status:
                        terminal_statuses = {
                            "FILLED",
                            "CANCELED",
                            "CANCELLED",
                            "EXPIRED",
                            "CLOSED",
                        }
                        if exchange_status in terminal_statuses:
                            logger.info(
                                f"Order {local_order.local_id} (exchange={local_order.exchange_order_id}) "
                                f"found with terminal status: {exchange_status}"
                            )
                            report["filled_orders"].append(
                                {
                                    "local_id": local_order.local_id,
                                    "exchange_order_id": local_order.exchange_order_id,
                                    "symbol": local_order.symbol,
                                    "status": exchange_status,
                                    "filled_qty": str(verified_order.get("filled", 0)),
                                    "avg_price": (
                                        str(verified_order.get("average", 0))
                                        if verified_order.get("average")
                                        else str(verified_order.get("price", 0))
                                    ),
                                }
                            )
                            await self.update_order_status(
                                local_order.local_id,
                                exchange_status,
                                str(verified_order.get("filled", 0)),
                                (
                                    str(verified_order.get("average", 0))
                                    if verified_order.get("average")
                                    else str(verified_order.get("price", 0))
                                ),
                            )
                            continue

                    logger.warning(
                        f"Local order {local_order.local_id} (exchange={local_order.exchange_order_id}) "
                        f"not found on exchange"
                    )
                    report["missing_on_exchange"].append(
                        {
                            "local_id": local_order.local_id,
                            "exchange_order_id": local_order.exchange_order_id,
                            "symbol": local_order.symbol,
                            "status": local_order.status,
                        }
                    )
                    await self.mark_as_stale(
                        local_order.local_id,
                        "Order not found on exchange during reconciliation",
                    )
                else:
                    # Order found - check status
                    exchange_status = exchange_order.get("status", "").upper()
                    if exchange_status != local_order.status:
                        logger.info(
                            f"Status mismatch for {local_order.local_id}: "
                            f"local={local_order.status}, exchange={exchange_status}"
                        )
                        report["status_mismatches"].append(
                            {
                                "local_id": local_order.local_id,
                                "local_status": local_order.status,
                                "exchange_status": exchange_status,
                            }
                        )
                        # Update local status
                        await self.update_order_status(
                            local_order.local_id,
                            exchange_status,
                            str(exchange_order.get("filled", 0)),
                            (
                                str(exchange_order.get("price", 0))
                                if exchange_order.get("price")
                                else None
                            ),
                        )
                    else:
                        report["verified_count"] += 1

            # Check for unexpected orders on exchange
            local_exchange_ids = {
                o.exchange_order_id for o in local_open if o.exchange_order_id
            }
            for exchange_id, exchange_order in exchange_orders.items():
                if exchange_id not in local_exchange_ids:
                    logger.warning(
                        f"Exchange order {exchange_id} not found in local state (symbol={exchange_order.get('symbol')})"
                    )
                    report["unexpected_on_exchange"].append(
                        {
                            "exchange_order_id": exchange_id,
                            "symbol": exchange_order.get("symbol"),
                            "status": exchange_order.get("status"),
                            "amount": exchange_order.get("amount"),
                        }
                    )

            logger.info(
                f"Reconciliation complete: {report['verified_count']} verified, "
                f"{len(report['missing_on_exchange'])} missing, "
                f"{len(report['status_mismatches'])} mismatches"
            )

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}", exc_info=True)
            report["error"] = str(e)

        # Update last known state for incremental fill detection
        self._last_known_order_ids = {
            o.exchange_order_id for o in self.get_open_orders() if o.exchange_order_id
        }
        import time as time_module

        self._last_reconcile_time = time_module.time()

        return report

    async def quick_fill_check(self, exchange_fetch_func) -> List[Dict[str, Any]]:
        """
        CPU Optimization: Quick fill check using incremental detection.

        Only queries exchange for orders that might have changed, avoiding
        full order book queries when possible.

        Args:
            exchange_fetch_func: Async function to fetch open orders from exchange

        Returns:
            List of newly filled orders (orders that were open but no longer on exchange)
        """
        import time as time_module

        try:
            # Get local open orders
            local_open = self.get_open_orders()
            if not local_open:
                return []

            # Fetch current orders from exchange
            symbols = set(o.symbol for o in local_open)
            current_exchange_ids: set = set()

            for symbol in symbols:
                try:
                    orders = await exchange_fetch_func(symbol)
                    for order in orders:
                        order_id = str(order.get("id", ""))
                        if order_id:
                            current_exchange_ids.add(order_id)
                except Exception as e:
                    logger.debug(f"Quick fill check: failed to fetch {symbol}: {e}")

            # Find orders that were on exchange but are now gone (filled/cancelled)
            previously_known = self._last_known_order_ids
            missing_on_exchange = previously_known - current_exchange_ids

            # Also check orders we haven't seen before (new fills from this session)
            newly_filled = []
            for order in local_open:
                if (
                    order.exchange_order_id
                    and order.exchange_order_id not in current_exchange_ids
                ):
                    if order.status.upper() in ["NEW", "PARTIALLY_FILLED", "OPEN"]:
                        newly_filled.append(
                            {
                                "local_id": order.local_id,
                                "exchange_order_id": order.exchange_order_id,
                                "symbol": order.symbol,
                                "side": order.side,
                                "quantity": order.quantity,
                            }
                        )

            # Update tracking state
            self._last_known_order_ids = current_exchange_ids
            self._last_reconcile_time = time_module.time()

            if missing_on_exchange:
                logger.info(
                    f"Quick fill check: {len(missing_on_exchange)} orders no longer on exchange"
                )

            return newly_filled

        except Exception as e:
            logger.error(f"Quick fill check failed: {e}", exc_info=True)
            return []

    async def prune_archive(self) -> None:
        """
        Move terminal orders older than retention window into 'orders_archive.json'.

        Terminal statuses are controlled by BOTV2_ORDER_STATE_PRUNE_STATUSES.
        Retention age is controlled by BOTV2_ORDER_STATE_RETENTION_HOURS.
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            to_archive = {}
            to_keep = {}

            orders_dict = self._orders.get("orders", {})
            for local_id, order in orders_dict.items():
                status = order.get("status", "").upper()
                created_at_str = order.get("created_at")

                should_archive = False
                if status in self._prune_statuses:
                    created_at = self._parse_iso_datetime(created_at_str)
                    if created_at is None:
                        logger.warning(
                            "Skipping archival for %s due to invalid created_at=%r",
                            local_id,
                            created_at_str,
                        )
                    elif now - created_at > timedelta(hours=self._retention_hours):
                        should_archive = True

                if should_archive:
                    archived_order = deepcopy(order)
                    if self._archive_strip_raw_response:
                        archived_order.pop("raw_response", None)
                    to_archive[local_id] = archived_order
                else:
                    to_keep[local_id] = order

            if not to_archive:
                logger.debug("No orders to prune from state")
                return

            logger.info(
                "Pruning %s orders to archive (retention_hours=%s, statuses=%s)",
                len(to_archive),
                self._retention_hours,
                sorted(self._prune_statuses),
            )

            # Update in-memory state
            self._orders["orders"] = to_keep
            snapshot = self._snapshot_locked()

        await asyncio.to_thread(self._save_sync, snapshot)

        archive_file = self.data_dir / "orders_archive.json"
        await asyncio.to_thread(self._append_to_archive, archive_file, to_archive)

        # Also prune from memory after archiving
        self.prune_memory()
        """
        Remove archived orders from in-memory state to free memory.

        Called after prune_archive to ensure archived orders don't remain
        in the in-memory _orders dict.

        Returns:
            Number of orders removed from memory.
        """
        if len(self._orders["orders"]) <= self._max_in_memory_orders:
            return 0

        # Get all terminal orders that should be in archive
        terminal_statuses = self._prune_statuses
        orders_to_remove = []

        for local_id, order in self._orders["orders"].items():
            status = order.get("status", "").upper()
            if status in terminal_statuses:
                orders_to_remove.append(local_id)

        # Remove oldest terminal orders until under limit
        removed_count = 0
        for local_id in orders_to_remove:
            if len(self._orders["orders"]) <= self._max_in_memory_orders:
                break
            if local_id in self._orders["orders"]:
                del self._orders["orders"][local_id]
                removed_count += 1

        if removed_count > 0:
            logger.info(
                f"Pruned {removed_count} orders from memory "
                f"(current: {len(self._orders['orders'])}, max: {self._max_in_memory_orders})"
            )

        return removed_count

    def _append_to_archive(
        self, archive_file: Path, new_orders: Dict[str, Any]
    ) -> None:
        """Helper to append orders to archive file synchronously."""
        try:
            archive_data = {"orders": {}, "metadata": {"last_archived": None}}

            if archive_file.exists():
                try:
                    with open(archive_file, "r") as f:
                        archive_data = json.load(f)
                except Exception:
                    logger.warning(
                        "Failed to load archive file, starting fresh archive"
                    )

            # Add new orders
            archive_data["orders"].update(new_orders)
            archive_data["metadata"]["last_archived"] = datetime.now(
                timezone.utc
            ).isoformat()

            archive_file.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write for archive with unique temp file.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(archive_file.parent),
                prefix=f"{archive_file.stem}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                json.dump(archive_data, f, indent=2, default=str)
                temp_path = Path(f.name)
            temp_path.replace(archive_file)

        except Exception as e:
            logger.error(f"Error appending to orders_archive.json: {e}", exc_info=True)

    def get_total_trades(self) -> int:
        """Get total number of closed trades."""
        return len(
            [o for o in self._orders["orders"].values() if o.get("status") == "CLOSED"]
        )

    def get_stats(self) -> Dict[str, Any]:
        """Get order statistics."""
        orders = self.get_all_orders()

        status_counts = {}
        for order in orders:
            status_counts[order.status] = status_counts.get(order.status, 0) + 1

        return {
            "total_orders": len(orders),
            "open_orders": len(self.get_open_orders()),
            "unverified_orders": len(self.get_unverified_orders()),
            "status_breakdown": status_counts,
            "last_updated": self._orders["metadata"].get("last_updated"),
        }


__all__ = ["OrderStateManager", "OrderRecord", "OrderRecordStatus"]
