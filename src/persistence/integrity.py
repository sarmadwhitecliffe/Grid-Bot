"""
src/persistence/integrity.py
---------------------------
Checksum and integrity verification system for Grid Bot state.

This module provides:
- IntegrityManager: SHA-256 checksums for all data files
- Auto-repair from checkpoints on checksum failure
- Integration with StateManager for verification on save
"""

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.persistence.wal import WALManager

logger = logging.getLogger(__name__)


@dataclass
class IntegrityStatus:
    """Status of integrity verification."""

    file_path: str
    expected_checksum: Optional[str]
    actual_checksum: Optional[str]
    is_valid: bool
    error: Optional[str] = None


class IntegrityManager:
    """
    Manages data integrity through checksums and verification.

    Features:
    - SHA-256 checksums for all data files
    - Checksum storage in state_checksums.json
    - Auto-repair from checkpoints on failure
    - Verification on save (optional)
    """

    CHECKSUM_FILE = "state_checksums.json"

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        verify_on_save: bool = True,
    ):
        self.data_dir = Path(data_dir)
        self.checksum_file = self.data_dir / self.CHECKSUM_FILE
        self.verify_on_save = verify_on_save

    def calculate_checksum(self, file_path: Path) -> str:
        """
        Calculate SHA-256 checksum for a file.

        Args:
            file_path: Path to the file

        Returns:
            Hex string of SHA-256 checksum
        """
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        return sha256.hexdigest()

    def verify_checksum(self, file_path: Path, expected: str) -> bool:
        """
        Verify file checksum against expected value.

        Args:
            file_path: Path to the file
            expected: Expected checksum hex string

        Returns:
            True if checksums match
        """
        if not file_path.exists():
            return False

        actual = self.calculate_checksum(file_path)
        return actual == expected

    def save_checksums(self, state_dir: Optional[Path] = None) -> Dict[str, str]:
        """
        Calculate and save checksums for all state files.

        Args:
            state_dir: Directory containing state files (defaults to data_dir)

        Returns:
            Dictionary mapping file paths to checksums
        """
        state_dir = state_dir or self.data_dir

        checksums = {}
        json_files = list(state_dir.glob("**/*.json"))
        jsonl_files = list(state_dir.glob("**/*.jsonl"))

        all_files = json_files + jsonl_files

        for file_path in all_files:
            if file_path.name.startswith("."):
                continue
            if "wal" in file_path.parts or "checkpoints" in file_path.parts:
                continue

            try:
                checksum = self.calculate_checksum(file_path)
                rel_path = str(file_path.relative_to(self.data_dir))
                checksums[rel_path] = checksum
            except Exception as e:
                logger.warning(f"Failed to calculate checksum for {file_path}: {e}")

        checksums_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "files": checksums,
        }

        with open(self.checksum_file, "w") as f:
            json.dump(checksums_data, f, indent=2)

        logger.info(f"Saved checksums for {len(checksums)} files")
        return checksums

    def load_checksums(self) -> Dict[str, str]:
        """
        Load stored checksums from file.

        Returns:
            Dictionary mapping file paths to checksums
        """
        if not self.checksum_file.exists():
            return {}

        try:
            with open(self.checksum_file) as f:
                data = json.load(f)
            return data.get("files", {})
        except Exception as e:
            logger.error(f"Failed to load checksums: {e}")
            return {}

    def verify_all(self, state_dir: Optional[Path] = None) -> Dict[str, Any]:
        """
        Verify all state files against stored checksums.

        Args:
            state_dir: Directory containing state files

        Returns:
            Dictionary with verification results
        """
        state_dir = state_dir or self.data_dir
        stored_checksums = self.load_checksums()

        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "valid": True,
            "files_checked": 0,
            "files_valid": 0,
            "files_failed": 0,
            "details": [],
            "missing_files": [],
            "new_files": [],
        }

        for rel_path, expected in stored_checksums.items():
            file_path = self.data_dir / rel_path

            if not file_path.exists():
                results["missing_files"].append(rel_path)
                results["valid"] = False
                results["details"].append(
                    IntegrityStatus(
                        file_path=rel_path,
                        expected_checksum=expected,
                        actual_checksum=None,
                        is_valid=False,
                        error="File missing",
                    )
                )
                continue

            results["files_checked"] += 1

            actual = self.calculate_checksum(file_path)
            is_valid = actual == expected

            results["details"].append(
                IntegrityStatus(
                    file_path=rel_path,
                    expected_checksum=expected,
                    actual_checksum=actual,
                    is_valid=is_valid,
                )
            )

            if is_valid:
                results["files_valid"] += 1
            else:
                results["files_failed"] += 1
                results["valid"] = False
                logger.warning(f"Checksum mismatch for {rel_path}")

        new_files = []
        current_files = set()
        for rel_path in stored_checksums.keys():
            current_files.add(rel_path)

        for json_file in state_dir.glob("**/*.json"):
            if json_file.name.startswith("."):
                continue
            rel_path = str(json_file.relative_to(self.data_dir))
            if rel_path not in current_files:
                new_files.append(rel_path)

        results["new_files"] = new_files

        if not results["valid"]:
            logger.error(
                f"Integrity verification failed: {results['files_failed']} files failed, "
                f"{len(results['missing_files'])} missing"
            )

        return results

    def get_wal_manager(self) -> Optional[WALManager]:
        """Get WAL manager for checkpoint access."""
        try:
            return WALManager(data_dir=self.data_dir)
        except Exception as e:
            logger.error(f"Failed to initialize WAL manager: {e}")
            return None

    def auto_repair_on_failure(
        self, state_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Attempt to repair integrity failures from latest checkpoint.

        Args:
            state_dir: Directory containing state files

        Returns:
            Dictionary with repair results
        """
        state_dir = state_dir or self.data_dir

        results = {
            "attempted": False,
            "success": False,
            "files_repaired": [],
            "errors": [],
        }

        verification = self.verify_all(state_dir)

        if verification["valid"]:
            logger.info("No integrity failures to repair")
            return results

        results["attempted"] = True

        wal_manager = self.get_wal_manager()
        if not wal_manager:
            results["errors"].append("WAL manager not available")
            return results

        checkpoint_id = wal_manager.get_latest_checkpoint()
        if not checkpoint_id:
            results["errors"].append("No checkpoints available")
            return results

        try:
            checkpoint_data = wal_manager.restore_checkpoint(checkpoint_id)

            for file_name, data in checkpoint_data.items():
                file_path = state_dir / f"{file_name}.json"
                try:
                    with open(file_path, "w") as f:
                        json.dump(data, f, indent=2, default=str)
                    results["files_repaired"].append(file_name)
                    logger.info(f"Repaired {file_name} from checkpoint {checkpoint_id}")
                except Exception as e:
                    results["errors"].append(f"Failed to repair {file_name}: {e}")

            self.save_checksums(state_dir)

            recheck = self.verify_all(state_dir)
            results["success"] = recheck["valid"]

            if results["success"]:
                logger.info("Auto-repair successful")
            else:
                logger.error("Auto-repair incomplete")

        except Exception as e:
            results["errors"].append(f"Checkpoint restore failed: {e}")
            logger.error(f"Auto-repair failed: {e}")

        return results

    def verify_and_repair(self, auto_repair: bool = True) -> Dict[str, Any]:
        """
        Verify integrity and optionally attempt repair.

        Args:
            auto_repair: If True, attempt auto-repair on failure

        Returns:
            Dictionary with verification and repair results
        """
        verification = self.verify_all()

        if not verification["valid"] and auto_repair:
            repair = self.auto_repair_on_failure()
            verification["repair_attempted"] = True
            verification["repair_result"] = repair
            verification["repaired"] = repair.get("success", False)

        return verification


class VerifiedStateManager:
    """
    Wrapper for StateManager that adds integrity verification.
    """

    def __init__(
        self, state_manager, integrity_manager: Optional[IntegrityManager] = None
    ):
        self.state_manager = state_manager
        self.integrity_manager = integrity_manager or IntegrityManager(
            data_dir=state_manager.data_dir,
            verify_on_save=True,
        )

    def save_with_verification(self, **kwargs) -> bool:
        """
        Save state and verify integrity.

        Returns:
            True if save and verification successful
        """
        save_method = getattr(self.state_manager, "save_all_states", None)
        if save_method:
            save_method(**kwargs)

        return self.integrity_manager.verify_and_repair(auto_repair=True).get(
            "valid", False
        )

    def __getattr__(self, name):
        return getattr(self.state_manager, name)
