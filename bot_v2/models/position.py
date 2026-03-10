"""
Position data model - Pure data container.

This module contains the Position class which holds all position attributes
as an immutable data structure. Business logic is separated into other modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from bot_v2.models.enums import PositionSide, PositionStatus


@dataclass(frozen=True)
class DecayCache:
    """
    Cache for R-decay calculations to prevent excessive recalculations.

    Implements hysteresis based on R-multiples to reduce CPU overhead during decay periods.
    Hysteresis bands prevent excessive recalculations while ensuring timely updates when
    R-multiples move significantly.
    """

    last_calculation_r: Decimal
    decay_percentage: Decimal
    multiplier: Decimal
    scheme: str
    hysteresis_upper_r: Decimal  # Upper R threshold for hysteresis
    hysteresis_lower_r: Decimal  # Lower R threshold for hysteresis
    is_active: bool

    def should_recalculate(self, current_r: Decimal) -> bool:
        """
        Check if recalculation is needed based on R-multiple hysteresis bands.

        Returns True if:
        - Decay inactive: current_r moved below lower band (toward activation)
        - Decay active: current_r moved above upper band (toward deactivation)
        """
        if not self.is_active:
            # If inactive, recalculate if we've moved below hysteresis threshold (toward activation)
            return current_r < self.hysteresis_lower_r
        else:
            # If active, recalculate if we've moved above hysteresis threshold (toward deactivation)
            return current_r > self.hysteresis_upper_r


@dataclass
class Position:
    """
    Immutable data model for a trading position.

    This class holds all position attributes but contains no business logic.
    Use PositionManager for lifecycle operations and state transitions.

    Attributes are organized into logical groups:
    - Core: Essential identification and entry data
    - Targets: Stop loss and take profit levels
    - Status: Current position status and amounts
    - Performance: MFE/MAE tracking
    - R-multiples: Risk-adjusted performance metrics
    - Flags: Boolean state indicators
    - Timestamps: Lifecycle timing information
    """

    # === CORE ATTRIBUTES ===
    symbol_id: str
    side: PositionSide
    entry_price: Decimal
    entry_time: datetime
    initial_amount: Decimal
    entry_atr: Decimal
    initial_risk_atr: Decimal
    total_entry_fee: Decimal

    # === TARGET LEVELS ===
    soft_sl_price: Decimal
    hard_sl_price: Decimal
    tp1_price: Decimal
    tp1a_price: Optional[Decimal] = None  # Quick scalp target (hybrid TP1)

    # === OPTIONAL IDENTIFIERS ===
    position_id: Optional[str] = None
    entry_order_id: Optional[str] = None

    # === CURRENT STATUS ===
    current_amount: Optional[Decimal] = None  # Set to initial_amount if None
    status: PositionStatus = PositionStatus.OPEN
    trailing_sl_price: Optional[Decimal] = None
    breakeven_level: Optional[Decimal] = None

    # === PERFORMANCE TRACKING ===
    mfe: Decimal = Decimal("0")  # Maximum Favorable Excursion
    mae: Decimal = Decimal("0")  # Maximum Adverse Excursion
    peak_price_since_entry: Optional[Decimal] = None  # Set to entry_price if None
    peak_price_since_tp1: Optional[Decimal] = None
    realized_profit: Decimal = Decimal("0.0")

    # === R-MULTIPLE TRACKING ===
    peak_favorable_r: Decimal = Decimal("0")
    peak_adverse_r: Decimal = Decimal("0")
    current_r: Decimal = Decimal("0")
    max_adverse_r_since_entry: Decimal = Decimal("0")
    max_adverse_r_since_tp1: Decimal = Decimal("0")
    trailing_start_r: Optional[Decimal] = None

    # === POST-TP1 QUALITY TRACKING ===
    peak_favorable_r_beyond_tp1: Decimal = Decimal(
        "0"
    )  # Relative gain BEYOND TP1a (0.7R)
    max_adverse_r_since_tp1_post: Decimal = Decimal("0")
    ratio_since_tp1: Decimal = Decimal("0")
    current_ratio: Decimal = Decimal("1.0")  # MFE/MAE ratio

    # === STATE FLAGS ===
    tp1a_hit: bool = False
    is_trailing_active: bool = False
    moved_to_breakeven: bool = False
    scaled_out_on_adverse: bool = False
    progress_breakeven_eligible: bool = False
    rdecay_override_active: bool = False
    weak_post_tp1_detected: bool = False

    # === TIMING & LIFECYCLE ===
    time_of_tp1: Optional[datetime] = None
    tp1_ratio_reset_timestamp: Optional[datetime] = None
    weak_post_tp1_since: Optional[datetime] = None
    post_tp1_probation_start: Optional[datetime] = None
    adverse_scaleout_timestamp: Optional[datetime] = (
        None  # Timestamp when adverse scale-out occurred
    )
    last_checked_bar_ts: Optional[int] = None
    creation_timestamp: Optional[int] = None  # Unix timestamp in milliseconds
    last_exit_check_timestamp: Optional[int] = None

    # === COUNTERS & TRACKING ===
    bars_held: int = 0
    mae_breach_counter: int = 0
    consecutive_low_ratio_checks: int = 0

    # === LOGGING THROTTLING STATE ===
    last_weak_post_tp1_log_ratio: Optional[Decimal] = None
    last_weak_post_tp1_log_timestamp: Optional[float] = None

    # === TEMPORARY STATE ===
    intrabar_breach_started_at: Optional[int] = None
    scaleout_suspend_until_bar_ts: Optional[int] = None
    defer_stale_exit_until_ts: Optional[int] = None

    # === EXIT CONDITIONS ===
    exit_conditions_met: List[str] = field(default_factory=list)
    last_rdecay_peak: Optional[Decimal] = None
    decay_cache: Optional[DecayCache] = None

    # === ENTRY CONTEXT ===
    leverage: Decimal = Decimal("1.0")
    tier_name: str = "Bronze"
    capital_allocation_pct: Decimal = Decimal("0")

    # === IDENTIFIERS (Added for Idempotency) ===
    position_id: Optional[str] = None
    entry_order_id: Optional[str] = None

    def __post_init__(self):
        """Set defaults for optional fields that depend on other fields."""
        if self.current_amount is None:
            object.__setattr__(self, "current_amount", self.initial_amount)
        if self.peak_price_since_entry is None:
            object.__setattr__(self, "peak_price_since_entry", self.entry_price)
        if self.creation_timestamp is None:
            import time

            object.__setattr__(self, "creation_timestamp", int(time.time() * 1000))

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert position to dictionary for JSON serialization.

        Returns:
            Dictionary with all position attributes, properly serialized.
        """
        from bot_v2.utils.decimal_utils import decimal_to_str

        data = {}
        for key, value in self.__dict__.items():
            if value is None:
                data[key] = None
            elif isinstance(value, Decimal):
                data[key] = decimal_to_str(value, precision=8)
            elif isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, (PositionSide, PositionStatus)):
                data[key] = value.value
            elif isinstance(value, bool):
                data[key] = value  # Preserve booleans as JSON booleans
            elif isinstance(value, (int, float)):
                data[key] = value  # Preserve numeric types
            elif isinstance(value, list):
                data[key] = value
            elif isinstance(value, DecayCache):
                # Special handling for DecayCache - convert to dict
                data[key] = {
                    "last_calculation_r": decimal_to_str(value.last_calculation_r),
                    "decay_percentage": decimal_to_str(value.decay_percentage),
                    "multiplier": decimal_to_str(value.multiplier),
                    "scheme": value.scheme,
                    "hysteresis_upper_r": decimal_to_str(value.hysteresis_upper_r),
                    "hysteresis_lower_r": decimal_to_str(value.hysteresis_lower_r),
                    "is_active": value.is_active,
                }
            else:
                data[key] = str(value)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        """
        Create a Position from a dictionary (e.g., loaded from JSON).

        Args:
            data: Dictionary containing position attributes.

        Returns:
            Position instance with all fields properly typed.
        """
        from bot_v2.utils.decimal_utils import to_decimal

        processed = {}

        # Decimal fields
        decimal_fields = {
            "entry_price",
            "initial_amount",
            "entry_atr",
            "initial_risk_atr",
            "total_entry_fee",
            "soft_sl_price",
            "hard_sl_price",
            "tp1_price",
            "tp1a_price",
            "current_amount",
            "trailing_sl_price",
            "breakeven_level",
            "mfe",
            "mae",
            "peak_price_since_entry",
            "peak_price_since_tp1",
            "realized_profit",
            "peak_favorable_r",
            "peak_adverse_r",
            "current_r",
            "max_adverse_r_since_entry",
            "max_adverse_r_since_tp1",
            "trailing_start_r",
            "peak_favorable_r_beyond_tp1",
            "max_adverse_r_since_tp1_post",
            "ratio_since_tp1",
            "current_ratio",
            "last_rdecay_peak",
            "last_weak_post_tp1_log_ratio",
        }

        # Datetime fields
        datetime_fields = {
            "entry_time",
            "time_of_tp1",
            "tp1_ratio_reset_timestamp",
            "weak_post_tp1_since",
            "post_tp1_probation_start",
            "adverse_scaleout_timestamp",
        }

        # Boolean fields
        boolean_fields = {
            "tp1a_hit",
            "is_trailing_active",
            "moved_to_breakeven",
            "scaled_out_on_adverse",
            "progress_breakeven_eligible",
            "rdecay_override_active",
            "weak_post_tp1_detected",
        }

        # Integer fields
        integer_fields = {
            "creation_timestamp",
            "last_exit_check_timestamp",
            "bars_held",
            "mae_breach_counter",
            "consecutive_low_ratio_checks",
            "last_checked_bar_ts",
            "intrabar_breach_started_at",
            "scaleout_suspend_until_bar_ts",
            "defer_stale_exit_until_ts",
        }

        # Float fields
        float_fields = {"last_weak_post_tp1_log_timestamp"}

        for key, value in data.items():
            if value is None:
                processed[key] = None
            elif key in decimal_fields:
                processed[key] = to_decimal(
                    value, context=f"position.{key}", default=None
                )
            elif key in datetime_fields:
                if isinstance(value, str):
                    processed[key] = datetime.fromisoformat(value)
                else:
                    processed[key] = value
            elif key in boolean_fields:
                # Handle boolean fields that might be serialized as strings
                if isinstance(value, str):
                    processed[key] = value.lower() in ("true", "1", "yes")
                else:
                    processed[key] = bool(value)
            elif key in integer_fields:
                # Handle integer fields
                processed[key] = int(value) if value is not None else None
            elif key in float_fields:
                # Handle float fields
                processed[key] = float(value) if value is not None else None
            elif key == "side":
                processed[key] = (
                    PositionSide(value) if isinstance(value, str) else value
                )
            elif key == "status":
                processed[key] = (
                    PositionStatus(value) if isinstance(value, str) else value
                )
            elif key == "exit_conditions_met":
                processed[key] = value if isinstance(value, list) else []
            elif key == "decay_cache":
                # Special handling for DecayCache reconstruction
                if value is not None and isinstance(value, dict):
                    processed[key] = DecayCache(
                        last_calculation_r=to_decimal(
                            value.get("last_calculation_r", "0")
                        ),
                        decay_percentage=to_decimal(value.get("decay_percentage", "0")),
                        multiplier=to_decimal(value.get("multiplier", "0")),
                        scheme=value.get("scheme", ""),
                        hysteresis_upper_r=to_decimal(
                            value.get("hysteresis_upper_r", "0")
                        ),
                        hysteresis_lower_r=to_decimal(
                            value.get("hysteresis_lower_r", "0")
                        ),
                        is_active=value.get("is_active", False),
                    )
                else:
                    processed[key] = None
            else:
                processed[key] = value

        return cls(**processed)

    def copy(self, **changes) -> "Position":
        """
        Create a copy of this position with optional field updates.

        Args:
            **changes: Fields to update in the copy.

        Returns:
            New Position instance with updated fields.

        Example:
            >>> updated = position.copy(current_amount=Decimal('0.01'), status=PositionStatus.CLOSED)
        """
        data = self.to_dict()
        # Remove transient/private attributes (e.g., _last_weak_post_tp1_log)
        for k in list(data.keys()):
            if k.startswith("_"):
                del data[k]
        data.update(changes)
        return self.from_dict(data)
