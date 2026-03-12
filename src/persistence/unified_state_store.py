"""
src/persistence/unified_state_store.py
----------------------------------
Unified state persistence combining all features.

This module provides:
- UnifiedStateStore: Single source of truth for all bot state
- Combines WAL, transactions, validation, checkpointing
- Migration from legacy state formats
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.persistence.wal import WALManager
from src.persistence.transaction import TransactionManager, AtomicStateStore
from src.persistence.validator import StateValidator, DataRecoveryManager
from src.persistence.integrity import IntegrityManager
from src.persistence.shutdown import GracefulShutdownHandler

logger = logging.getLogger(__name__)


class UnifiedStateStore:
    """
    Unified state persistence combining all features.

    Features:
    - Single source of truth for all state
    - WAL for crash recovery
    - Atomic transactions
    - Data validation and recovery
    - Integrity checksums
    - Graceful shutdown
    - Checkpoint management
    """

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        enable_wal: bool = True,
        enable_transactions: bool = True,
        enable_validation: bool = True,
        enable_integrity: bool = True,
    ):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.enable_wal = enable_wal
        self.enable_transactions = enable_transactions
        self.enable_validation = enable_validation
        self.enable_integrity = enable_integrity

        if self.enable_wal:
            self.wal_manager = WALManager(data_dir=self.data_dir)
        else:
            self.wal_manager = None

        if self.enable_transactions:
            self.transaction_manager = TransactionManager(
                journal_dir=self.data_dir / "wal" / "journal"
            )
            self.atomic_store = AtomicStateStore(
                state_manager=None,
                wal_manager=self.wal_manager,
                checkpoint_on_commit=True,
            )
        else:
            self.transaction_manager = None
            self.atomic_store = None

        if self.enable_validation:
            self.validator = StateValidator(data_dir=self.data_dir)
            self.recovery_manager = DataRecoveryManager(data_dir=self.data_dir)
        else:
            self.validator = None
            self.recovery_manager = None

        if self.enable_integrity:
            self.integrity_manager = IntegrityManager(data_dir=self.data_dir)
        else:
            self.integrity_manager = None

        self.shutdown_handler = GracefulShutdownHandler(data_dir=self.data_dir)

        self._state_cache: Dict[str, Any] = {}
        self._last_save: Optional[datetime] = None

        logger.info(
            f"UnifiedStateStore initialized: wal={enable_wal}, txn={enable_transactions}, val={enable_validation}, integrity={enable_integrity}"
        )

    def validate(self) -> Any:
        """Validate all state data."""
        if not self.enable_validation or not self.validator:
            return None
        return self.validator.validate_all()

    def reconcile_fills(self) -> Dict[str, Any]:
        """Reconcile fills to find orphaned entries."""
        if not self.enable_validation or not self.validator:
            return {}
        return self.validator.reconcile_fills()

    def checkpoint(self, label: Optional[str] = None) -> Optional[str]:
        """Create a checkpoint of current state."""
        if not self.enable_wal or not self.wal_manager:
            return None

        state_snapshot = self._collect_state_snapshot()
        checkpoint_id = self.wal_manager.create_checkpoint(state_snapshot)

        if self.enable_integrity and self.integrity_manager:
            self.integrity_manager.save_checksums()

        self._last_save = datetime.now(timezone.utc)

        logger.info(f"Checkpoint created: {checkpoint_id}")
        return checkpoint_id

    def restore(self, checkpoint_id: str) -> Dict[str, Any]:
        """Restore state from checkpoint."""
        if not self.enable_wal or not self.wal_manager:
            return {}

        return self.wal_manager.restore_checkpoint(checkpoint_id)

    def get_latest_checkpoint(self) -> Optional[str]:
        """Get the latest checkpoint ID."""
        if not self.enable_wal or not self.wal_manager:
            return None
        return self.wal_manager.get_latest_checkpoint()

    def verify_integrity(self) -> Dict[str, Any]:
        """Verify data integrity using checksums."""
        if not self.enable_integrity or not self.integrity_manager:
            return {"enabled": False}

        return self.integrity_manager.verify_and_repair()

    def get_health_status(self) -> Dict[str, Any]:
        """Get health status of the persistence layer."""
        status = {
            "enabled": True,
            "components": {
                "wal": self.enable_wal,
                "transactions": self.enable_transactions,
                "validation": self.enable_validation,
                "integrity": self.enable_integrity,
            },
            "last_save": self._last_save.isoformat() if self._last_save else None,
            "shutdown_state": self.shutdown_handler.get_state().value,
        }

        if self.enable_wal and self.wal_manager:
            status["wal_status"] = self.wal_manager.get_recovery_info()

        if self.enable_integrity and self.integrity_manager:
            integrity = self.integrity_manager.verify_all()
            status["integrity"] = {
                "valid": integrity.get("valid", False),
                "files_checked": integrity.get("files_checked", 0),
                "files_failed": integrity.get("files_failed", 0),
            }

        if self.shutdown_handler.was_crash():
            status["last_exit"] = "crash"
            if self.enable_validation and self.recovery_manager:
                validation = self.recovery_manager.validate_and_recover(
                    auto_reconcile=True
                )
                status["recovery"] = {
                    "performed": True,
                    "valid": validation.is_valid,
                    "issues": len(validation.issues),
                }
        else:
            status["last_exit"] = "clean"

        return status

    def _collect_state_snapshot(self) -> Dict[str, Any]:
        """Collect current state for checkpointing."""
        return {
            "positions": {},
            "capitals": {},
            "history": [],
            "grid_states": {},
            "grid_history": [],
            "exposure": {},
        }

    def shutdown(self) -> None:
        """Perform graceful shutdown."""
        self.shutdown_handler.create_shutdown_marker(
            success=True,
            reason="Normal shutdown",
        )


def create_unified_store(
    data_dir: Path = Path("data_futures"),
    **kwargs,
) -> UnifiedStateStore:
    """
    Factory function to create a UnifiedStateStore.

    Args:
        data_dir: Data directory
        **kwargs: Additional configuration options

    Returns:
        Configured UnifiedStateStore instance
    """
    store = UnifiedStateStore(data_dir=data_dir, **kwargs)

    health = store.get_health_status()
    if health.get("last_exit") == "crash":
        logger.warning("Previous exit was a crash, data recovery may be needed")

    return store
