"""
tests/test_persistence_wal.py
-----------------------------
Unit tests for the Write-Ahead Logging (WAL) system.
"""

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

import pytest

from src.persistence.wal import (
    WALEntry,
    WALOperationType,
    WriteAheadLog,
    WALManager,
)


@pytest.fixture
def temp_wal_dir():
    """Create a temporary WAL directory for testing."""
    temp_dir = tempfile.mkdtemp()
    wal_path = Path(temp_dir)
    yield wal_path
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def wal(temp_wal_dir):
    """Create a WriteAheadLog instance for testing."""
    return WriteAheadLog(
        wal_dir=temp_wal_dir,
        max_file_size=1024 * 1024,  # 1MB
        max_entries=100,
    )


@pytest.fixture
def wal_manager():
    """Create a WALManager instance for testing."""
    temp_dir = tempfile.mkdtemp()
    try:
        data_dir = Path(temp_dir) / "data"
        data_dir.mkdir(exist_ok=True)
        manager = WALManager(data_dir=data_dir)
        yield manager
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class TestWALEntry:
    """Tests for WALEntry class."""

    def test_entry_creation(self):
        """Test WALEntry creation with automatic checksum."""
        entry = WALEntry(
            operation=WALOperationType.CREATE_ORDER,
            payload={"order_id": "test_123", "side": "buy"},
        )

        assert entry.operation == WALOperationType.CREATE_ORDER
        assert entry.payload["order_id"] == "test_123"
        assert entry.checksum != ""
        assert entry.sequence == 0

    def test_checksum_verification(self):
        """Test checksum verification."""
        entry = WALEntry(
            operation=WALOperationType.RECORD_FILL,
            payload={"fill_id": "fill_456"},
        )

        assert entry.verify() is True

    def test_checksum_failure_on_modification(self):
        """Test that checksum fails after modification."""
        entry = WALEntry(
            operation=WALOperationType.CREATE_ORDER,
            payload={"order_id": "test_123"},
        )

        entry.payload["order_id"] = "modified"
        assert entry.verify() is False

    def test_serialization_json(self):
        """Test entry serialization to JSON."""
        entry = WALEntry(
            operation=WALOperationType.RECORD_FILL,
            payload={"fill_id": "fill_456", "price": 100.5},
            sequence=42,
        )

        json_str = entry.to_json()
        assert "operation" in json_str
        assert "RECORD_FILL" in json_str

    def test_deserialization_json(self):
        """Test entry deserialization from JSON."""
        original = WALEntry(
            operation=WALOperationType.UPDATE_ORDER,
            payload={"order_id": "test_789", "status": "filled"},
            sequence=10,
        )

        json_str = original.to_json()
        restored = WALEntry.from_json(json_str)

        assert restored.operation == original.operation
        assert restored.payload == original.payload
        assert restored.sequence == original.sequence
        assert restored.checksum == original.checksum
        assert restored.verify() is True


class TestWriteAheadLog:
    """Tests for WriteAheadLog class."""

    def test_append_single_entry(self, wal):
        """Test appending a single entry."""
        seq = wal.append(
            WALOperationType.CREATE_ORDER,
            {"order_id": "order_001", "side": "buy"},
        )

        assert seq == 0

    def test_append_multiple_entries(self, wal):
        """Test appending multiple entries."""
        for i in range(5):
            seq = wal.append(
                WALOperationType.CREATE_ORDER,
                {"order_id": f"order_{i:03d}"},
            )
            assert seq == i

    def test_replay_entries(self, wal):
        """Test replaying WAL entries."""
        for i in range(3):
            wal.append(
                WALOperationType.RECORD_FILL,
                {"fill_id": f"fill_{i}", "price": 100 + i},
            )

        entries = wal.replay(0)
        assert len(entries) == 3
        assert entries[0].payload["fill_id"] == "fill_0"
        assert entries[1].payload["fill_id"] == "fill_1"
        assert entries[2].payload["fill_id"] == "fill_2"

    def test_replay_from_sequence(self, wal):
        """Test replaying from a specific sequence."""
        for i in range(5):
            wal.append(
                WALOperationType.RECORD_FILL,
                {"fill_id": f"fill_{i}"},
            )

        entries = wal.replay(2)
        assert len(entries) == 3
        assert entries[0].sequence == 2

    def test_get_last_sequence(self, wal):
        """Test getting the last sequence number."""
        for i in range(3):
            wal.append(
                WALOperationType.CREATE_ORDER,
                {"order_id": f"order_{i}"},
            )

        assert wal.get_last_sequence() == 3

    def test_concurrent_writes(self, temp_wal_dir):
        """Test concurrent writes from multiple threads."""
        wal = WriteAheadLog(wal_dir=temp_wal_dir, max_entries=1000)
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(50):
                    wal.append(
                        WALOperationType.RECORD_FILL,
                        {"thread": thread_id, "i": i},
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        entries = wal.replay(0)
        assert len(entries) == 200

    def test_archive_current(self, wal):
        """Test archiving current WAL files."""
        for i in range(5):
            wal.append(
                WALOperationType.CREATE_ORDER,
                {"order_id": f"order_{i}"},
            )

        archived = wal.archive_current()
        assert len(archived) > 0
        assert all(str(p).endswith(".gz") for p in archived)

    def test_truncate_to_sequence(self, wal):
        """Test truncating WAL to a specific sequence."""
        for i in range(10):
            wal.append(
                WALOperationType.RECORD_FILL,
                {"fill_id": f"fill_{i}"},
            )

        removed = wal.truncate_to_sequence(4)
        entries = wal.replay(0)

        assert removed > 0
        assert len(entries) <= 5
        assert all(e.sequence <= 4 for e in entries)

    def test_corrupt_entry_handling(self, temp_wal_dir):
        """Test handling of corrupt WAL entries."""
        wal = WriteAheadLog(wal_dir=temp_wal_dir)

        wal.append(
            WALOperationType.CREATE_ORDER,
            {"order_id": "valid_1"},
        )

        corrupt_file = temp_wal_dir / "current" / "wal_corrupt.log"
        corrupt_file.parent.mkdir(parents=True, exist_ok=True)
        with open(corrupt_file, "w") as f:
            f.write('{"invalid": "json"\n')
            f.write(
                '{"operation": "VALID", "payload": {}, "timestamp": "2026-01-01", "sequence": 99, "checksum": "abc"}\n'
            )

        entries = wal.replay(0)
        assert len(entries) == 1
        assert entries[0].payload["order_id"] == "valid_1"


class TestWALManager:
    """Tests for WALManager class."""

    def test_log_order_create(self, wal_manager):
        """Test logging order creation."""
        seq = wal_manager.log_order_create(
            order_id="order_001",
            order_data={"side": "buy", "price": 50000},
        )
        assert seq == 0

    def test_log_order_update(self, wal_manager):
        """Test logging order update."""
        seq = wal_manager.log_order_update(
            order_id="order_001",
            updates={"status": "filled"},
        )
        assert seq == 0

    def test_log_fill(self, wal_manager):
        """Test logging fill event."""
        seq = wal_manager.log_fill(
            {
                "fill_id": "fill_001",
                "symbol": "BTC/USDT",
                "side": "buy",
                "price": 50000,
                "amount": 0.1,
            }
        )
        assert seq == 0

    def test_log_position_operations(self, wal_manager):
        """Test logging position operations."""
        seq1 = wal_manager.log_position_create("pos_001", {"size": 1.0})
        seq2 = wal_manager.log_position_update("pos_001", {"size": 0.5})
        seq3 = wal_manager.log_position_delete("pos_001")

        assert seq1 == 0
        assert seq2 == 1
        assert seq3 == 2

    def test_create_checkpoint(self, wal_manager):
        """Test checkpoint creation."""
        state = {
            "orders": {"order_001": {"status": "open"}},
            "positions": {"pos_001": {"size": 1.0}},
        }

        checkpoint_id = wal_manager.create_checkpoint(state)
        assert checkpoint_id.startswith("chk_")

        checkpoint_path = wal_manager.checkpoint_dir / checkpoint_id
        assert checkpoint_path.exists()
        assert (checkpoint_path / "orders.json").exists()
        assert (checkpoint_path / "positions.json").exists()

    def test_restore_checkpoint(self, wal_manager):
        """Test checkpoint restoration."""
        state = {
            "orders": {"order_001": {"status": "filled"}},
            "capitals": {"BTC/USDT": 1000},
        }

        checkpoint_id = wal_manager.create_checkpoint(state)
        restored = wal_manager.restore_checkpoint(checkpoint_id)

        assert "orders" in restored
        assert restored["orders"]["order_001"]["status"] == "filled"
        assert restored["capitals"]["BTC/USDT"] == 1000

    def test_get_latest_checkpoint(self):
        """Test getting the latest checkpoint."""
        temp_dir = tempfile.mkdtemp()
        try:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir(exist_ok=True)
            manager = WALManager(data_dir=data_dir)

            assert manager.get_latest_checkpoint() is None

            manager.create_checkpoint({"test": {"key": "value1"}})
            manager.create_checkpoint({"test": {"key": "value2"}})
            manager.create_checkpoint({"test": {"key": "value3"}})

            latest = manager.get_latest_checkpoint()
            assert latest is not None
            assert latest.startswith("chk_")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_checkpoint_cleanup(self, wal_manager):
        """Test that old checkpoints are cleaned up."""
        for i in range(7):
            wal_manager.create_checkpoint({"data": {"i": i}})

        checkpoints = list(wal_manager.checkpoint_dir.iterdir())
        assert len(checkpoints) <= 5

    def test_get_recovery_info(self):
        """Test getting recovery information."""
        temp_dir = tempfile.mkdtemp()
        try:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir(exist_ok=True)
            manager = WALManager(data_dir=data_dir)

            for i in range(3):
                manager.log_order_create(f"order_{i}", {"side": "buy"})

            manager.create_checkpoint({"test": {}})

            info = manager.get_recovery_info()
            assert info["last_sequence"] == 4  # 3 orders + 1 checkpoint
            assert info["latest_checkpoint"] is not None
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestWALIntegration:
    """Integration tests for WAL system."""

    def test_full_recovery_cycle(self):
        """Test complete recovery cycle: create entries -> checkpoint -> restore."""
        temp_dir = tempfile.mkdtemp()
        try:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir(exist_ok=True)

            manager = WALManager(data_dir=data_dir)

            manager.log_order_create("order_001", {"side": "buy", "price": 50000})
            manager.log_order_create("order_002", {"side": "sell", "price": 51000})
            manager.log_fill({"fill_id": "fill_001", "price": 50000})

            state = {
                "orders": {
                    "order_001": {"side": "buy", "price": 50000, "status": "open"},
                    "order_002": {"side": "sell", "price": 51000, "status": "open"},
                },
                "fills": [
                    {"fill_id": "fill_001", "price": 50000},
                ],
            }

            checkpoint_id = manager.create_checkpoint(state)

            restored = manager.restore_checkpoint(checkpoint_id)
            assert len(restored["orders"]) == 2
            assert len(restored["fills"]) == 1

            entries = manager.wal.replay(0)
            assert len(entries) == 4  # 3 entries + 1 checkpoint
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sequence_tracking_after_rotation(self, temp_wal_dir):
        """Test that sequence numbers are maintained across rotations."""
        wal = WriteAheadLog(
            wal_dir=temp_wal_dir,
            max_entries=5,
        )

        for i in range(10):
            wal.append(WALOperationType.RECORD_FILL, {"i": i})

        entries = wal.replay(0)
        assert len(entries) == 10

        sequences = [e.sequence for e in entries]
        assert sequences == list(range(10))

    def test_empty_wal_recovery(self):
        """Test recovery from empty WAL."""
        temp_dir = tempfile.mkdtemp()
        try:
            data_dir = Path(temp_dir) / "data"
            data_dir.mkdir(exist_ok=True)

            manager = WALManager(data_dir=data_dir)

            info = manager.get_recovery_info()
            assert info["last_sequence"] == 0

            entries = manager.wal.replay(0)
            assert len(entries) == 0
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
