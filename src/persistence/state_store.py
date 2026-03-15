"""
src/persistence/state_store.py
-------------------------------
Atomic JSON persistence for bot state.

Writes use a write-then-rename strategy (temp file -> os.replace) that is
atomic on POSIX filesystems, preventing partial/corrupt state on crash.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from src.oms import FillRecord
from src.persistence.fill_logger import FillLogger

logger = logging.getLogger(__name__)


class StateStore:
    """
    Atomic JSON-based persistence for Grid Bot runtime state.

    State includes: centre_price, initial_equity, and the full OMS
    order registry (exported by OrderManager.export_state()).
    """

    def __init__(
        self, state_file: Path, fill_logger: Optional[FillLogger] = None
    ) -> None:
        """
        Initialise the StateStore.

        Args:
            state_file: Absolute path to the primary state JSON file.
            fill_logger: Optional FillLogger for persistent fill logging.
        """
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = state_file.with_suffix(".json.tmp")
        self.fill_logger = fill_logger

    def save(self, state: dict) -> None:
        """
        Atomically write state dict to disk.

        Adds a '_saved_at' ISO timestamp to the state before writing.
        Write order: 1) write temp file; 2) os.replace (atomic on POSIX).

        Args:
            state: JSON-serializable dict. Should include 'centre_price',
                   'initial_equity', and the 'orders' key from OMS export.
        """
        state["_saved_at"] = datetime.utcnow().isoformat()
        with open(self._tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(self._tmp, self.state_file)
        logger.debug("State saved -> %s", self.state_file)

    def load(self) -> Optional[dict]:
        """
        Load persisted state from disk.

        Returns:
            dict: Previously saved state, or None if no state file exists.

        Note:
            No exception is raised on corruption -- corrupted files are
            backed up and None is returned for a clean restart.
        """
        if not self.state_file.exists():
            logger.info("No existing state file — starting fresh.")
            return None
        with open(self.state_file) as f:
            try:
                state = json.load(f)
                logger.info(
                    "Loaded state from %s (saved at %s)",
                    self.state_file,
                    state.get("_saved_at"),
                )
                return state
            except json.JSONDecodeError as exc:
                logger.error(
                    "State file corrupted: %s — backing up and starting fresh.",
                    exc,
                )
                backup = self.state_file.with_suffix(".json.corrupted")
                self.state_file.rename(backup)
                return None

    def clear(self) -> None:
        """
        Delete the state file.

        Called after take-profit lock-in or emergency close to ensure
        the bot starts fresh next run rather than resuming a stale state.
        """
        if self.state_file.exists():
            self.state_file.unlink()
            logger.info("State file cleared.")

    def log_trade(self, trade: dict) -> None:
        """
        Append a fill event to the append-only trade log (.jsonl).

        Args:
            trade: Dict describing the fill event. A '_ts' UTC timestamp
                   is added automatically before writing.
        """
        if self.fill_logger:
            fill_record = FillRecord.from_dict(trade)
            self.fill_logger.log_fill(fill_record)

        log_path = self.state_file.parent / "trade_log.jsonl"
        trade["_ts"] = datetime.utcnow().isoformat()
        with open(log_path, "a") as f:
            f.write(json.dumps(trade, default=str) + "\n")

    def get_fills_by_order(self, order_id: str) -> List[FillRecord]:
        """
        Get all fills for a specific order.

        Args:
            order_id: Order ID to query

        Returns:
            List of FillRecords for the order
        """
        if self.fill_logger:
            return self.fill_logger.get_fills_by_order(order_id)
        return []

    def get_all_fills(self) -> List[FillRecord]:
        """
        Get all fill records.

        Returns:
            List of all FillRecords
        """
        fills = []
        log_path = self.state_file.parent / "fill_log.jsonl"
        if not log_path.exists():
            return fills

        try:
            with open(log_path, "r") as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        fills.append(FillRecord.from_dict(data))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"Failed to read fills: {e}")

        return fills
