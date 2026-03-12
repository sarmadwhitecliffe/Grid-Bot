"""
Capital Manager - Simple Single Capital Per Symbol

Manages capital allocation per symbol.
Thread-safe operations for concurrent access.

Design Philosophy:
- ONE capital value per symbol
- Mode (sim/live) read from strategy_configs.json (single source of truth)
- Tier tracking for adaptive risk management
- Simple, testable, maintainable

Example:
    >>> manager = CapitalManager(data_dir=Path("data_futures"))
    >>> manager.get_capital("BTCUSDT")
    Decimal('1000.00')
    >>> manager.update_capital("BTCUSDT", Decimal("50.00"))  # +$50 profit
    >>> manager.get_capital("BTCUSDT")
    Decimal('1050.00')
"""

import asyncio
import json
import logging
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CapitalManager:
    """
    Thread-safe capital management for trading symbols.

    Storage Format (Consolidated - Single Source of Truth):
    {
        "BTCUSDT": {
            "capital": "1000.00",
            "tier": "PROBATION",
            "tier_entry_time": "2025-11-14T12:00:00+00:00",
            "trades_in_tier": 0,
            "consecutive_losses_in_tier": 0,
            "last_transition_time": "2025-11-14T12:00:00+00:00",
            "previous_tier": null,
            "last_total_trades": 0,
            "last_notified_tier": "PROBATION"
        }
    }

    Note: Mode (simulation/live) is read from strategy_configs.json,
    not stored here. Single source of truth.
    """

    def __init__(self, data_dir: Path, strategy_configs: Optional[Dict] = None) -> None:
        """
        Initialize capital manager.

        Args:
            data_dir: Directory for capital persistence file
            strategy_configs: Strategy configs to read initial_capital from
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.capitals_file = self.data_dir / "symbol_capitals.json"
        self.strategy_configs = strategy_configs or {}

        # Thread safety
        self._lock = asyncio.Lock()

        # In-memory state
        self._capitals: Dict[str, Dict[str, str]] = {}

        # Optional callback for critical alerts (e.g., capital depletion)
        self._on_critical_alert = None

        # Load existing capitals
        self._load()

    def _load(self) -> None:
        """Load capitals from disk or initialize from config."""
        if not self.capitals_file.exists():
            logger.info("No existing capitals file, initializing from strategy_configs")
            for symbol, config in self.strategy_configs.items():
                initial = str(getattr(config, "initial_capital", "1000.00"))
                self._capitals[symbol] = self._create_default_entry(initial, "PROBATION")
            # Materialize file immediately so runtime state is visible from startup.
            self._save()
            return

        try:
            with open(self.capitals_file, "r") as f:
                data = json.load(f)

            # Handle legacy format (plain string capital)
            for symbol, cap_data in data.items():
                if isinstance(cap_data, (str, int, float)):
                    # Legacy format: {"BTCUSDT": "1000.00"}
                    self._capitals[symbol] = self._create_default_entry(
                        str(cap_data), "PROBATION"
                    )
                    logger.info(f"Migrated {symbol} from legacy format")
                else:
                    # New format: full dict with tier metadata
                    from datetime import datetime, timezone

                    cleaned = {
                        "capital": cap_data.get("capital", "1000.00"),
                        "tier": cap_data.get("tier", "PROBATION"),
                        "tier_entry_time": cap_data.get(
                            "tier_entry_time", datetime.now(timezone.utc).isoformat()
                        ),
                        "trades_in_tier": cap_data.get("trades_in_tier", 0),
                        "consecutive_losses_in_tier": cap_data.get(
                            "consecutive_losses_in_tier", 0
                        ),
                        "last_transition_time": cap_data.get(
                            "last_transition_time",
                            datetime.now(timezone.utc).isoformat(),
                        ),
                        "previous_tier": cap_data.get("previous_tier"),
                        "last_total_trades": cap_data.get("last_total_trades", 0),
                        "last_notified_tier": cap_data.get(
                            "last_notified_tier", "PROBATION"
                        ),
                    }
                    self._capitals[symbol] = cleaned

            logger.info(f"Loaded capitals for {len(self._capitals)} symbols")

        except Exception as e:
            logger.error(f"Error loading capitals: {e}", exc_info=True)
            self._capitals = {}

    def _save(self) -> None:
        """Save capitals to disk atomically (write-to-temp, fsync, replace)."""
        try:
            self.capitals_file.parent.mkdir(parents=True, exist_ok=True)

            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.capitals_file.parent),
                prefix=self.capitals_file.name + ".",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._capitals, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.capitals_file)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

            logger.debug(f"Saved capitals to {self.capitals_file} (atomic)")
        except Exception as e:
            logger.error(f"Error saving capitals: {e}", exc_info=True)

    def _create_default_entry(
        self, capital: str = "1000.00", tier: str = "PROBATION"
    ) -> Dict[str, any]:
        """Create default capital entry with all tier metadata."""
        from datetime import datetime, timezone

        return {
            "capital": capital,
            "tier": tier,
            "tier_entry_time": datetime.now(timezone.utc).isoformat(),
            "trades_in_tier": 0,
            "consecutive_losses_in_tier": 0,
            "last_transition_time": datetime.now(timezone.utc).isoformat(),
            "previous_tier": None,
            "last_total_trades": 0,
            "last_notified_tier": "PROBATION",  # Always start as PROBATION, only change when explicitly notified
        }

    async def get_capital(self, symbol: str) -> Decimal:
        """
        Get current capital for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current capital (default $1000 for new symbols)
        """
        async with self._lock:
            if symbol not in self._capitals:
                # Read initial_capital from strategy_configs, default to 1000
                initial_cap = "1000.00"
                if symbol in self.strategy_configs:
                    # StrategyConfig is a dataclass, use attribute access
                    config_cap = self.strategy_configs[symbol].initial_capital
                    initial_cap = str(config_cap)

                # Initialize new symbol with config capital
                self._capitals[symbol] = {
                    "capital": initial_cap,
                    "tier": "PROBATION",
                    "last_notified_tier": "PROBATION",
                }
                self._save()
                logger.info(
                    f"Initialized {symbol} with capital ${initial_cap} (from config)"
                )

            return Decimal(self._capitals[symbol]["capital"])

    async def update_capital(self, symbol: str, pnl: Decimal) -> Decimal:
        """
        Update capital after trade close (thread-safe).

        Args:
            symbol: Trading symbol
            pnl: Profit/loss to apply

        Returns:
            New capital after update
        """
        async with self._lock:
            # Get or initialize capital (inline to avoid recursion)
            if symbol not in self._capitals:
                self._capitals[symbol] = {
                    "capital": "1000.00",
                    "tier": "PROBATION",
                    "last_notified_tier": "PROBATION",
                }
                logger.info(f"Initialized {symbol} with default capital $1000")

            current = Decimal(self._capitals[symbol]["capital"])
            new_capital = current + pnl

            # Prevent negative capital
            if new_capital < Decimal("0"):
                logger.warning(
                    f"{symbol} capital would go negative "
                    f"(${current} + ${pnl}), clamping to $0"
                )
                new_capital = Decimal("0")

            self._capitals[symbol]["capital"] = str(new_capital)
            self._save()

            # Alert on capital depletion
            if new_capital == Decimal("0"):
                logger.critical(
                    f"CAPITAL DEPLETED: {symbol} capital is now $0. "
                    f"Trading halted for this symbol."
                )
                if self._on_critical_alert:
                    try:
                        asyncio.ensure_future(
                            self._on_critical_alert(
                                symbol,
                                f"CAPITAL DEPLETED: {symbol} capital hit $0 "
                                f"(was ${current}, PnL ${pnl:+.2f}). "
                                f"Trading halted for this symbol."
                            )
                        )
                    except Exception as e:
                        logger.error(f"Failed to send capital depletion alert: {e}")

            logger.info(
                f"{symbol} capital: ${current:.2f} → ${new_capital:.2f} "
                f"(PnL: ${pnl:+.2f})"
            )

            return new_capital

    async def set_capital(self, symbol: str, amount: Decimal) -> None:
        """
        Manually set capital (e.g., when resetting for testing).

        Args:
            symbol: Trading symbol
            amount: New capital amount
        """
        async with self._lock:
            if symbol not in self._capitals:
                self._capitals[symbol] = {
                    "capital": str(amount),
                    "tier": "PROBATION",
                    "last_notified_tier": "PROBATION",
                }
            else:
                old_capital = self._capitals[symbol]["capital"]
                self._capitals[symbol]["capital"] = str(amount)
                logger.info(
                    f"{symbol} capital manually set: ${old_capital} → ${amount}"
                )

            self._save()

    async def get_tier(self, symbol: str) -> str:
        """Get risk tier for a symbol."""
        async with self._lock:
            if symbol not in self._capitals:
                return "PROBATION"
            return self._capitals[symbol].get("tier", "PROBATION")

    async def set_tier(self, symbol: str, tier: str) -> None:
        """
        Update risk tier (called by adaptive risk manager).
        Note: For full tier updates with metadata, use update_tier_history() instead.

        Args:
            symbol: Trading symbol
            tier: Risk tier name
        """
        # Validate tier name - prevent invalid tiers like "KILL_SWITCH"
        valid_tiers = {
            "PROBATION",
            "CONSERVATIVE",
            "STANDARD",
            "AGGRESSIVE",
            "CHAMPION",
        }
        if tier not in valid_tiers:
            logger.error(
                f"Invalid tier '{tier}' for {symbol} - must be one of {valid_tiers}"
            )
            logger.error("This likely indicates a bug in tier classification logic")
            # Default to PROBATION for invalid tiers
            tier = "PROBATION"

        async with self._lock:
            if symbol not in self._capitals:
                self._capitals[symbol] = self._create_default_entry("1000.00", tier)
                logger.info(f"Initialized {symbol} with tier {tier}")
            else:
                old_tier = self._capitals[symbol].get("tier", "PROBATION")
                self._capitals[symbol]["tier"] = tier

                if old_tier != tier:
                    logger.info(f"{symbol} tier changed: {old_tier} → {tier}")

            self._save()

    async def get_last_notified_tier(self, symbol: str) -> str:
        """Get last tier that user was notified about."""
        async with self._lock:
            if symbol not in self._capitals:
                return "PROBATION"
            return self._capitals[symbol].get("last_notified_tier", "PROBATION")

    async def set_last_notified_tier(self, symbol: str, tier: str) -> None:
        """Update last notified tier (prevent spam)."""
        async with self._lock:
            if symbol not in self._capitals:
                self._capitals[symbol] = self._create_default_entry(
                    "1000.00", "PROBATION"
                )
                self._capitals[symbol]["last_notified_tier"] = tier
            else:
                self._capitals[symbol]["last_notified_tier"] = tier

            self._save()

    async def get_tier_history(self, symbol: str) -> Dict[str, any]:
        """Get complete tier metadata for a symbol (used by AdaptiveRiskManager for hysteresis)."""
        async with self._lock:
            if symbol not in self._capitals:
                return {
                    "current_tier": "PROBATION",
                    "tier_entry_time": None,
                    "trades_in_tier": 0,
                    "consecutive_losses_in_tier": 0,
                    "last_transition_time": None,
                    "previous_tier": None,
                    "last_total_trades": 0,
                }

            entry = self._capitals[symbol]
            return {
                "current_tier": entry.get("tier", "PROBATION"),
                "tier_entry_time": entry.get("tier_entry_time"),
                "trades_in_tier": entry.get("trades_in_tier", 0),
                "consecutive_losses_in_tier": entry.get(
                    "consecutive_losses_in_tier", 0
                ),
                "last_transition_time": entry.get("last_transition_time"),
                "previous_tier": entry.get("previous_tier"),
                "last_total_trades": entry.get("last_total_trades", 0),
            }

    async def update_tier_history(self, symbol: str, tier_data: Dict[str, any]) -> None:
        """Update tier metadata (called by AdaptiveRiskManager)."""
        # Validate tier name
        current_tier = tier_data.get("current_tier", "PROBATION")
        valid_tiers = {
            "PROBATION",
            "CONSERVATIVE",
            "STANDARD",
            "AGGRESSIVE",
            "CHAMPION",
        }
        if current_tier not in valid_tiers:
            logger.error(
                f"Invalid tier '{current_tier}' in tier_data for {symbol} - must be one of {valid_tiers}"
            )
            logger.error(
                "This likely indicates a bug in tier classification logic - defaulting to PROBATION"
            )
            tier_data["current_tier"] = "PROBATION"

        async with self._lock:
            if symbol not in self._capitals:
                self._capitals[symbol] = self._create_default_entry(
                    "1000.00", tier_data.get("current_tier", "PROBATION")
                )

            # Update tier metadata fields
            entry = self._capitals[symbol]
            entry["tier"] = tier_data.get(
                "current_tier", entry.get("tier", "PROBATION")
            )
            entry["tier_entry_time"] = tier_data.get(
                "tier_entry_time", entry.get("tier_entry_time")
            )
            entry["trades_in_tier"] = tier_data.get(
                "trades_in_tier", entry.get("trades_in_tier", 0)
            )
            entry["consecutive_losses_in_tier"] = tier_data.get(
                "consecutive_losses_in_tier", entry.get("consecutive_losses_in_tier", 0)
            )
            entry["last_transition_time"] = tier_data.get(
                "last_transition_time", entry.get("last_transition_time")
            )
            entry["previous_tier"] = tier_data.get(
                "previous_tier", entry.get("previous_tier")
            )
            entry["last_total_trades"] = tier_data.get(
                "last_total_trades", entry.get("last_total_trades", 0)
            )

            self._save()

    def get_all_capitals(self) -> Dict[str, Decimal]:
        """
        Get all symbol capitals (synchronous).
        Includes both persisted capitals and initial capitals for newly configured symbols.

        Returns:
            Dict of symbol → capital
        """
        result = {}
        
        # 1. Start with configured symbols (initial capital)
        for symbol, config in self.strategy_configs.items():
            if hasattr(config, 'initial_capital'):
                result[symbol] = Decimal(str(config.initial_capital))
            else:
                result[symbol] = Decimal("1000.00")

        # 2. Overwrite with actual persisted/running capitals
        for symbol, data in self._capitals.items():
            result[symbol] = Decimal(data["capital"])
            
        return result

    def set_critical_alert_callback(self, callback) -> None:
        """Set callback for critical capital alerts."""
        self._on_critical_alert = callback

    async def is_halted(self, symbol: str) -> bool:
        """Check if trading is halted due to zero capital."""
        capital = await self.get_capital(symbol)
        return capital <= Decimal("0")

    # ========= Second Trade Leverage Override Application =========
    def apply_second_trade_override(
        self,
        symbol: str,
        leverage: Decimal,
        tier_max_leverage: Decimal,
        state_manager,
        feature_cfg: Dict[str, Any],
    ) -> Decimal:
        """
        Apply second trade leverage override if qualified and pending.

        Args:
            symbol: Symbol ID (e.g., 'XRPUSDT')
            leverage: Current calculated leverage (Kelly/tier logic)
            tier_max_leverage: Tier maximum leverage
            state_manager: StateManager instance for override persistence
            feature_cfg: Config dict loaded from second_trade_override.feature.json

        Returns:
            Possibly modified leverage (Decimal)
        """
        try:
            logger.debug(
                f"[{symbol}] override_eval_start leverage={leverage} tier_max={tier_max_leverage} feature_cfg_present={bool(feature_cfg)}"
            )
            if not feature_cfg or not feature_cfg.get("enabled", False):
                logger.info(f"[{symbol}] leverage_override_ignored_flag_disabled")
                return leverage

            scope = feature_cfg.get("scope", "global")
            scope_key_prefix = "GLOBAL" if scope == "global" else symbol
            day_key = state_manager.make_day_key()
            override = state_manager.get_first_unconsumed_override(day_key, scope_key_prefix)
            if not override:
                logger.debug(
                    f"[{symbol}] leverage_override_no_pending day_key={day_key} scope={scope_key_prefix}"
                )
                return leverage
            logger.debug(f"[{symbol}] leverage_override_pending state={override}")
            if not override:
                return leverage
            if override.get("consumed") or override.get("expired"):
                logger.info(
                    f"[{symbol}] leverage_override_inactive consumed={override.get('consumed')} expired={override.get('expired')}"
                )
                return leverage

            # Get the actual scope_key used (includes sequence number)
            scope_key = override.get("_scope_key", scope_key_prefix)

            # Expiry handling
            max_delay = feature_cfg.get("max_delay_minutes") or 0
            if max_delay > 0:
                from datetime import datetime, timezone

                qualified_at_str = override.get("qualified_at")
                if qualified_at_str:
                    try:
                        qualified_at = datetime.fromisoformat(
                            qualified_at_str.replace("Z", "+00:00")
                        )
                        diff_min = (
                            datetime.now(timezone.utc) - qualified_at
                        ).total_seconds() / 60
                        if diff_min > max_delay:
                            # Expire unused
                            state_manager.expire_second_trade_override(
                                day_key, scope_key
                            )
                            logger.info(
                                f"[{symbol}] leverage_override_expired_unused day_key={day_key} scope={scope_key} delay_minutes={diff_min:.2f} max_delay={max_delay}"
                            )
                            return leverage
                    except Exception as e:
                        logger.warning(
                            f"[{symbol}] Failed parsing qualified_at for override expiry: {e}"
                        )

            # Attempt consumption
            logger.info(
                f"[{symbol}] leverage_override_consumption_attempt day_key={day_key} scope={scope_key}"
            )
            consumed = state_manager.consume_second_trade_override(day_key, scope_key)
            if not consumed:
                logger.info(
                    f"[{symbol}] leverage_override_consumption_failed day_key={day_key} scope={scope_key}"
                )
                return leverage

            # Apply target leverage if configured
            target_leverage = feature_cfg.get("target_leverage")
            if target_leverage is not None:
                new_leverage = Decimal(str(target_leverage))
                logger.info(
                    f"[{symbol}] leverage_override_applied {leverage}x → {new_leverage}x (target={target_leverage}x)"
                )
                return new_leverage

            # Fallback to old behavior: clamp to tier max
            new_leverage = (
                tier_max_leverage if tier_max_leverage > leverage else leverage
            )
            if new_leverage != leverage:
                logger.info(
                    f"[{symbol}] leverage_override_applied {leverage}x → {new_leverage}x (tier_max={tier_max_leverage}x)"
                )
            else:
                logger.info(
                    f"[{symbol}] leverage_override_applied but leverage already at tier max {leverage}x"
                )
            return new_leverage
        except Exception as e:
            logger.error(f"[{symbol}] Error applying second trade override: {e}")
            return leverage

    async def get_mode(self, symbol: str) -> str:
        """
        DEPRECATED: Mode should be read from strategy_configs.json, not stored here.
        This method exists only for backward compatibility with tests.

        Args:
            symbol: Trading symbol

        Returns:
            Always returns 'simulation' (mode is no longer stored in capital manager)
        """
        logger.warning(
            f"get_mode() is deprecated for {symbol} - mode should be read from strategy_configs.json"
        )
        return "simulation"

    async def set_mode(self, symbol: str, mode: str) -> None:
        """
        DEPRECATED: Mode should be read from strategy_configs.json, not stored here.
        This method exists only for backward compatibility with tests.
        Does nothing except log a warning.

        Args:
            symbol: Trading symbol
            mode: Mode to set (ignored)
        """
        logger.warning(
            f"set_mode() is deprecated for {symbol}. Mode should be configured in strategy_configs.json, "
            f"not stored in capital manager. This call has no effect."
        )

    def get_all_modes(self) -> Dict[str, str]:
        """
        DEPRECATED: Mode should be read from strategy_configs.json, not stored here.
        This method exists only for backward compatibility with tests.

        Returns:
            Dict of symbol → mode (always returns 'simulation' as mode is no longer stored)
        """
        logger.warning(
            "get_all_modes() is deprecated - mode should be read from strategy_configs.json"
        )
        return {
            symbol: "simulation"  # Default since mode is no longer stored
            for symbol in self._capitals.keys()
        }
