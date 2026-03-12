"""
State Manager - Persistent Storage for Bot State

Manages loading and saving of trading bot state to JSON files:
- Active positions
- Symbol capitals (allocated funds per symbol)
- Trade history
- Strategy configurations

Provides atomic writes, error handling, and data validation.
Includes Write-Ahead Logging (WAL) for crash recovery.
"""

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.utils.decimal_utils import DecimalEncoder
from src.persistence.wal import WALManager, WALOperationType
from src.persistence.transaction import TransactionManager, AtomicStateStore
from src.persistence.validator import (
    StateValidator,
    DataRecoveryManager,
    ValidationReport,
)

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages persistence of trading bot state to JSON files.

    Provides:
    - Load/save active positions
    - Load/save symbol capitals
    - Load/save trade history
    - Load strategy configurations
    - Atomic writes with error handling
    """

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        enable_wal: bool = True,
        enable_transactions: bool = True,
    ) -> None:
        """
        Initialize state manager.

        Args:
            data_dir: Directory for data files (default: data_futures/)
            enable_wal: Enable Write-Ahead Logging for crash recovery (default: True)
            enable_transactions: Enable atomic transactions (default: True)
        """
        self.data_dir = data_dir
        self.data_dir.mkdir(exist_ok=True)

        self.enable_wal = enable_wal
        self.enable_transactions = enable_transactions

        self.positions_file = self.data_dir / "active_positions.json"
        self.capitals_file = self.data_dir / "symbol_capitals.json"
        self.history_file = self.data_dir / "trade_history.json"
        self.grid_state_file = self.data_dir / "grid_states.json"
        self.grid_history_file = self.data_dir / "grid_trade_history.json"
        self.grid_exposure_file = self.data_dir / "grid_exposure.json"
        self.fill_log_file = self.data_dir / "fill_log.jsonl"
        self.strategy_configs_file = Path("config") / "strategy_configs.json"

        self.save_count = 0  # Counter for condensing save logs
        self._io_lock = threading.Lock()
        self._ensure_runtime_files()

        if self.enable_wal:
            self.wal_manager = WALManager(data_dir=self.data_dir)
            logger.info(f"StateManager WAL enabled, data_dir: {self.data_dir}")
        else:
            self.wal_manager = None

        if self.enable_transactions:
            self.transaction_manager = TransactionManager(
                journal_dir=self.data_dir / "wal" / "journal"
            )
            self.atomic_store = AtomicStateStore(
                self, self.wal_manager, checkpoint_on_commit=True
            )
            logger.info(f"StateManager transactions enabled")
        else:
            self.transaction_manager = None
            self.atomic_store = None

        logger.info(f"StateManager initialized with data_dir: {self.data_dir}")

    def _ensure_runtime_files(self) -> None:
        """Create core runtime files if missing to keep state shape predictable."""
        defaults = {
            self.positions_file: {},
            self.history_file: [],
            self.grid_state_file: {},
            self.grid_history_file: [],
            self.grid_exposure_file: {},
        }
        for path, default_data in defaults.items():
            if path.exists():
                continue
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(default_data, f, indent=2, cls=DecimalEncoder)
            except Exception as e:
                logger.error(f"Failed to initialize runtime file {path}: {e}")

    def _load_json(self, file_path: Path, default_factory: Callable[[], Any]) -> Any:
        """
        Safely load JSON from file with fallback to default factory.

        Args:
            file_path: Path to the JSON file
            default_factory: Function that returns default value if file doesn't exist

        Returns:
            Parsed JSON data or default value
        """
        with self._io_lock:
            if not file_path.exists():
                logger.debug(f"File {file_path} does not exist, using default")
                return default_factory()

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                if not content.strip():
                    logger.warning(f"File {file_path} is empty, using default")
                    return default_factory()

                return json.loads(content, parse_float=Decimal)

            except (IOError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load or parse {file_path}: {e}")
                # Attempt to preserve corrupt file for postmortem and recreate a fresh default file
                try:
                    corrupted_snapshot = file_path.with_name(
                        f"{file_path.name}.corrupt.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
                    )
                    file_path.rename(corrupted_snapshot)
                    logger.warning(
                        f"Renamed corrupt file {file_path} -> {corrupted_snapshot}"
                    )
                except Exception as rename_exc:
                    logger.error(
                        f"Failed to rename corrupt file {file_path}: {rename_exc}"
                    )
                # Recreate a fresh file with default content to avoid repeated failures
                try:
                    default_val = default_factory()
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(default_val, f, indent=2, cls=DecimalEncoder)
                except Exception:
                    pass
                return default_factory()

    def _save_json(self, data: Any, file_path: Path) -> None:
        """
        Safely save data to JSON file with error handling.

        Args:
            data: Data to serialize to JSON
            file_path: Target file path
        """
        try:
            with self._io_lock:
                # Ensure parent directory exists
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # Write atomically: write to a temp file in same directory, fsync, then replace
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(file_path.parent),
                    prefix=file_path.name + ".",
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, cls=DecimalEncoder)
                        f.flush()
                        os.fsync(f.fileno())
                    # Atomic replace
                    os.replace(tmp_path, file_path)
                finally:
                    # Best-effort temp cleanup regardless of final state.
                    try:
                        os.remove(tmp_path)
                    except FileNotFoundError:
                        pass
                    except Exception:
                        pass

                logger.debug(f"Saved state to {file_path} (atomic)")

        except (IOError, TypeError, OSError) as e:
            logger.error(f"Error saving state to {file_path}: {e}", exc_info=True)

    def load_positions(self) -> Dict[str, Position]:
        """
        Load active positions from persistent storage with validation.

        Validates:
        - Required fields exist
        - Value ranges are valid
        - Timestamps are parseable
        - Decimal fields are valid

        Returns:
            Dictionary mapping symbol to Position objects
        """
        positions_raw = self._load_json(self.positions_file, dict)

        positions = {}
        corrupted_count = 0

        for symbol, pos_dict in positions_raw.items():
            try:
                # Validate required fields exist
                required_fields = [
                    "symbol_id",
                    "side",
                    "entry_price",
                    "initial_amount",
                    "entry_time",
                    "initial_risk_atr",
                    "entry_atr",
                    "status",
                ]
                missing = [f for f in required_fields if f not in pos_dict]
                if missing:
                    logger.error(
                        f"Position {symbol} missing required fields: {missing}. "
                        f"Skipping corrupted position."
                    )
                    corrupted_count += 1
                    continue

                # Validate value ranges
                initial_amount = Decimal(str(pos_dict.get("initial_amount", 0)))
                current_amount = Decimal(
                    str(pos_dict.get("current_amount", initial_amount))
                )
                entry_price = Decimal(str(pos_dict.get("entry_price", 0)))

                if initial_amount <= 0:
                    logger.error(
                        f"Position {symbol} has invalid initial_amount: {initial_amount}. Skipping."
                    )
                    corrupted_count += 1
                    continue

                if current_amount < 0:
                    logger.error(
                        f"Position {symbol} has negative current_amount: {current_amount}. Skipping."
                    )
                    corrupted_count += 1
                    continue

                if current_amount > initial_amount:
                    logger.warning(
                        f"Position {symbol} current_amount ({current_amount}) > initial_amount ({initial_amount}). "
                        f"Clamping to initial_amount."
                    )
                    pos_dict["current_amount"] = str(initial_amount)

                if entry_price <= 0:
                    logger.error(
                        f"Position {symbol} has invalid entry_price: {entry_price}. Skipping."
                    )
                    corrupted_count += 1
                    continue

                # Validate side is valid
                valid_sides = ["LONG", "SHORT", "long", "short"]  # Accept both cases
                if pos_dict.get("side") not in valid_sides:
                    logger.error(
                        f"Position {symbol} has invalid side: {pos_dict.get('side')}. "
                        f"Expected 'LONG', 'SHORT', 'long', or 'short'. Skipping."
                    )
                    corrupted_count += 1
                    continue

                # Try to parse the position (this validates all fields via Position.from_dict)
                position = Position.from_dict(pos_dict)
                positions[symbol] = position

            except (ValueError, KeyError, TypeError) as e:
                logger.error(
                    f"Failed to load position for {symbol}: {e}. "
                    f"Position data may be corrupted. Skipping.",
                    exc_info=True,
                )
                corrupted_count += 1
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error loading position for {symbol}: {e}. Skipping.",
                    exc_info=True,
                )
                corrupted_count += 1
                continue

        if corrupted_count > 0:
            logger.warning(
                f"⚠️  Loaded {len(positions)} valid positions, skipped {corrupted_count} corrupted positions. "
                f"Check logs for details."
            )
        else:
            logger.info(f"Loaded {len(positions)} active positions (all valid)")

        return positions

    def load_capitals(self) -> Dict[str, Decimal]:
        """
        Load symbol capitals from persistent storage.

        Returns:
            Dictionary mapping symbol to allocated capital (Decimal)
        """
        capitals_raw = self._load_json(self.capitals_file, dict)

        capitals = {}
        for symbol, cap_value in capitals_raw.items():
            try:
                # Support both formats:
                # 1) legacy: {"BTC/USDT": "2000.0"}
                # 2) current: {"BTC/USDT": {"capital": "2000.0", ...}}
                if isinstance(cap_value, dict):
                    cap_field = cap_value.get("capital")
                    if cap_field is None:
                        raise ValueError("missing 'capital' field")
                    capitals[symbol] = Decimal(str(cap_field))
                else:
                    capitals[symbol] = Decimal(str(cap_value))
            except Exception as e:
                logger.error(f"Failed to load capital for {symbol}: {e}")
                continue

        logger.info(f"Loaded capitals for {len(capitals)} symbols")
        return capitals

    def load_trade_history(self) -> List[Dict[str, Any]]:
        """
        Load trade history from persistent storage.

        Returns:
            List of trade history entries (dicts)
        """
        history = self._load_json(self.history_file, list)
        logger.info(f"Loaded {len(history)} trade history entries")
        return history

    def load_grid_states(self) -> Dict[str, Any]:
        """
        Load active grid sessions from persistent storage.
        """
        from bot_v2.models.grid_state import GridState

        raw_states = self._load_json(self.grid_state_file, dict)
        grid_states = {}
        for symbol, data in raw_states.items():
            try:
                grid_states[symbol] = GridState.from_dict(data)
            except Exception as e:
                logger.error(f"Failed to load grid state for {symbol}: {e}")

        return grid_states

    def load_grid_trade_history(self) -> List[Dict[str, Any]]:
        """Load closed grid-trade records from persistent storage."""
        history = self._load_json(self.grid_history_file, list)
        logger.info(f"Loaded {len(history)} grid trade history entries")
        return history

    def save_grid_states(self, grid_states: Dict[str, Any]) -> None:
        """
        Save active grid sessions to persistent storage.
        """
        serialized = {s: state.to_dict() for s, state in grid_states.items()}

        if self.wal_manager:
            self.wal_manager.log_state_save(
                "grid_states",
                {"count": len(grid_states), "symbols": list(grid_states.keys())},
            )

        self._save_json(serialized, self.grid_state_file)
        logger.debug(f"Saved grid states for {len(grid_states)} symbols")

    def save_grid_trade_history(self, history: List[Dict[str, Any]]) -> None:
        """Save closed grid-trade records to persistent storage."""
        if self.wal_manager:
            self.wal_manager.log_state_save(
                "grid_trade_history", {"count": len(history)}
            )

        self._save_json(history, self.grid_history_file)
        if len(history) > 0:
            logger.info(f"Saved {len(history)} grid trade history entries")
        else:
            logger.debug("Saved grid trade history (empty)")

    def save_grid_exposure_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Save latest runtime grid exposure snapshot by symbol."""
        if self.wal_manager:
            self.wal_manager.log_state_save(
                "grid_exposure", {"symbols": list(snapshot.keys())}
            )

        self._save_json(snapshot, self.grid_exposure_file)
        logger.debug(f"Saved grid exposure snapshot for {len(snapshot)} symbols")

    def append_fill_log_event(self, event: Dict[str, Any]) -> None:
        """Append one grid fill event as JSONL for durable event auditing."""
        try:
            if self.wal_manager:
                self.wal_manager.log_fill(event)

            with self._io_lock:
                self.fill_log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self.fill_log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, cls=DecimalEncoder))
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
        except (IOError, TypeError, OSError) as e:
            logger.error(f"Error appending fill log event: {e}", exc_info=True)

    def load_states(
        self,
    ) -> Tuple[
        Dict[str, Position],
        Dict[str, Decimal],
        List[Dict[str, Any]],
        Dict[str, Any],
        List[Dict[str, Any]],
    ]:
        """
        Load all bot state from persistent storage.

        Returns:
            Tuple of (active_positions, symbol_capitals, trade_history, grid_states, grid_trade_history)
        """
        positions = self.load_positions()
        capitals = self.load_capitals()
        history = self.load_trade_history()
        grid_states = self.load_grid_states()
        grid_history = self.load_grid_trade_history()

        logger.info(
            f"Loaded all states: {len(positions)} positions, "
            f"{len(capitals)} capitals, {len(history)} history entries, "
            f"{len(grid_states)} grid states, {len(grid_history)} grid history entries"
        )

        return positions, capitals, history, grid_states, grid_history

    def get_wal_recovery_info(self) -> Dict[str, Any]:
        """
        Get WAL recovery information.

        Returns:
            Dictionary with WAL status and recovery info
        """
        if not self.wal_manager:
            return {"enabled": False}

        return {
            "enabled": True,
            **self.wal_manager.get_recovery_info(),
        }

    def create_checkpoint(self) -> Optional[str]:
        """
        Create a checkpoint of current state.

        Returns:
            Checkpoint ID if successful, None otherwise
        """
        if not self.wal_manager:
            logger.warning("WAL not enabled, cannot create checkpoint")
            return None

        try:
            positions = self.load_positions()
            capitals = self.load_capitals()
            history = self.load_trade_history()
            grid_states = self.load_grid_states()
            grid_history = self.load_grid_trade_history()

            state_snapshot = {
                "positions": {s: p.to_dict() for s, p in positions.items()},
                "capitals": {s: str(c) for s, c in capitals.items()},
                "history": history,
                "grid_states": {s: state.to_dict() for s, state in grid_states.items()},
                "grid_history": grid_history,
            }

            checkpoint_id = self.wal_manager.create_checkpoint(state_snapshot)
            logger.info(f"Created checkpoint: {checkpoint_id}")
            return checkpoint_id
        except Exception as e:
            logger.error(f"Failed to create checkpoint: {e}")
            return None

    def validate_state(self, auto_reconcile: bool = True) -> ValidationReport:
        """
        Validate all state data for integrity issues.

        Args:
            auto_reconcile: If True, attempt automatic reconciliation of issues

        Returns:
            ValidationReport with validation results
        """
        recovery_manager = DataRecoveryManager(data_dir=self.data_dir)
        return recovery_manager.validate_and_recover(auto_reconcile=auto_reconcile)

    def reconcile_fills(self) -> Dict[str, Any]:
        """
        Reconcile fills to identify orphaned entries and calculate PnL.

        Returns:
            Dictionary with reconciliation results
        """
        validator = StateValidator(data_dir=self.data_dir)
        return validator.reconcile_fills()

    def load_strategy_configs(self) -> Dict[str, StrategyConfig]:
        """
        Load strategy configurations from config file.

        Returns:
            Dictionary mapping symbol to StrategyConfig objects
        """
        main_configs_raw = self._load_json(self.strategy_configs_file, dict)

        if not main_configs_raw:
            logger.critical(
                f"Primary config file '{self.strategy_configs_file}' empty or invalid."
            )
            return {}

        final_configs = {}
        for symbol, base_params in main_configs_raw.items():
            # Skip global feature blocks that are not symbol configs (legacy check for removed second_trade_max_leverage)
            if symbol == "second_trade_max_leverage":
                continue
            # Skip disabled strategies
            if not base_params.get("enabled", False):
                logger.debug(f"Skipping disabled strategy: {symbol}")
                continue

            final_params = base_params.copy()
            clean_symbol = symbol.upper().replace("/", "")

            # Special handling for 30m timeframe volatility lookback
            timeframe = final_params.get("timeframe")
            if timeframe == "30m":
                final_params["volatility_filter_lookback"] = 30

            try:
                final_configs[clean_symbol] = StrategyConfig(clean_symbol, final_params)
            except Exception as e:
                logger.error(f"Failed to load config for {symbol}: {e}")
                continue

        logger.info(f"Loaded {len(final_configs)} strategy configurations")
        return final_configs

    def save_positions(self, positions: Dict[str, Position]) -> None:
        """
        Save active positions to persistent storage.

        Args:
            positions: Dictionary mapping symbol to Position objects
        """
        serialized = {symbol: pos.to_dict() for symbol, pos in positions.items()}

        if self.wal_manager:
            for symbol, pos in positions.items():
                self.wal_manager.log_position_update(symbol, pos.to_dict())

        self._save_json(serialized, self.positions_file)
        self.save_count += 1
        if self.save_count % 10 == 0:  # Log every 10th save to reduce verbosity
            logger.info(f"State saved: {self.save_count} updates in session")
        else:
            logger.debug(f"Saved {len(positions)} active positions")

    def save_capitals(self, capitals: Dict[str, Decimal]) -> None:
        """
        Save symbol capitals to persistent storage.

        Args:
            capitals: Dictionary mapping symbol to capital (Decimal)
        """
        serialized = {symbol: str(cap) for symbol, cap in capitals.items()}

        if self.wal_manager:
            self.wal_manager.log_state_save("capitals", serialized)

        self._save_json(serialized, self.capitals_file)
        # Use debug level to reduce verbosity (capitals change infrequently)
        logger.debug(f"Saved capitals for {len(capitals)} symbols")

    def save_history(self, history: List[Dict[str, Any]]) -> None:
        """
        Save trade history to persistent storage.

        Args:
            history: List of trade history entries (dicts)
        """
        if self.wal_manager and len(history) > 0:
            self.wal_manager.log_state_save(
                "history", {"count": len(history), "entries": history}
            )

        self._save_json(history, self.history_file)
        # Only log at INFO when there are actual entries to avoid noise
        if len(history) > 0:
            logger.info(f"Saved {len(history)} trade history entries")
        else:
            logger.debug("Saved trade history (empty)")

    def save_all_states(
        self,
        positions: Dict[str, Position],
        capitals: Dict[str, Decimal],
        history: List[Dict[str, Any]],
    ) -> None:
        """
        Save all bot state to persistent storage.

        Args:
            positions: Active positions
            capitals: Symbol capitals
            history: Trade history
        """
        self.save_positions(positions)
        self.save_capitals(capitals)
        self.save_history(history)
        logger.info("Saved all bot states")

    # ========= Second Trade Override State (Leverage Boost) =========
    def _second_trade_override_file(self) -> Path:
        return self.data_dir / "second_trade_override_state.json"

    def _load_second_trade_overrides(self) -> Dict[str, Any]:
        return self._load_json(self._second_trade_override_file(), dict)

    def _save_second_trade_overrides(self, data: Dict[str, Any]) -> None:
        self._save_json(data, self._second_trade_override_file())

    @staticmethod
    def make_day_key(ts: Optional[datetime] = None) -> str:
        if ts is None:
            ts = datetime.now(timezone.utc)
        return ts.strftime("%Y%m%d_UTC")

    def get_second_trade_override(
        self, day_key: str, scope_key: str
    ) -> Optional[Dict[str, Any]]:
        overrides = self._load_second_trade_overrides()
        return overrides.get(day_key, {}).get(scope_key)

    def set_second_trade_override(
        self, day_key: str, scope_key: str, payload: Dict[str, Any]
    ) -> None:
        overrides = self._load_second_trade_overrides()
        day_bucket = overrides.setdefault(day_key, {})
        # Only set if not already present (idempotent)
        if scope_key not in day_bucket:
            day_bucket[scope_key] = payload
            self._save_second_trade_overrides(overrides)
            logger.info(
                f"[Override] Set second trade leverage override {day_key}/{scope_key} payload={payload}"
            )
        else:
            logger.debug(
                f"[Override] Existing override present {day_key}/{scope_key}, skipping set"
            )

    def consume_second_trade_override(self, day_key: str, scope_key: str) -> bool:
        overrides = self._load_second_trade_overrides()
        day_bucket = overrides.get(day_key, {})
        entry = day_bucket.get(scope_key)
        if not entry or entry.get("consumed"):
            return False
        entry["consumed"] = True
        entry["consumed_at"] = datetime.now(timezone.utc).isoformat()
        self._save_second_trade_overrides(overrides)
        logger.info(f"[Override] Consumed second trade override {day_key}/{scope_key}")
        return True

    def expire_second_trade_override(self, day_key: str, scope_key: str) -> bool:
        overrides = self._load_second_trade_overrides()
        day_bucket = overrides.get(day_key, {})
        entry = day_bucket.get(scope_key)
        if not entry or entry.get("consumed"):
            return False
        entry["expired"] = True
        entry["expired_at"] = datetime.now(timezone.utc).isoformat()
        self._save_second_trade_overrides(overrides)
        logger.info(
            f"[Override] Expired unused second trade override {day_key}/{scope_key}"
        )
        return True

    def count_daily_overrides(self, day_key: str, scope_key_prefix: str) -> int:
        """
        Count the number of daily overrides for a scope (symbol) today.
        Non-expired overrides only (expired ones are ignored).
        Backward compatible: handles both old single-key and new sequenced formats.

        Args:
            day_key: Date key (e.g., '20260307_UTC')
            scope_key_prefix: Base symbol (e.g., 'XRPUSDT') or "GLOBAL" without sequence suffix

        Returns:
            Count of non-expired overrides matching the prefix
        """
        overrides = self._load_second_trade_overrides()
        day_bucket = overrides.get(day_key, {})

        count = 0

        # Check for old-format single key (backward compat)
        if scope_key_prefix in day_bucket and not day_bucket[scope_key_prefix].get(
            "expired", False
        ):
            count += 1

        # Check for new-format sequenced keys
        for key, entry in day_bucket.items():
            # Check if key matches pattern: XRPUSDT_1, XRPUSDT_2, etc.
            if key.startswith(f"{scope_key_prefix}_") and not entry.get(
                "expired", False
            ):
                count += 1

        return count

    def get_first_unconsumed_override(
        self, day_key: str, scope_key_prefix: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the first unconsumed override for a scope (FIFO).
        Backward compatible: checks both old single-key format and new sequenced format.

        Args:
            day_key: Date key (e.g., '20260307_UTC')
            scope_key_prefix: Base symbol or "GLOBAL" without sequence suffix

        Returns:
            First unconsumed override dict, or None
        """
        overrides = self._load_second_trade_overrides()
        day_bucket = overrides.get(day_key, {})

        # First, try backward compatibility: check for exact key match (old format)
        if scope_key_prefix in day_bucket:
            entry = day_bucket[scope_key_prefix]
            if not entry.get("consumed", False) and not entry.get("expired", False):
                entry["_scope_key"] = (
                    scope_key_prefix  # Store key for consumption tracking
                )
                return entry

        # New format: look for sequenced keys (XRPUSDT_1, XRPUSDT_2, etc.)
        # Sort by numeric suffix to preserve FIFO when sequence reaches 10+.
        def _sequence_num(key: str) -> int:
            try:
                return int(key.rsplit("_", 1)[1])
            except (ValueError, IndexError):
                # Keep malformed keys at the end without breaking processing.
                return 10**9

        matching_keys = sorted(
            [
                key
                for key in day_bucket.keys()
                if key.startswith(f"{scope_key_prefix}_")
            ],
            key=_sequence_num,
        )

        for key in matching_keys:
            entry = day_bucket[key]
            if not entry.get("consumed", False) and not entry.get("expired", False):
                # Return with the key for consumption tracking
                entry["_scope_key"] = key  # Store key for consume operation
                return entry

        return None
