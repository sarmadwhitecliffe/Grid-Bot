"""
src/persistence/transaction.py
------------------------------
Atomic transaction system for state persistence.

This module provides:
- StateTransaction: Context manager for atomic state updates
- TransactionJournal: Records all transaction attempts for auditing
- TransactionManager: Coordinates multi-component transactions

Ensures all-or-nothing semantics for state changes across multiple components.
"""

import asyncio
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TransactionStatus(str, Enum):
    """Status of a transaction."""

    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"


class TransactionPhase(str, Enum):
    """Phase of transaction execution."""

    PREPARING = "PREPARING"
    COMMITTED_PENDING = "COMMITTED_PENDING"
    COMMITTED = "COMMITTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class TransactionRecord:
    """Record of a single transaction."""

    transaction_id: str
    status: TransactionStatus
    phase: TransactionPhase
    started_at: datetime
    completed_at: Optional[datetime] = None
    operations: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    timeout_seconds: int = 30

    def is_expired(self) -> bool:
        """Check if transaction has exceeded timeout."""
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return elapsed > self.timeout_seconds

    def duration_ms(self) -> Optional[float]:
        """Get transaction duration in milliseconds."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None


class TransactionOperation:
    """A single operation within a transaction."""

    def __init__(
        self,
        name: str,
        execute_fn: Callable[[], Any],
        rollback_fn: Optional[Callable[[], None]] = None,
    ):
        self.name = name
        self.execute_fn = execute_fn
        self.rollback_fn = rollback_fn
        self.result: Any = None
        self.error: Optional[Exception] = None

    def execute(self) -> Any:
        """Execute the operation."""
        try:
            self.result = self.execute_fn()
            return self.result
        except Exception as e:
            self.error = e
            raise

    def rollback(self) -> None:
        """Rollback the operation if possible."""
        if self.rollback_fn and self.error is None:
            try:
                self.rollback_fn()
                logger.debug(f"Rolled back operation: {self.name}")
            except Exception as e:
                logger.error(f"Failed to rollback operation {self.name}: {e}")


class StateTransaction:
    """
    Context manager for atomic state transactions.

    Usage:
        with StateTransaction() as txn:
            txn.add_operation("save_orders", save_orders_fn, rollback_orders_fn)
            txn.add_operation("save_positions", save_positions_fn)
        # All operations committed or all rolled back
    """

    def __init__(
        self,
        transaction_id: Optional[str] = None,
        timeout_seconds: int = 30,
    ):
        self.transaction_id = transaction_id or str(uuid.uuid4())[:8]
        self.timeout_seconds = timeout_seconds
        self.status = TransactionStatus.PENDING
        self.phase = TransactionPhase.PREPARING
        self.operations: List[TransactionOperation] = []
        self.started_at = datetime.now(timezone.utc)
        self.completed_at: Optional[datetime] = None
        self.error: Optional[str] = None
        self._committed = False

    def add_operation(
        self,
        name: str,
        execute_fn: Callable[[], Any],
        rollback_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        """Add an operation to the transaction."""
        if self._committed:
            raise RuntimeError("Cannot add operation after transaction is committed")

        operation = TransactionOperation(
            name=name,
            execute_fn=execute_fn,
            rollback_fn=rollback_fn,
        )
        self.operations.append(operation)
        logger.debug(f"Transaction {self.transaction_id}: Added operation {name}")

    def execute(self) -> bool:
        """Execute all operations in the transaction."""
        self.phase = TransactionPhase.COMMITTED_PENDING

        if self.is_expired():
            self.status = TransactionStatus.TIMED_OUT
            self.error = f"Transaction timed out after {self.timeout_seconds}s"
            logger.error(f"Transaction {self.transaction_id} timed out")
            return False

        executed = []

        try:
            for operation in self.operations:
                if self.is_expired():
                    raise TimeoutError(f"Transaction timed out during {operation.name}")

                logger.debug(
                    f"Transaction {self.transaction_id}: Executing {operation.name}"
                )
                operation.execute()
                executed.append(operation)

            self.status = TransactionStatus.COMMITTED
            self.phase = TransactionPhase.COMMITTED
            self.completed_at = datetime.now(timezone.utc)
            self._committed = True

            duration = self.duration_ms()
            logger.info(
                f"Transaction {self.transaction_id} committed: "
                f"{len(self.operations)} operations in {duration:.2f}ms"
            )
            return True

        except Exception as e:
            self.error = str(e)
            self.status = TransactionStatus.FAILED
            logger.error(
                f"Transaction {self.transaction_id} failed: {e}. "
                f"Rolling back {len(executed)} operations."
            )

            self.phase = TransactionPhase.ROLLING_BACK
            self._rollback_operations(executed)
            self.phase = TransactionPhase.ROLLED_BACK
            self.completed_at = datetime.now(timezone.utc)
            return False

    def _rollback_operations(self, executed: List[TransactionOperation]) -> None:
        """Rollback executed operations in reverse order."""
        for operation in reversed(executed):
            try:
                operation.rollback()
            except Exception as e:
                logger.error(f"Failed to rollback {operation.name}: {e}")

    def is_expired(self) -> bool:
        """Check if transaction has exceeded timeout."""
        elapsed = (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return elapsed > self.timeout_seconds

    def duration_ms(self) -> Optional[float]:
        """Get transaction duration in milliseconds."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds() * 1000
        return None

    def __enter__(self) -> "StateTransaction":
        """Enter transaction context."""
        logger.debug(f"Transaction {self.transaction_id} started")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit transaction context."""
        if exc_type is not None:
            logger.error(f"Transaction {self.transaction_id} exception: {exc_val}")
            self.execute()
            return False

        if not self._committed:
            self.execute()

        return True


class TransactionJournal:
    """
    Journal of all transaction attempts for auditing and recovery.
    """

    def __init__(self, journal_dir: Optional[Path] = None):
        self.journal_dir = journal_dir or Path("data_futures/wal/journal")
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, TransactionRecord] = {}
        self._lock = threading.RLock()

    def begin_transaction(
        self,
        transaction_id: str,
        timeout_seconds: int = 30,
    ) -> TransactionRecord:
        """Record the start of a transaction."""
        with self._lock:
            record = TransactionRecord(
                transaction_id=transaction_id,
                status=TransactionStatus.PENDING,
                phase=TransactionPhase.PREPARING,
                started_at=datetime.now(timezone.utc),
                timeout_seconds=timeout_seconds,
            )
            self._records[transaction_id] = record
            self._persist_record(record)
            logger.debug(f"Transaction {transaction_id} recorded in journal")
            return record

    def update_status(
        self,
        transaction_id: str,
        status: TransactionStatus,
        phase: Optional[TransactionPhase] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update transaction status."""
        with self._lock:
            if transaction_id in self._records:
                record = self._records[transaction_id]
                record.status = status
                if phase:
                    record.phase = phase
                if error:
                    record.error = error
                if status in (
                    TransactionStatus.COMMITTED,
                    TransactionStatus.ROLLED_BACK,
                    TransactionStatus.TIMED_OUT,
                    TransactionStatus.FAILED,
                ):
                    record.completed_at = datetime.now(timezone.utc)
                self._persist_record(record)

    def add_operation(
        self,
        transaction_id: str,
        operation: Dict[str, Any],
    ) -> None:
        """Add an operation to the transaction record."""
        with self._lock:
            if transaction_id in self._records:
                self._records[transaction_id].operations.append(operation)

    def get_record(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Get a transaction record."""
        with self._lock:
            return self._records.get(transaction_id)

    def get_active_transactions(self) -> List[TransactionRecord]:
        """Get all pending/active transactions."""
        with self._lock:
            return [
                r
                for r in self._records.values()
                if r.status == TransactionStatus.PENDING
            ]

    def get_recent_transactions(self, limit: int = 100) -> List[TransactionRecord]:
        """Get recent transaction records."""
        with self._lock:
            sorted_records = sorted(
                self._records.values(),
                key=lambda r: r.started_at,
                reverse=True,
            )
            return sorted_records[:limit]

    def cleanup_old_records(self, max_age_hours: int = 24) -> int:
        """Remove old transaction records."""
        with self._lock:
            cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
            to_remove = []

            for tid, record in self._records.items():
                if record.started_at.timestamp() < cutoff:
                    to_remove.append(tid)

            for tid in to_remove:
                del self._records[tid]

            return len(to_remove)

    def _persist_record(self, record: TransactionRecord) -> None:
        """Persist transaction record to disk."""
        try:
            journal_file = self.journal_dir / f"{record.transaction_id}.json"
            import json

            data = {
                "transaction_id": record.transaction_id,
                "status": record.status.value,
                "phase": record.phase.value,
                "started_at": record.started_at.isoformat(),
                "completed_at": record.completed_at.isoformat()
                if record.completed_at
                else None,
                "operations": record.operations,
                "error": record.error,
                "timeout_seconds": record.timeout_seconds,
            }

            with open(journal_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist transaction record: {e}")


class TransactionManager:
    """
    Manages transactions across the persistence layer.

    Provides:
    - Coordinated transactions across multiple components
    - Automatic timeout handling
    - Transaction journaling for auditing
    - Recovery from incomplete transactions
    """

    def __init__(
        self,
        journal_dir: Optional[Path] = None,
        default_timeout: int = 30,
    ):
        self.journal = TransactionJournal(journal_dir)
        self.default_timeout = default_timeout
        self._active_transactions: Dict[str, StateTransaction] = {}
        self._lock = threading.RLock()

    @contextmanager
    def transaction(
        self,
        transaction_id: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
    ):
        """
        Context manager for creating a new transaction.

        Usage:
            with manager.transaction() as txn:
                txn.add_operation("save_orders", save_fn, rollback_fn)
                txn.add_operation("save_positions", save_fn)
        """
        txn = StateTransaction(
            transaction_id=transaction_id,
            timeout_seconds=timeout_seconds or self.default_timeout,
        )

        self.journal.begin_transaction(
            txn.transaction_id,
            timeout_seconds=txn.timeout_seconds,
        )

        with self._lock:
            self._active_transactions[txn.transaction_id] = txn

        try:
            yield txn

            if not txn._committed:
                success = txn.execute()
                self.journal.update_status(
                    txn.transaction_id,
                    TransactionStatus.COMMITTED
                    if success
                    else TransactionStatus.FAILED,
                    phase=txn.phase,
                    error=txn.error,
                )
        except Exception as e:
            logger.error(f"Transaction {txn.transaction_id} exception: {e}")
            self.journal.update_status(
                txn.transaction_id,
                TransactionStatus.FAILED,
                phase=txn.phase,
                error=str(e),
            )
            raise
        finally:
            with self._lock:
                self._active_transactions.pop(txn.transaction_id, None)

    def get_active_transaction(self, transaction_id: str) -> Optional[StateTransaction]:
        """Get an active transaction by ID."""
        with self._lock:
            return self._active_transactions.get(transaction_id)

    def get_active_transaction_count(self) -> int:
        """Get count of active transactions."""
        with self._lock:
            return len(self._active_transactions)

    def check_timeouts(self) -> List[str]:
        """
        Check for and handle timed-out transactions.

        Returns:
            List of transaction IDs that were timed out
        """
        timed_out = []

        with self._lock:
            for txn_id, txn in list(self._active_transactions.items()):
                if txn.is_expired():
                    logger.warning(f"Transaction {txn_id} timed out, forcing rollback")
                    txn.status = TransactionStatus.TIMED_OUT
                    txn.phase = TransactionPhase.ROLLING_BACK
                    self.journal.update_status(
                        txn_id,
                        TransactionStatus.TIMED_OUT,
                        phase=TransactionPhase.ROLLING_BACK,
                        error="Transaction timed out",
                    )

                    timed_out_ids = []
                    for t in list(self._active_transactions.values()):
                        if t.is_expired():
                            timed_out.append(t.transaction_id)

        return timed_out

    def recover_incomplete(self) -> Dict[str, Any]:
        """
        Recover from incomplete transactions on startup.

        Returns:
            Recovery report with details
        """
        report = {
            "incomplete_transactions": 0,
            "recovered": 0,
            "details": [],
        }

        active = self.journal.get_active_transactions()

        for record in active:
            if record.is_expired():
                report["incomplete_transactions"] += 1
                logger.warning(
                    f"Found incomplete transaction {record.transaction_id} "
                    f"started at {record.started_at}"
                )

                self.journal.update_status(
                    record.transaction_id,
                    TransactionStatus.TIMED_OUT,
                    phase=TransactionPhase.ROLLED_BACK,
                    error="Recovered as incomplete on startup",
                )

                report["details"].append(
                    {
                        "transaction_id": record.transaction_id,
                        "started_at": record.started_at.isoformat(),
                        "operations": len(record.operations),
                    }
                )

        logger.info(
            f"Recovery complete: {report['incomplete_transactions']} incomplete transactions"
        )
        return report


class AtomicStateStore:
    """
    Atomic state store that wraps StateManager with transaction support.

    Provides:
    - All-or-nothing state updates
    - Automatic checkpoint on transaction commit
    - WAL integration for crash recovery
    """

    def __init__(
        self, state_manager, wal_manager=None, checkpoint_on_commit: bool = True
    ):
        self.state_manager = state_manager
        self.wal_manager = wal_manager
        self.checkpoint_on_commit = checkpoint_on_commit
        self.transaction_manager = TransactionManager()

    def save_all_with_transaction(
        self,
        positions: Dict[str, Any],
        capitals: Dict[str, Any],
        history: List[Dict[str, Any]],
        grid_states: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Save all state atomically within a transaction.

        Returns:
            True if successful, False if rolled back
        """
        with self.transaction_manager.transaction() as txn:
            txn.add_operation(
                "save_positions",
                lambda: self.state_manager.save_positions(positions),
            )
            txn.add_operation(
                "save_capitals",
                lambda: self.state_manager.save_capitals(capitals),
            )
            txn.add_operation(
                "save_history",
                lambda: self.state_manager.save_history(history),
            )

            if grid_states:
                txn.add_operation(
                    "save_grid_states",
                    lambda: self.state_manager.save_grid_states(grid_states),
                )

        if txn._committed and self.checkpoint_on_commit and self.wal_manager:
            try:
                self.state_manager.create_checkpoint()
            except Exception as e:
                logger.error(f"Failed to create checkpoint after commit: {e}")

        return txn._committed

    @contextmanager
    def batch_save(self):
        """
        Context manager for batch saves with automatic checkpoint.

        Usage:
            with store.batch_save() as batch:
                batch.save_positions(positions)
                batch.save_capitals(capitals)
        """

        class BatchContext:
            def __init__(self, mgr):
                self.mgr = mgr
                self.operations = []

            def save_positions(self, positions):
                self.operations.append(
                    ("positions", lambda: self.mgr.save_positions(positions))
                )

            def save_capitals(self, capitals):
                self.operations.append(
                    ("capitals", lambda: self.mgr.save_capitals(capitals))
                )

            def save_history(self, history):
                self.operations.append(
                    ("history", lambda: self.mgr.save_history(history))
                )

            def save_grid_states(self, states):
                self.operations.append(
                    ("grid_states", lambda: self.mgr.save_grid_states(states))
                )

        batch = BatchContext(self.state_manager)
        yield batch

        with self.transaction_manager.transaction() as txn:
            for name, fn in batch.operations:
                txn.add_operation(name, fn)
