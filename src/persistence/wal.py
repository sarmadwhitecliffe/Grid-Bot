"""
src/persistence/wal.py
-----------------------
Write-Ahead Logging (WAL) system for atomic state persistence.

This module provides:
- WALEntry: Structured log entries with checksums
- WriteAheadLog: Low-level append-only file operations
- WALManager: High-level operations including replay, recovery, and rotation

All WAL entries are append-only to prevent data loss during crashes.
Each entry includes a SHA-256 checksum for integrity verification.
"""

import gzip
import hashlib
import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


class WALOperationType(str, Enum):
    """Types of operations that can be recorded in the WAL."""

    CREATE_ORDER = "CREATE_ORDER"
    UPDATE_ORDER = "UPDATE_ORDER"
    DELETE_ORDER = "DELETE_ORDER"
    RECORD_FILL = "RECORD_FILL"
    CREATE_POSITION = "CREATE_POSITION"
    UPDATE_POSITION = "UPDATE_POSITION"
    DELETE_POSITION = "DELETE_POSITION"
    SAVE_STATE = "SAVE_STATE"
    SAVE_CAPITAL = "SAVE_CAPITAL"
    SAVE_GRID_STATE = "SAVE_GRID_STATE"
    CHECKPOINT = "CHECKPOINT"


@dataclass
class WALEntry:
    """A single Write-Ahead Log entry with integrity verification."""

    operation: WALOperationType
    payload: Dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    sequence: int = 0
    checksum: str = ""

    def __post_init__(self):
        if not self.checksum:
            self.checksum = self._calculate_checksum()

    def _calculate_checksum(self) -> str:
        """Calculate SHA-256 checksum of the entry (excluding checksum field)."""
        data = {
            "operation": self.operation.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
        }
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def verify(self) -> bool:
        """Verify entry integrity using checksum."""
        return self.checksum == self._calculate_checksum()

    def to_json(self) -> str:
        """Serialize entry to JSON line format."""
        data = {
            "operation": self.operation.value,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "checksum": self.checksum,
        }
        return json.dumps(data, default=str)

    @classmethod
    def from_json(cls, line: str) -> "WALEntry":
        """Deserialize entry from JSON line format."""
        data = json.loads(line)
        return cls(
            operation=WALOperationType(data["operation"]),
            payload=data["payload"],
            timestamp=data["timestamp"],
            sequence=data["sequence"],
            checksum=data["checksum"],
        )


class WriteAheadLog:
    """
    Low-level append-only WAL implementation.

    Provides thread-safe write operations and basic read/iterate functionality.
    Uses file locking to prevent concurrent write issues.
    """

    MAX_ENTRY_SIZE = 1024 * 1024  # 1MB max entry size
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB max file size before rotation
    MAX_ENTRIES = 1000  # Max entries before rotation
    MAX_WAL_FILES = 10  # Max WAL files to keep

    def __init__(
        self,
        wal_dir: Path,
        max_file_size: int = MAX_FILE_SIZE,
        max_entries: int = MAX_ENTRIES,
    ):
        """
        Initialize the WriteAheadLog.

        Args:
            wal_dir: Directory for WAL files
            max_file_size: Max size in bytes before rotation
            max_entries: Max entries before rotation
        """
        self.wal_dir = Path(wal_dir)
        self.current_dir = self.wal_dir / "current"
        self.archive_dir = self.wal_dir / "archive"
        self.corrupt_dir = self.wal_dir / "corrupt"
        self.max_file_size = max_file_size
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._sequence = 0
        self._current_file: Optional[Path] = None
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create WAL directories if they don't exist."""
        for directory in [
            self.wal_dir,
            self.current_dir,
            self.archive_dir,
            self.corrupt_dir,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def _get_current_file(self) -> Path:
        """Get or create the current WAL file."""
        if self._current_file and self._current_file.exists():
            return self._current_file

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._current_file = self.current_dir / f"wal_{timestamp}.log"

        if self._current_file.exists():
            last_seq = self._get_last_sequence_from_file(self._current_file)
            if last_seq > self._sequence:
                self._sequence = last_seq

        return self._current_file

    def _get_last_sequence_from_file(self, file_path: Path) -> int:
        """Get the last sequence number from a WAL file."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_entry = WALEntry.from_json(lines[-1].strip())
                    return last_entry.sequence + 1
        except Exception:
            pass
        return 0

    def _should_rotate(self, file_path: Path) -> bool:
        """Check if WAL file should be rotated."""
        if not file_path.exists():
            return True

        file_size = file_path.stat().st_size
        entry_count = sum(1 for _ in open(file_path, "r")) if file_size > 0 else 0

        return file_size >= self.max_file_size or entry_count >= self.max_entries

    def _rotate_if_needed(self) -> None:
        """Rotate WAL file if size/entry limits exceeded."""
        current = self._get_current_file()
        if self._should_rotate(current):
            self._current_file = None
            logger.info(f"WAL rotation triggered for {current}")
            self._cleanup_old_wal_files()

    def _cleanup_old_wal_files(self, keep: int = None) -> None:
        """Remove old WAL files, keeping only the most recent ones."""
        if keep is None:
            keep = self.MAX_WAL_FILES

        wal_files = sorted(
            self.current_dir.glob("wal_*.log"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        removed = 0
        for old_file in wal_files[keep:]:
            try:
                old_file.unlink()
                removed += 1
                logger.debug(f"Removed old WAL file: {old_file.name}")
            except Exception as e:
                logger.error(f"Failed to remove old WAL file {old_file}: {e}")

        if removed > 0:
            logger.info(
                f"Cleaned up {removed} old WAL files, kept {min(keep, len(wal_files))}"
            )

    def append(self, operation: WALOperationType, payload: Dict[str, Any]) -> int:
        """
        Append an entry to the WAL.

        Args:
            operation: Type of operation
            payload: Operation data

        Returns:
            Sequence number of the appended entry

        Raises:
            IOError: If write fails
        """
        with self._lock:
            self._rotate_if_needed()
            file_path = self._get_current_file()

            entry = WALEntry(
                operation=operation,
                payload=payload,
                sequence=self._sequence,
            )

            try:
                with open(file_path, "a", encoding="utf-8") as f:
                    f.write(entry.to_json() + "\n")
                    f.flush()
                    os.fsync(f.fileno())

                self._sequence += 1
                logger.debug(f"WAL append: {operation.value} seq={entry.sequence}")
                return entry.sequence

            except Exception as e:
                logger.error(f"Failed to append WAL entry: {e}")
                raise IOError(f"WAL append failed: {e}") from e

    def iterate(self, since_sequence: int = 0) -> Generator[WALEntry, None, None]:
        """
        Iterate over WAL entries from a given sequence number.

        Args:
            since_sequence: Starting sequence number (inclusive)

        Yields:
            WALEntry objects in order
        """
        with self._lock:
            files = sorted(self.current_dir.glob("wal_*.log"))
            for file_path in files:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = WALEntry.from_json(line)
                                if entry.sequence >= since_sequence:
                                    yield entry
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    f"Corrupted WAL entry in {file_path}: {e}"
                                )
                                self._handle_corrupt_entry(file_path, line)
                            except Exception as e:
                                logger.error(f"Error parsing WAL entry: {e}")
                except Exception as e:
                    logger.error(f"Error reading WAL file {file_path}: {e}")

    def replay(self, since_sequence: int = 0) -> List[WALEntry]:
        """
        Replay all WAL entries since the given sequence number.

        Args:
            since_sequence: Starting sequence number (inclusive)

        Returns:
            List of WAL entries
        """
        return list(self.iterate(since_sequence))

    def get_last_sequence(self) -> int:
        """Get the last sequence number in the WAL."""
        with self._lock:
            files = sorted(self.current_dir.glob("wal_*.log"))
            if not files:
                return 0

            last_file = files[-1]
            try:
                with open(last_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if lines:
                        last_entry = WALEntry.from_json(lines[-1].strip())
                        return last_entry.sequence + 1
            except Exception as e:
                logger.error(f"Error getting last sequence: {e}")

            return 0

    def _handle_corrupt_entry(self, file_path: Path, line: str) -> None:
        """Move corrupt entry to corrupt directory."""
        try:
            corrupt_file = (
                self.corrupt_dir
                / f"{file_path.stem}_corrupt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
            )
            with open(corrupt_file, "w", encoding="utf-8") as f:
                f.write(line + "\n")
            logger.warning(f"Moved corrupt WAL entry to {corrupt_file}")
        except Exception as e:
            logger.error(f"Failed to handle corrupt entry: {e}")

    def archive_current(self) -> List[Path]:
        """
        Archive current WAL files to archive directory.

        Returns:
            List of archived file paths
        """
        archived = []
        with self._lock:
            for file_path in self.current_dir.glob("wal_*.log"):
                try:
                    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                    archived_name = f"{file_path.stem}_{timestamp}.log.gz"
                    archived_path = self.archive_dir / archived_name

                    with open(file_path, "rb") as f_in:
                        with gzip.open(archived_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)

                    file_path.unlink()
                    archived.append(archived_path)
                    logger.info(f"Archived WAL: {file_path} -> {archived_path}")
                except Exception as e:
                    logger.error(f"Failed to archive WAL file {file_path}: {e}")

        return archived

    def recover_from_archive(
        self, entry_filter: Optional[Callable[[WALEntry], bool]] = None
    ) -> List[WALEntry]:
        """
        Recover entries from archived WAL files.

        Args:
            entry_filter: Optional filter function to select specific entries

        Returns:
            List of recovered entries
        """
        recovered = []
        with self._lock:
            for archive_file in sorted(self.archive_dir.glob("wal_*.log.gz")):
                try:
                    with gzip.open(archive_file, "rt", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = WALEntry.from_json(line)
                                if entry_filter is None or entry_filter(entry):
                                    recovered.append(entry)
                            except Exception as e:
                                logger.warning(f"Error parsing archived entry: {e}")
                except Exception as e:
                    logger.error(f"Error reading archive {archive_file}: {e}")

        return sorted(recovered, key=lambda e: e.sequence)

    def truncate_to_sequence(self, sequence: int) -> int:
        """
        Truncate WAL entries after the given sequence (keep entries <= sequence).

        Args:
            sequence: Sequence number to truncate after

        Returns:
            Number of entries removed
        """
        removed = 0
        with self._lock:
            entries = self.replay(0)
            entries_to_keep = [e for e in entries if e.sequence <= sequence]

            if len(entries_to_keep) == len(entries):
                return 0

            current = self._get_current_file()
            try:
                with open(current, "w", encoding="utf-8") as f:
                    for entry in entries_to_keep:
                        f.write(entry.to_json() + "\n")
                        removed += 1

                logger.info(
                    f"Truncated WAL to sequence {sequence}, removed {len(entries) - removed} entries"
                )
            except Exception as e:
                logger.error(f"Failed to truncate WAL: {e}")

        return removed


class WALManager:
    """
    High-level WAL Manager for state persistence and recovery.

    Provides:
    - Automatic WAL logging for state changes
    - Checkpoint creation and restoration
    - State recovery from WAL
    - Integrity verification
    """

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        wal_dir: Optional[Path] = None,
        max_file_size: int = 10 * 1024 * 1024,
        max_entries: int = 1000,
    ):
        """
        Initialize the WAL Manager.

        Args:
            data_dir: Base data directory
            wal_dir: WAL directory (defaults to data_dir/wal)
            max_file_size: Max WAL file size before rotation
            max_entries: Max entries per WAL file
        """
        self.data_dir = Path(data_dir)
        self.wal_dir = wal_dir or (self.data_dir / "wal")
        self.checkpoint_dir = self.data_dir / "checkpoints"
        self.wal = WriteAheadLog(
            wal_dir=self.wal_dir,
            max_file_size=max_file_size,
            max_entries=max_entries,
        )
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create required directories."""
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def log_operation(
        self,
        operation: WALOperationType,
        payload: Dict[str, Any],
    ) -> int:
        """
        Log a state operation to the WAL.

        Args:
            operation: Type of operation
            payload: Operation data

        Returns:
            Sequence number of logged entry
        """
        return self.wal.append(operation, payload)

    def log_order_create(self, order_id: str, order_data: Dict[str, Any]) -> int:
        """Log order creation."""
        return self.log_operation(
            WALOperationType.CREATE_ORDER,
            {"order_id": order_id, **order_data},
        )

    def log_order_update(self, order_id: str, updates: Dict[str, Any]) -> int:
        """Log order update."""
        return self.log_operation(
            WALOperationType.UPDATE_ORDER,
            {"order_id": order_id, **updates},
        )

    def log_order_delete(self, order_id: str) -> int:
        """Log order deletion."""
        return self.log_operation(
            WALOperationType.DELETE_ORDER,
            {"order_id": order_id},
        )

    def log_fill(self, fill_data: Dict[str, Any]) -> int:
        """Log a fill event."""
        return self.log_operation(
            WALOperationType.RECORD_FILL,
            fill_data,
        )

    def log_position_create(
        self, position_id: str, position_data: Dict[str, Any]
    ) -> int:
        """Log position creation."""
        return self.log_operation(
            WALOperationType.CREATE_POSITION,
            {"position_id": position_id, **position_data},
        )

    def log_position_update(self, position_id: str, updates: Dict[str, Any]) -> int:
        """Log position update."""
        return self.log_operation(
            WALOperationType.UPDATE_POSITION,
            {"position_id": position_id, **updates},
        )

    def log_position_delete(self, position_id: str) -> int:
        """Log position deletion."""
        return self.log_operation(
            WALOperationType.DELETE_POSITION,
            {"position_id": position_id},
        )

    def log_state_save(self, state_type: str, state_data: Dict[str, Any]) -> int:
        """Log generic state save."""
        return self.log_operation(
            WALOperationType.SAVE_STATE,
            {"state_type": state_type, **state_data},
        )

    def log_checkpoint(self, checkpoint_id: str, metadata: Dict[str, Any]) -> int:
        """Log checkpoint creation."""
        return self.log_operation(
            WALOperationType.CHECKPOINT,
            {"checkpoint_id": checkpoint_id, **metadata},
        )

    def create_checkpoint(self, state_snapshot: Dict[str, Any]) -> str:
        """
        Create a checkpoint of current state.

        Args:
            state_snapshot: Current state to checkpoint

        Returns:
            Checkpoint ID
        """
        checkpoint_id = f"chk_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        checkpoint_path = self.checkpoint_dir / checkpoint_id
        checkpoint_path.mkdir(exist_ok=True)

        for filename, data in state_snapshot.items():
            file_path = checkpoint_path / f"{filename}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)

        self.log_checkpoint(
            checkpoint_id,
            {
                "files": list(state_snapshot.keys()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

        self._cleanup_old_checkpoints()

        logger.info(f"Created checkpoint: {checkpoint_id}")
        return checkpoint_id

    def _cleanup_old_checkpoints(self, keep: int = 5) -> None:
        """Remove old checkpoints, keeping only the most recent ones."""
        checkpoints = sorted(
            [d for d in self.checkpoint_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )

        for old_checkpoint in checkpoints[keep:]:
            try:
                shutil.rmtree(old_checkpoint)
                logger.debug(f"Removed old checkpoint: {old_checkpoint.name}")
            except Exception as e:
                logger.error(f"Failed to remove old checkpoint {old_checkpoint}: {e}")

    def get_latest_checkpoint(self) -> Optional[str]:
        """Get the ID of the most recent checkpoint."""
        checkpoints = sorted(
            [d for d in self.checkpoint_dir.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        return checkpoints[0].name if checkpoints else None

    def restore_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        """
        Restore state from a checkpoint.

        Args:
            checkpoint_id: Checkpoint ID to restore

        Returns:
            Dictionary of restored state data

        Raises:
            FileNotFoundError: If checkpoint doesn't exist
        """
        checkpoint_path = self.checkpoint_dir / checkpoint_id
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {checkpoint_id}")

        restored = {}
        for file_path in checkpoint_path.glob("*.json"):
            state_type = file_path.stem
            with open(file_path, "r", encoding="utf-8") as f:
                restored[state_type] = json.load(f)

        logger.info(f"Restored checkpoint: {checkpoint_id}")
        return restored

    def recover_state(
        self,
        state_loader: Callable[[], Dict[str, Any]],
        apply_entry: Callable[[WALEntry], None],
    ) -> Dict[str, Any]:
        """
        Recover state by replaying WAL entries.

        Args:
            state_loader: Function to load current state
            apply_entry: Function to apply a WAL entry to state

        Returns:
            Recovered state dictionary
        """
        state = state_loader()
        last_sequence = self.wal.get_last_sequence()

        logger.info(f"Starting WAL recovery from sequence {last_sequence}")

        entries = self.wal.replay(0)
        applied_count = 0

        for entry in entries:
            try:
                if entry.verify():
                    apply_entry(entry)
                    applied_count += 1
                else:
                    logger.warning(
                        f"Checksum mismatch for entry seq={entry.sequence}, skipping"
                    )
            except Exception as e:
                logger.error(f"Error applying WAL entry seq={entry.sequence}: {e}")

        logger.info(
            f"WAL recovery complete: {applied_count}/{len(entries)} entries applied"
        )

        return state

    def get_recovery_info(self) -> Dict[str, Any]:
        """Get information about WAL state for recovery planning."""
        last_seq = self.wal.get_last_sequence()
        current_files = list((self.wal_dir / "current").glob("wal_*.log"))
        archive_files = list((self.wal_dir / "archive").glob("wal_*.log.gz"))

        total_size = sum(f.stat().st_size for f in current_files if f.exists())

        return {
            "last_sequence": last_seq,
            "current_files": len(current_files),
            "archive_files": len(archive_files),
            "total_wal_size_bytes": total_size,
            "latest_checkpoint": self.get_latest_checkpoint(),
        }

    def force_rotation(self) -> None:
        """Force WAL file rotation."""
        self.wal._current_file = None
        self.wal._rotate_if_needed()
        logger.info("Forced WAL rotation")

    def archive_and_truncate(self, keep_sequence: int) -> int:
        """
        Archive current WAL and truncate to given sequence.

        Args:
            keep_sequence: Sequence number to keep

        Returns:
            Number of entries removed
        """
        self.wal.archive_current()
        return self.wal.truncate_to_sequence(keep_sequence)
