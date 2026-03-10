"""
tests/test_state_store.py
--------------------------
Unit tests for src/persistence/state_store.py.

Tests atomic write/read round-trips, corruption handling, and trade logging.
No exchange calls — pure filesystem operations on tmp paths.
"""

import json
import time
from pathlib import Path

import pytest

from src.persistence.state_store import StateStore


@pytest.fixture
def tmp_state_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "grid_state.json"


@pytest.fixture
def store(tmp_state_file: Path) -> StateStore:
    return StateStore(state_file=tmp_state_file)


class TestSaveLoad:
    def test_save_creates_file(self, store: StateStore, tmp_state_file: Path) -> None:
        store.save({"centre_price": 30_000.0})
        assert tmp_state_file.exists()

    def test_load_returns_none_when_no_file(
        self, store: StateStore, tmp_state_file: Path
    ) -> None:
        result = store.load()
        assert result is None

    def test_round_trip_preserves_data(self, store: StateStore) -> None:
        original = {"centre_price": 29_500.0, "initial_equity": 1_000.0}
        store.save(original)
        loaded = store.load()
        assert loaded is not None
        assert loaded["centre_price"] == 29_500.0
        assert loaded["initial_equity"] == 1_000.0

    def test_save_adds_timestamp(self, store: StateStore) -> None:
        store.save({"centre_price": 30_000.0})
        loaded = store.load()
        assert "_saved_at" in loaded

    def test_overwrite_updates_data(self, store: StateStore) -> None:
        store.save({"centre_price": 30_000.0})
        store.save({"centre_price": 31_000.0})  # overwrite
        loaded = store.load()
        assert loaded["centre_price"] == 31_000.0


class TestAtomicWrite:
    def test_tmp_file_removed_after_save(
        self, store: StateStore, tmp_state_file: Path
    ) -> None:
        """Temporary .json.tmp file must not exist after a successful save."""
        store.save({"centre_price": 30_000.0})
        tmp_file = tmp_state_file.with_suffix(".json.tmp")
        assert not tmp_file.exists()


class TestClear:
    def test_clear_removes_file(self, store: StateStore, tmp_state_file: Path) -> None:
        store.save({"centre_price": 30_000.0})
        store.clear()
        assert not tmp_state_file.exists()

    def test_clear_no_file_no_error(self, store: StateStore) -> None:
        """clear() on a non-existent file should not raise."""
        store.clear()  # must not raise


class TestCorruptedFile:
    def test_corrupted_file_returns_none(
        self, store: StateStore, tmp_state_file: Path
    ) -> None:
        """A corrupted JSON file should be backed up and None returned."""
        tmp_state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_state_file.write_text("{ invalid json !!!")
        result = store.load()
        assert result is None
        backup = tmp_state_file.with_suffix(".json.corrupted")
        assert backup.exists()


class TestTradeLog:
    def test_log_trade_appends(self, store: StateStore, tmp_state_file: Path) -> None:
        trade1 = {"side": "buy", "price": 30_000.0}
        trade2 = {"side": "sell", "price": 30_300.0}
        store.log_trade(trade1)
        store.log_trade(trade2)
        log_path = tmp_state_file.parent / "trade_log.jsonl"
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["side"] == "buy"
        assert json.loads(lines[1])["side"] == "sell"
