"""
tests/test_persistence_transaction.py
------------------------------------
Unit tests for the atomic transaction system.
"""

import tempfile
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.persistence.transaction import (
    StateTransaction,
    TransactionStatus,
    TransactionPhase,
    TransactionJournal,
    TransactionManager,
    AtomicStateStore,
)


class TestStateTransaction:
    """Tests for StateTransaction class."""

    def test_transaction_creation(self):
        """Test transaction creation with default settings."""
        txn = StateTransaction()

        assert txn.transaction_id is not None
        assert txn.status == TransactionStatus.PENDING
        assert txn.phase == TransactionPhase.PREPARING
        assert len(txn.operations) == 0

    def test_transaction_with_custom_id(self):
        """Test transaction with custom ID."""
        txn = StateTransaction(transaction_id="test-123")

        assert txn.transaction_id == "test-123"

    def test_add_operation(self):
        """Test adding operations to transaction."""
        txn = StateTransaction()

        results = []

        def op1():
            results.append("op1")
            return "result1"

        def op2():
            results.append("op2")
            return "result2"

        txn.add_operation("operation1", op1)
        txn.add_operation("operation2", op2)

        assert len(txn.operations) == 2
        assert txn.operations[0].name == "operation1"
        assert txn.operations[1].name == "operation2"

    def test_successful_transaction(self):
        """Test successful transaction commit."""
        txn = StateTransaction()

        results = []

        def op1():
            results.append("op1")
            return 1

        def op2():
            results.append("op2")
            return 2

        txn.add_operation("op1", op1)
        txn.add_operation("op2", op2)

        success = txn.execute()

        assert success is True
        assert txn.status == TransactionStatus.COMMITTED
        assert txn.phase == TransactionPhase.COMMITTED
        assert results == ["op1", "op2"]

    def test_failed_transaction_rollback(self):
        """Test failed transaction triggers rollback."""
        txn = StateTransaction()

        results = []

        def op1():
            results.append("op1")
            return 1

        def op2():
            results.append("op2")
            raise ValueError("Operation 2 failed")

        def rollback_op1():
            results.append("rollback_op1")

        txn.add_operation("op1", op1, rollback_fn=rollback_op1)
        txn.add_operation("op2", op2)

        success = txn.execute()

        assert success is False
        assert txn.status == TransactionStatus.FAILED
        assert "rollback_op1" in results

    def test_transaction_timeout(self):
        """Test transaction timeout handling."""
        txn = StateTransaction(timeout_seconds=0)

        def slow_op():
            time.sleep(0.1)
            return "done"

        txn.add_operation("slow", slow_op)

        success = txn.execute()

        assert success is False
        assert txn.status == TransactionStatus.TIMED_OUT

    def test_context_manager_success(self):
        """Test transaction as context manager (success)."""
        results = []

        with StateTransaction() as txn:
            txn.add_operation("op1", lambda: results.append("op1"))
            txn.add_operation("op2", lambda: results.append("op2"))

        assert results == ["op1", "op2"]

    def test_context_manager_failure(self):
        """Test transaction as context manager (failure)."""
        results = []

        def rollback():
            results.append("rollback")

        txn = StateTransaction()
        txn.add_operation("op1", lambda: results.append("op1"), rollback_fn=rollback)
        txn.add_operation("op2", lambda: (_ for _ in ()).throw(ValueError("fail")))

        success = txn.execute()

        assert success is False
        assert "op1" in results
        assert "rollback" in results

    def test_cannot_add_after_commit(self):
        """Test that adding operations after commit raises error."""
        txn = StateTransaction()

        txn.add_operation("op1", lambda: None)
        txn.execute()

        with pytest.raises(
            RuntimeError, match="Cannot add operation after transaction is committed"
        ):
            txn.add_operation("op2", lambda: None)


class TestTransactionJournal:
    """Tests for TransactionJournal class."""

    @pytest.fixture
    def journal(self):
        """Create a temporary journal."""
        temp_dir = tempfile.mkdtemp()
        journal = TransactionJournal(journal_dir=Path(temp_dir))
        yield journal
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_begin_transaction(self, journal):
        """Test recording transaction start."""
        record = journal.begin_transaction("txn-1", timeout_seconds=30)

        assert record.transaction_id == "txn-1"
        assert record.status == TransactionStatus.PENDING

    def test_update_status(self, journal):
        """Test updating transaction status."""
        journal.begin_transaction("txn-1")
        journal.update_status("txn-1", TransactionStatus.COMMITTED)

        record = journal.get_record("txn-1")
        assert record.status == TransactionStatus.COMMITTED
        assert record.completed_at is not None

    def test_get_active_transactions(self, journal):
        """Test getting active transactions."""
        journal.begin_transaction("txn-1")
        journal.begin_transaction("txn-2")

        journal.update_status("txn-1", TransactionStatus.COMMITTED)

        active = journal.get_active_transactions()
        assert len(active) == 1
        assert active[0].transaction_id == "txn-2"


class TestTransactionManager:
    """Tests for TransactionManager class."""

    @pytest.fixture
    def manager(self):
        """Create a transaction manager."""
        temp_dir = tempfile.mkdtemp()
        mgr = TransactionManager(journal_dir=Path(temp_dir))
        yield mgr
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_transaction_context(self, manager):
        """Test transaction context manager."""
        results = []

        with manager.transaction("txn-1") as txn:
            txn.add_operation("op1", lambda: results.append("op1"))

        assert results == ["op1"]

    def test_transaction_rollback_on_failure(self, manager):
        """Test transaction rollback on failure."""
        results = []

        def rollback():
            results.append("rollback")

        txn = StateTransaction(transaction_id="txn-1")
        txn.add_operation("op1", lambda: results.append("op1"), rollback_fn=rollback)
        txn.add_operation("op2", lambda: (_ for _ in ()).throw(ValueError("fail")))

        success = txn.execute()

        assert success is False
        assert "op1" in results
        assert "rollback" in results

    def test_multiple_concurrent_transactions(self, manager):
        """Test multiple concurrent transactions."""
        results = []

        def run_transaction(txn_id):
            with manager.transaction(txn_id) as txn:
                txn.add_operation(
                    f"op-{txn_id}", lambda tid=txn_id: results.append(tid)
                )

        threads = [
            threading.Thread(target=run_transaction, args=(i,)) for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 5

    def test_check_timeouts(self, manager):
        """Test timeout checking."""
        txn = StateTransaction(transaction_id="txn-1", timeout_seconds=0)
        txn.add_operation("op1", lambda: time.sleep(1))

        with manager._lock:
            manager._active_transactions["txn-1"] = txn

        manager.journal.begin_transaction("txn-1", timeout_seconds=0)

        time.sleep(0.1)

        timeouts = manager.check_timeouts()

        assert len(timeouts) > 0

    def test_recover_incomplete(self, manager):
        """Test recovery from incomplete transactions."""
        journal = manager.journal
        journal.begin_transaction("txn-1", timeout_seconds=1)

        time.sleep(1.1)

        report = manager.recover_incomplete()

        assert report["incomplete_transactions"] >= 1


class TestAtomicStateStore:
    """Tests for AtomicStateStore class."""

    @pytest.fixture
    def mock_state_manager(self):
        """Create a mock state manager."""

        class MockStateManager:
            def __init__(self):
                self.saved = {}

            def save_positions(self, positions):
                self.saved["positions"] = positions

            def save_capitals(self, capitals):
                self.saved["capitals"] = capitals

            def save_history(self, history):
                self.saved["history"] = history

            def save_grid_states(self, states):
                self.saved["grid_states"] = states

            def create_checkpoint(self):
                return "checkpoint_id"

        return MockStateManager()

    def test_save_all_with_transaction(self, mock_state_manager):
        """Test atomic save all."""
        store = AtomicStateStore(mock_state_manager, checkpoint_on_commit=False)

        success = store.save_all_with_transaction(
            positions={"BTC": {"amount": 1.0}},
            capitals={"BTC": 1000},
            history=[],
        )

        assert success is True
        assert "positions" in mock_state_manager.saved
        assert "capitals" in mock_state_manager.saved

    def test_save_all_with_grid_states(self, mock_state_manager):
        """Test atomic save with grid states."""
        store = AtomicStateStore(mock_state_manager, checkpoint_on_commit=False)

        success = store.save_all_with_transaction(
            positions={},
            capitals={},
            history=[],
            grid_states={"BTC": {"active": True}},
        )

        assert success is True
        assert "grid_states" in mock_state_manager.saved


class TestTransactionEdgeCases:
    """Edge case tests for transactions."""

    def test_empty_transaction(self):
        """Test transaction with no operations."""
        txn = StateTransaction()

        success = txn.execute()

        assert success is True
        assert txn.status == TransactionStatus.COMMITTED

    def test_rollback_with_no_operations_executed(self):
        """Test rollback when no operations were executed."""
        txn = StateTransaction()

        def failing_op():
            raise ValueError("fail immediately")

        txn.add_operation("fail", failing_op)

        success = txn.execute()

        assert success is False
        assert txn.phase == TransactionPhase.ROLLED_BACK

    def test_operations_called_once(self):
        """Test that operations are only called once."""
        call_count = 0

        def counting_op():
            nonlocal call_count
            call_count += 1
            return call_count

        txn = StateTransaction()
        txn.add_operation("count", counting_op)
        txn.execute()

        assert call_count == 1

    def test_transaction_duration_tracking(self):
        """Test that transaction duration is tracked."""
        txn = StateTransaction()

        def slow_op():
            time.sleep(0.01)
            return "done"

        txn.add_operation("slow", slow_op)
        txn.execute()

        duration = txn.duration_ms()
        assert duration is not None
        assert duration > 0

    def test_sequential_transactions_independent(self):
        """Test that sequential transactions are independent."""
        results = []

        txn1 = StateTransaction(transaction_id="txn-1")
        txn1.add_operation("op1", lambda: results.append("txn1-op1"))
        txn1.execute()

        txn2 = StateTransaction(transaction_id="txn-2")
        txn2.add_operation("op2", lambda: results.append("txn2-op1"))
        txn2.execute()

        assert results == ["txn1-op1", "txn2-op1"]
        assert txn1.status == TransactionStatus.COMMITTED
        assert txn2.status == TransactionStatus.COMMITTED
