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
from typing import Optional

logger = logging.getLogger(__name__)


class StateStore:
    """
    Atomic JSON-based persistence for Grid Bot runtime state.

    State includes: centre_price, initial_equity, and the full OMS
    order registry (exported by OrderManager.export_state()).
    """

    def __init__(self, state_file: Path) -> None:
        """
        Initialise the StateStore.

        Args:
            state_file: Absolute path to the primary state JSON file.
        """
        self.state_file = state_file
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = state_file.with_suffix(".json.tmp")

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
        log_path = self.state_file.parent / "trade_log.jsonl"
        trade["_ts"] = datetime.utcnow().isoformat()
        with open(log_path, "a") as f:
            f.write(json.dumps(trade, default=str) + "\n")
