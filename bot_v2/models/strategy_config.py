"""
Strategy Configuration Model

Dataclass representing per-symbol strategy parameters loaded from
config/strategy_configs.json. This replaces the dict-based config
with a type-safe, validated structure.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class StrategyConfig:
    """
    Per-symbol strategy configuration.

    Loaded from strategy_configs.json and used throughout the trading system.
    All numeric values are stored as Decimal for precision.
    """

    # === IDENTIFICATION ===
    symbol_id: str
    enabled: bool = True
    mode: str = "local_sim"  # "local_sim", "paper", "live"

    # === CAPITAL & RISK ===
    initial_capital: Decimal = Decimal("1000.0")
    leverage: Decimal = Decimal("5")
    capital_usage_percent: Decimal = Decimal("100")

    # === LIVE TRADING SAFETY CAPS ===
    max_notional_per_order: Optional[Decimal] = (
        None  # Per-order notional cap (e.g., $10 for testing)
    )
    daily_max_trades: Optional[int] = None  # Daily trade count limit
    daily_max_notional: Optional[Decimal] = None  # Daily total notional limit
    dry_run: bool = False  # Log orders without executing (safety testing)

    # === TIMEFRAME & INDICATORS ===
    timeframe: str = "30m"
    atr_period: int = 14

    # === STOP LOSS LEVELS (ATR multipliers) ===
    soft_sl_atr_mult: Decimal = Decimal("4.5")
    hard_sl_atr_mult: Decimal = Decimal("5.5")
    catastrophic_stop_mult: Decimal = Decimal("6.0")
    soft_sl_activation_bars: int = 1

    # === TAKE PROFIT LEVELS ===
    tp1a_atr_mult: Decimal = Decimal("0.7")  # Quick scalp target
    tp1a_close_percent: Decimal = Decimal("30")
    tp1_atr_mult: Decimal = Decimal("1.2")  # Primary profit target
    tp1_close_percent: Decimal = Decimal("50")
    tp1_enabled: bool = True  # Controls use of TP1b-style fixed profit exits

    # === TRAILING STOP ===
    trail_sl_atr_mult: Decimal = Decimal("1.2")
    trailing_buffer_pct: Decimal = Decimal("0.60")
    trailing_start_r: Decimal = Decimal("0.85")
    # R-based trailing floors for established winners (in R)
    min_trailing_r_floor_low: Decimal = Decimal("0.0")  # Applies once current_r >= 1R
    min_trailing_r_floor_high: Decimal = Decimal("0.0")  # Applies once current_r >= 2R

    # === AGGRESSIVE PEAK EXIT (APE) CONTROLS ===
    # Option B: Configurable grace + post‑TP1 measurement + initial pullback tolerance
    # NOTE: Default increased from 3% to 15% to allow trending trades more breathing room
    ape_use_post_tp1_measurement: bool = True
    post_tp1_ape_grace_seconds: int = 120
    post_tp1_ape_initial_pullback_pct: Decimal = Decimal(
        "0.15"
    )  # First 3 min: 15% tolerance
    post_tp1_ape_initial_minutes: int = 3
    post_tp1_ape_base_pullback_pct: Decimal = Decimal(
        "0.15"
    )  # Base: 15% pullback tolerance
    # Config-driven APE thresholds (defaults allow trending trades to develop)
    ape_min_r: Decimal = Decimal("0.3")  # Min peak R before APE considered
    ape_pullback_pct: Decimal = Decimal(
        "0.15"
    )  # Required pullback from peak R (15% default, was 3%)
    ape_min_ratio: Decimal = Decimal("5.0")  # Min MFE/MAE ratio for APE

    # === BREAKEVEN ===
    breakeven_trigger_r: Decimal = Decimal("999")  # Disabled by default
    breakeven_offset_atr: Decimal = Decimal("0.0")

    # === ADVERSE SCALE-OUT ===
    partial_exit_on_adverse_r: Decimal = Decimal("2.5")
    partial_exit_pct: Decimal = Decimal("50")
    scaleout_grace_period_seconds: int = 300  # Grace period after adverse scale-out before soft SL can trigger (default: 5 minutes)

    # === GIVEBACK PROTECTION ===
    max_giveback_pct: Decimal = Decimal("15")

    # === TIME-BASED EXITS ===
    min_hold_time_hours: Decimal = Decimal("0.5")
    stale_max_minutes: int = 600
    tp1_time_exit_minutes: int = 90

    # === MFE/MAE RATIO EXITS ===
    mfe_mae_ratio_cutoff: Decimal = Decimal("10.0")
    mfe_mae_min_mae_r: Decimal = Decimal("0.375")
    min_bars_before_mfe_mae_cut: int = 1
    mae_persist_bars: int = 1

    # === INTRABAR MAE DETECTION ===
    mae_intrabar_min_r: Decimal = Decimal("0.20")
    mae_intrabar_ratio_cutoff: Decimal = Decimal("10.0")
    mae_intrabar_hold_seconds: int = 60

    # === SCALE-OUT BEHAVIOR ===
    scaleout_be_offset_atr: Decimal = Decimal("0.1")
    scaleout_trail_atr_mult: Decimal = Decimal("1.2")
    mae_suspend_bars_after_scaleout: int = 2

    # === STALE TRADE MANAGEMENT ===
    stale_min_mfe_r: Decimal = Decimal("0.3")
    stale_exit_even_if_flat: bool = True
    stale_progress_breakeven_r: Decimal = Decimal("0.5")
    stale_enforce_breakeven_before_exit: bool = False
    stale_trail_atr_mult: Decimal = Decimal("1.0")

    # === VOLATILITY FILTERING ===
    volatility_filter_lookback: int = 30
    volatility_threshold_percentile: Decimal = Decimal("0.0")
    min_atr_percent: Decimal = Decimal("0.5")
    volatility_cache_ttl: int = 600
    volatility_regime_lookback: int = 10
    mean_reversion_threshold: Decimal = Decimal("0.7")

    # === COST MODELING ===
    cost_floor_multiplier: Decimal = Decimal("2.0")
    slippage_pct: Decimal = Decimal("0.1")

    # === GRID STRATEGY PARAMETERS ===
    grid_enabled: bool = False
    grid_spacing_pct: Decimal = Decimal("0.01")
    grid_num_grids_up: int = 25
    grid_num_grids_down: int = 25
    grid_order_size_quote: Decimal = Decimal("100.0")
    grid_recentre_trigger: int = 3
    grid_adx_threshold: int = 30
    grid_bb_width_threshold: Decimal = Decimal("0.04")
    grid_max_open_orders: int = 100
    grid_stop_policy: str = (
        "cancel_open_orders"  # cancel_open_orders | keep_open_orders
    )

    # === GRID SESSION MANAGEMENT ===
    grid_session_tp_reinvest: bool = (
        True  # Re-invest gains after hitting TP instead of stopping
    )
    grid_session_tp_pct: Decimal = Decimal("0.05")  # Session take profit threshold (5%)
    grid_session_max_dd_pct: Decimal = Decimal(
        "0.07"
    )  # Session max drawdown threshold (7%)
    grid_reinvest_min_interval_seconds: int = 60  # Cooldown between re-investments
    grid_auto_restart: bool = True  # Auto-restart stopped grids that had activity

    # === GRID CAPITAL MANAGEMENT ===
    grid_capital_constraint: bool = True  # Enable capital-aware grid sizing
    grid_leverage: Optional[int] = None  # Override leverage for grids (None = use tier)

    @classmethod
    def from_dict(cls, symbol_id: str, data: dict) -> "StrategyConfig":
        """
        Create StrategyConfig from JSON dict.
        Converts all numeric values to Decimal for precision.
        """
        from bot_v2.utils.decimal_utils import to_decimal

        return cls(
            symbol_id=symbol_id,
            enabled=data.get("enabled", True),
            mode=data.get("mode", "local_sim"),
            initial_capital=to_decimal(data.get("initial_capital", "1000.0")),
            leverage=to_decimal(data.get("leverage", "5")),
            capital_usage_percent=to_decimal(data.get("capital_usage_percent", "100")),
            timeframe=data.get("timeframe", "30m"),
            atr_period=int(data.get("atr_period", 14)),
            soft_sl_atr_mult=to_decimal(data.get("soft_sl_atr_mult", "4.5")),
            hard_sl_atr_mult=to_decimal(data.get("hard_sl_atr_mult", "5.5")),
            catastrophic_stop_mult=to_decimal(
                data.get("catastrophic_stop_mult", "6.0")
            ),
            soft_sl_activation_bars=int(data.get("soft_sl_activation_bars", 1)),
            tp1a_atr_mult=to_decimal(data.get("tp1a_atr_mult", "0.7")),
            tp1a_close_percent=to_decimal(data.get("tp1a_close_percent", "30")),
            tp1_atr_mult=to_decimal(data.get("tp1_atr_mult", "1.2")),
            tp1_close_percent=to_decimal(data.get("tp1_close_percent", "50")),
            tp1_enabled=bool(data.get("tp1_enabled", True)),
            trail_sl_atr_mult=to_decimal(data.get("trail_sl_atr_mult", "1.2")),
            trailing_buffer_pct=to_decimal(data.get("trailing_buffer_pct", "0.60")),
            trailing_start_r=to_decimal(data.get("trailing_start_r", "0.85")),
            min_trailing_r_floor_low=to_decimal(
                data.get("min_trailing_r_floor_low", "0.0")
            ),
            min_trailing_r_floor_high=to_decimal(
                data.get("min_trailing_r_floor_high", "0.0")
            ),
            ape_use_post_tp1_measurement=bool(
                data.get("ape_use_post_tp1_measurement", True)
            ),
            post_tp1_ape_grace_seconds=int(data.get("post_tp1_ape_grace_seconds", 120)),
            post_tp1_ape_initial_pullback_pct=to_decimal(
                data.get("post_tp1_ape_initial_pullback_pct", "0.05")
            ),
            post_tp1_ape_initial_minutes=int(
                data.get("post_tp1_ape_initial_minutes", 3)
            ),
            post_tp1_ape_base_pullback_pct=to_decimal(
                data.get("post_tp1_ape_base_pullback_pct", "0.03")
            ),
            ape_min_r=to_decimal(data.get("ape_min_r", "1.0")),
            ape_pullback_pct=to_decimal(data.get("ape_pullback_pct", "0.03")),
            ape_min_ratio=to_decimal(data.get("ape_min_ratio", "5.0")),
            breakeven_trigger_r=to_decimal(data.get("breakeven_trigger_r", "999")),
            breakeven_offset_atr=to_decimal(data.get("breakeven_offset_atr", "0.0")),
            partial_exit_on_adverse_r=to_decimal(
                data.get("partial_exit_on_adverse_r", "2.5")
            ),
            partial_exit_pct=to_decimal(data.get("partial_exit_pct", "50")),
            max_giveback_pct=to_decimal(data.get("max_giveback_pct", "15")),
            min_hold_time_hours=to_decimal(data.get("min_hold_time_hours", "0.5")),
            stale_max_minutes=int(data.get("stale_max_minutes", 600)),
            tp1_time_exit_minutes=int(data.get("tp1_time_exit_minutes", 90)),
            mfe_mae_ratio_cutoff=to_decimal(data.get("mfe_mae_ratio_cutoff", "10.0")),
            mfe_mae_min_mae_r=to_decimal(data.get("mfe_mae_min_mae_r", "0.375")),
            min_bars_before_mfe_mae_cut=int(data.get("min_bars_before_mfe_mae_cut", 1)),
            mae_persist_bars=int(data.get("mae_persist_bars", 1)),
            mae_intrabar_min_r=to_decimal(data.get("mae_intrabar_min_r", "0.20")),
            mae_intrabar_ratio_cutoff=to_decimal(
                data.get("mae_intrabar_ratio_cutoff", "10.0")
            ),
            mae_intrabar_hold_seconds=int(data.get("mae_intrabar_hold_seconds", 60)),
            scaleout_be_offset_atr=to_decimal(
                data.get("scaleout_be_offset_atr", "0.1")
            ),
            scaleout_trail_atr_mult=to_decimal(
                data.get("scaleout_trail_atr_mult", "1.2")
            ),
            mae_suspend_bars_after_scaleout=int(
                data.get("mae_suspend_bars_after_scaleout", 2)
            ),
            stale_min_mfe_r=to_decimal(data.get("stale_min_mfe_r", "0.3")),
            stale_exit_even_if_flat=data.get("stale_exit_even_if_flat", True),
            stale_progress_breakeven_r=to_decimal(
                data.get("stale_progress_breakeven_r", "0.5")
            ),
            stale_enforce_breakeven_before_exit=data.get(
                "stale_enforce_breakeven_before_exit", False
            ),
            stale_trail_atr_mult=to_decimal(data.get("stale_trail_atr_mult", "1.0")),
            volatility_filter_lookback=int(data.get("volatility_filter_lookback", 30)),
            volatility_threshold_percentile=to_decimal(
                data.get("volatility_threshold_percentile", "0.0")
            ),
            min_atr_percent=to_decimal(data.get("min_atr_percent", "0.5")),
            volatility_cache_ttl=int(data.get("volatility_cache_ttl", 600)),
            volatility_regime_lookback=int(data.get("volatility_regime_lookback", 10)),
            mean_reversion_threshold=to_decimal(
                data.get("mean_reversion_threshold", "0.7")
            ),
            cost_floor_multiplier=to_decimal(data.get("cost_floor_multiplier", "2.0")),
            slippage_pct=to_decimal(
                data.get("slippage_pct", data.get("expected_slippage_percent", "0.1"))
            ),
            # Live trading safety caps
            max_notional_per_order=(
                to_decimal(data.get("max_notional_per_order"))
                if data.get("max_notional_per_order")
                else None
            ),
            daily_max_trades=(
                int(data["daily_max_trades"]) if "daily_max_trades" in data else None
            ),
            daily_max_notional=(
                to_decimal(data.get("daily_max_notional"))
                if data.get("daily_max_notional")
                else None
            ),
            dry_run=data.get("dry_run", False),
            # Grid Strategy
            grid_enabled=bool(data.get("grid_enabled", False)),
            grid_spacing_pct=to_decimal(data.get("grid_spacing_pct", "0.01")),
            grid_num_grids_up=int(data.get("grid_num_grids_up", 25)),
            grid_num_grids_down=int(data.get("grid_num_grids_down", 25)),
            grid_order_size_quote=to_decimal(
                data.get("grid_order_size_quote", "100.0")
            ),
            grid_recentre_trigger=int(data.get("grid_recentre_trigger", 3)),
            grid_adx_threshold=int(data.get("grid_adx_threshold", 30)),
            grid_bb_width_threshold=to_decimal(
                data.get("grid_bb_width_threshold", "0.04")
            ),
            grid_max_open_orders=int(data.get("grid_max_open_orders", 100)),
            grid_stop_policy=data.get("grid_stop_policy", "cancel_open_orders"),
            # Grid Session Management
            grid_session_tp_reinvest=bool(data.get("grid_session_tp_reinvest", True)),
            grid_session_tp_pct=to_decimal(data.get("grid_session_tp_pct", "0.05")),
            grid_session_max_dd_pct=to_decimal(
                data.get("grid_session_max_dd_pct", "0.07")
            ),
            grid_reinvest_min_interval_seconds=int(
                data.get("grid_reinvest_min_interval_seconds", 60)
            ),
            grid_auto_restart=bool(data.get("grid_auto_restart", True)),
        )

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        from bot_v2.utils.decimal_utils import decimal_to_str

        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "initial_capital": decimal_to_str(self.initial_capital),
            "leverage": decimal_to_str(self.leverage),
            "capital_usage_percent": decimal_to_str(self.capital_usage_percent),
            "timeframe": self.timeframe,
            "atr_period": self.atr_period,
            "soft_sl_atr_mult": decimal_to_str(self.soft_sl_atr_mult),
            "hard_sl_atr_mult": decimal_to_str(self.hard_sl_atr_mult),
            "catastrophic_stop_mult": decimal_to_str(self.catastrophic_stop_mult),
            "soft_sl_activation_bars": self.soft_sl_activation_bars,
            "tp1a_atr_mult": decimal_to_str(self.tp1a_atr_mult),
            "tp1a_close_percent": decimal_to_str(self.tp1a_close_percent),
            "tp1_atr_mult": decimal_to_str(self.tp1_atr_mult),
            "tp1_close_percent": decimal_to_str(self.tp1_close_percent),
            "trail_sl_atr_mult": decimal_to_str(self.trail_sl_atr_mult),
            "trailing_buffer_pct": decimal_to_str(self.trailing_buffer_pct),
            "tp1_enabled": self.tp1_enabled,
            "trailing_start_r": self.trailing_start_r,
            "min_trailing_r_floor_low": decimal_to_str(self.min_trailing_r_floor_low),
            "min_trailing_r_floor_high": decimal_to_str(self.min_trailing_r_floor_high),
            "ape_use_post_tp1_measurement": self.ape_use_post_tp1_measurement,
            "post_tp1_ape_grace_seconds": self.post_tp1_ape_grace_seconds,
            "post_tp1_ape_initial_pullback_pct": decimal_to_str(
                self.post_tp1_ape_initial_pullback_pct
            ),
            "post_tp1_ape_initial_minutes": self.post_tp1_ape_initial_minutes,
            "post_tp1_ape_base_pullback_pct": decimal_to_str(
                self.post_tp1_ape_base_pullback_pct
            ),
            "ape_min_r": decimal_to_str(self.ape_min_r),
            "ape_pullback_pct": decimal_to_str(self.ape_pullback_pct),
            "ape_min_ratio": decimal_to_str(self.ape_min_ratio),
            "partial_exit_on_adverse_r": decimal_to_str(self.partial_exit_on_adverse_r),
            "partial_exit_pct": decimal_to_str(self.partial_exit_pct),
            "max_giveback_pct": decimal_to_str(self.max_giveback_pct),
            "min_hold_time_hours": decimal_to_str(self.min_hold_time_hours),
            "stale_max_minutes": self.stale_max_minutes,
            "tp1_time_exit_minutes": self.tp1_time_exit_minutes,
            "mfe_mae_ratio_cutoff": decimal_to_str(self.mfe_mae_ratio_cutoff),
            "mfe_mae_min_mae_r": decimal_to_str(self.mfe_mae_min_mae_r),
            "min_bars_before_mfe_mae_cut": self.min_bars_before_mfe_mae_cut,
            "mae_persist_bars": self.mae_persist_bars,
            "mae_intrabar_min_r": decimal_to_str(self.mae_intrabar_min_r),
            "mae_intrabar_ratio_cutoff": decimal_to_str(self.mae_intrabar_ratio_cutoff),
            "mae_intrabar_hold_seconds": self.mae_intrabar_hold_seconds,
            "scaleout_be_offset_atr": decimal_to_str(self.scaleout_be_offset_atr),
            "scaleout_trail_atr_mult": decimal_to_str(self.scaleout_trail_atr_mult),
            "mae_suspend_bars_after_scaleout": self.mae_suspend_bars_after_scaleout,
            "stale_min_mfe_r": decimal_to_str(self.stale_min_mfe_r),
            "stale_exit_even_if_flat": self.stale_exit_even_if_flat,
            "stale_progress_breakeven_r": decimal_to_str(
                self.stale_progress_breakeven_r
            ),
            "stale_enforce_breakeven_before_exit": self.stale_enforce_breakeven_before_exit,
            "stale_trail_atr_mult": decimal_to_str(self.stale_trail_atr_mult),
            "volatility_filter_lookback": self.volatility_filter_lookback,
            "volatility_threshold_percentile": decimal_to_str(
                self.volatility_threshold_percentile
            ),
            "min_atr_percent": decimal_to_str(self.min_atr_percent),
            "volatility_cache_ttl": self.volatility_cache_ttl,
            "volatility_regime_lookback": self.volatility_regime_lookback,
            "mean_reversion_threshold": decimal_to_str(self.mean_reversion_threshold),
            "cost_floor_multiplier": decimal_to_str(self.cost_floor_multiplier),
            "slippage_pct": decimal_to_str(self.slippage_pct),
            # Grid parameters
            "grid_enabled": self.grid_enabled,
            "grid_spacing_pct": decimal_to_str(self.grid_spacing_pct),
            "grid_num_grids_up": self.grid_num_grids_up,
            "grid_num_grids_down": self.grid_num_grids_down,
            "grid_order_size_quote": decimal_to_str(self.grid_order_size_quote),
            "grid_recentre_trigger": self.grid_recentre_trigger,
            "grid_adx_threshold": self.grid_adx_threshold,
            "grid_bb_width_threshold": decimal_to_str(self.grid_bb_width_threshold),
            "grid_max_open_orders": self.grid_max_open_orders,
            "grid_stop_policy": self.grid_stop_policy,
            # Grid Session Management
            "grid_session_tp_reinvest": self.grid_session_tp_reinvest,
            "grid_session_tp_pct": decimal_to_str(self.grid_session_tp_pct),
            "grid_session_max_dd_pct": decimal_to_str(self.grid_session_max_dd_pct),
            "grid_reinvest_min_interval_seconds": self.grid_reinvest_min_interval_seconds,
            "grid_auto_restart": self.grid_auto_restart,
        }
