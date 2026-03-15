"""
src/persistence/fill_logger.py
------------------------------
Fill logging system that wraps WAL and provides persistent fill records.

This module provides:
- FillLogger: Handles persistent fill logging with WAL integration
- Async fill logging support for non-blocking operations
- Fill replay for crash recovery
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from src.oms import FillRecord

logger = logging.getLogger(__name__)


class FillLogger:
    """
    Handles persistent fill logging with WAL integration.

    Provides:
    - Synchronous fill logging to WAL
    - Async fill logging for non-blocking writes
    - Fill replay for crash recovery
    - Query methods for fills by order ID
    """

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        wal_manager=None,
    ):
        self.data_dir = Path(data_dir)
        self.fill_log_file = self.data_dir / "fill_log.jsonl"
        self.wal_manager = wal_manager
        self._lock = threading.RLock()
        self._processed_fills: Set[str] = set()
        self._processed_fills_timestamps: Dict[str, datetime] = {}

        self.fill_log_file.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_fill_log_exists(self) -> None:
        """Ensure fill log file exists."""
        if not self.fill_log_file.exists():
            self.fill_log_file.touch()

    def log_fill(self, fill: FillRecord) -> None:
        """
        Log a fill to persistent storage.

        Args:
            fill: FillRecord to log
        """
        with self._lock:
            self._ensure_fill_log_exists()

            if self.wal_manager:
                try:
                    self.wal_manager.log_fill(fill.to_dict())
                except Exception as e:
                    logger.error(f"Failed to write fill to WAL: {e}")

            try:
                with open(self.fill_log_file, "a") as f:
                    f.write(json.dumps(fill.to_dict()) + "\n")
                logger.debug(f"Logged fill {fill.fill_id} for order {fill.order_id}")
            except Exception as e:
                logger.error(f"Failed to write fill to fill_log.jsonl: {e}")
                raise

            self._processed_fills.add(fill.order_id)
            self._processed_fills_timestamps[fill.order_id] = datetime.now(timezone.utc)

    async def log_fill_async(self, fill: FillRecord) -> None:
        """Log a fill asynchronously (non-blocking)."""
        await asyncio.to_thread(self.log_fill, fill)

    def is_fill_processed(self, order_id: str) -> bool:
        """
        Check if a fill has already been processed.

        Args:
            order_id: Order ID to check

        Returns:
            True if the fill has been processed
        """
        with self._lock:
            return order_id in self._processed_fills

    def mark_fill_processed(self, order_id: str) -> None:
        """
        Mark a fill as processed.

        Args:
            order_id: Order ID to mark
        """
        with self._lock:
            self._processed_fills.add(order_id)
            self._processed_fills_timestamps[order_id] = datetime.now(timezone.utc)

    def load_processed_fills(self) -> Set[str]:
        """
        Load processed fills from fill log on startup.

        Returns:
            Set of order IDs that have been processed
        """
        with self._lock:
            processed = set()
            if not self.fill_log_file.exists():
                return processed

            try:
                with open(self.fill_log_file, "r") as f:
                    for line in f:
                        try:
                            data = json.loads(line.strip())
                            order_id = data.get("order_id", "")
                            if order_id:
                                processed.add(order_id)
                        except json.JSONDecodeError:
                            continue

                self._processed_fills = processed
                logger.info(f"Loaded {len(processed)} processed fills from fill log")
                return processed
            except Exception as e:
                logger.error(f"Failed to load processed fills: {e}")
                return processed

    def get_fills_by_order(self, order_id: str) -> List[FillRecord]:
        """
        Get all fills for a specific order.

        Args:
            order_id: Order ID to query

        Returns:
            List of FillRecords for the order
        """
        fills = []
        if not self.fill_log_file.exists():
            return fills

        try:
            with open(self.fill_log_file, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        if data.get("order_id") == order_id:
                            fills.append(FillRecord.from_dict(data))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to query fills for order {order_id}: {e}")

        return fills

    def replay_fills(self, since_sequence: int = 0) -> List[FillRecord]:
        """
        Replay fills from WAL since a specific sequence.

        Args:
            since_sequence: WAL sequence number to replay from

        Returns:
            List of FillRecords to process
        """
        fills = []

        if not self.wal_manager:
            logger.warning("No WAL manager configured, skipping fill replay")
            return fills

        try:
            entries = self.wal_manager.wal.replay(since_sequence)
            for entry in entries:
                if entry.operation.value == "RECORD_FILL":
                    try:
                        fill = FillRecord.from_dict(entry.payload)
                        fills.append(fill)
                    except Exception as e:
                        logger.error(f"Failed to parse fill from WAL: {e}")
        except Exception as e:
            logger.error(f"Failed to replay fills from WAL: {e}")

        return fills

    def cleanup_old_fills(self, max_age_hours: int = 24) -> int:
        """
        Clean up old processed fill records to prevent unbounded growth.

        Args:
            max_age_hours: Maximum age in hours for processed fills

        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
            to_remove = []

            for order_id, timestamp in self._processed_fills_timestamps.items():
                if timestamp.timestamp() < cutoff:
                    to_remove.append(order_id)

            for order_id in to_remove:
                self._processed_fills.discard(order_id)
                self._processed_fills_timestamps.pop(order_id, None)

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old processed fills")

            return len(to_remove)

    def get_fill_count(self) -> int:
        """Get total number of fills in the log."""
        if not self.fill_log_file.exists():
            return 0

        try:
            with open(self.fill_log_file, "r") as f:
                return sum(1 for _ in f)
        except Exception as e:
            logger.error(f"Failed to count fills: {e}")
            return 0
