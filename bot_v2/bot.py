"""
Main TradingBot class for bot_v2.

Integrates all bot_v2 modules:
- Position tracking
- Exit engine
- Order execution
- Risk management
- State persistence
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from bot_v2.execution.live_exchange import LiveExchange
from bot_v2.execution.market_data_cache import MarketDataCache
from bot_v2.execution.order_manager import OrderManager
from bot_v2.execution.simulated_exchange import SimulatedExchange

# OLD BOT PROVEN MODULES - Extracted from 4+ years battle-tested bot.py
from bot_v2.exit_engine.engine_v1 import ExitConditionEngine
from bot_v2.filters.cost_filter import CostFilter
from bot_v2.filters.volatility_filter import VolatilityFilter
from bot_v2.models.enums import PositionSide, PositionStatus, TradeSide
from bot_v2.models.position import Position
from bot_v2.models.position_v1 import PositionState as PositionV1
from bot_v2.models.position_v1 import PositionStatus as PositionStatusV1
from bot_v2.models.position_v1 import TradeSide as TradeSideV1
from bot_v2.models.strategy_config import StrategyConfig
from src.notification import Notifier
from bot_v2.persistence.state_manager import StateManager
from bot_v2.position.tracker import PositionTracker
from bot_v2.position.trailing_stop import TrailingStopCalculator
from bot_v2.risk.adaptive_integration import AdaptiveRiskIntegration
from bot_v2.risk.capital_manager import CapitalManager
from bot_v2.risk.global_risk_manager import GlobalRiskManager
from bot_v2.utils.latency_tracker import LatencyTracker
from bot_v2.utils.logging_config import setup_logging
from bot_v2.utils.performance_profiler import profile_signal_processing
from bot_v2.utils.symbol_utils import match_symbol_format, normalize_to_config_format

setup_logging()
logger = logging.getLogger(__name__)

# Performance tracking configuration (Phase 1: Measurement & Baseline)
ENABLE_LATENCY_TRACKING = os.getenv("ENABLE_LATENCY_TRACKING", "true").lower() == "true"
ENABLE_PERFORMANCE_PROFILING = (
    os.getenv("ENABLE_PERFORMANCE_PROFILING", "false").lower() == "true"
)
ENABLE_GRID_LATENCY_LOGGING = (
    os.getenv("ENABLE_GRID_LATENCY_LOGGING", "true").lower() == "true"
)
GRID_LATENCY_WARN_MS = float(os.getenv("GRID_LATENCY_WARN_MS", "1500"))
GRID_LATENCY_WARN_INTERVAL_SECS = float(
    os.getenv("GRID_LATENCY_WARN_INTERVAL_SECS", "60")
)
GRID_LATENCY_WARN_DELTA_MS = float(os.getenv("GRID_LATENCY_WARN_DELTA_MS", "500"))

# Constants (EXACT from bot_v1 line 72)
HEARTBEAT_INTERVAL_SECONDS: int = 3600  # Send heartbeat every hour


def _convert_position_to_v1(pos: Position) -> PositionV1:
    """Convert bot_v2 Position (dataclass) to old bot PositionState for exit engine."""
    # Map PositionSide enum to TradeSideV1
    side_map = {
        PositionSide.LONG: TradeSideV1.BUY,
        PositionSide.SHORT: TradeSideV1.SELL,
    }

    # Map PositionStatus to PositionStatusV1
    status_map = {
        "open": PositionStatusV1.OPEN,
        "partially_closed": PositionStatusV1.PARTIALLY_CLOSED,
    }

    # Convert to PositionStateV1 - use dataclass dict() method
    pos_dict = {
        "symbol_id": pos.symbol_id,
        "side": side_map[pos.side],
        "entry_price": pos.entry_price,
        "entry_time": pos.entry_time,
        "initial_amount": pos.initial_amount,
        "entry_atr": pos.entry_atr,
        "initial_risk_atr": pos.initial_risk_atr,
        "soft_sl_price": pos.soft_sl_price,
        "hard_sl_price": pos.hard_sl_price,
        "tp1_price": pos.tp1_price,
        "tp1a_price": pos.tp1a_price,
        "total_entry_fee": pos.total_entry_fee,
        "current_amount": pos.current_amount,
        "status": status_map.get(pos.status.value, PositionStatusV1.OPEN),
        "trailing_sl_price": pos.trailing_sl_price,
        "time_of_tp1": pos.time_of_tp1,
        "last_checked_bar_ts": pos.last_checked_bar_ts,
        "mfe": pos.mfe,
        "mae": pos.mae,
        "peak_price_since_entry": pos.peak_price_since_entry,
        "peak_price_since_tp1": pos.peak_price_since_tp1,
        "realized_profit": pos.realized_profit,
        "is_trailing_active": pos.is_trailing_active,
        "moved_to_breakeven": pos.moved_to_breakeven,
        "scaled_out_on_adverse": pos.scaled_out_on_adverse,
        "adverse_scaleout_timestamp": pos.adverse_scaleout_timestamp,
        "mae_breach_counter": pos.mae_breach_counter,
        "intrabar_breach_started_at": pos.intrabar_breach_started_at,
        "scaleout_suspend_until_bar_ts": pos.scaleout_suspend_until_bar_ts,
        "progress_breakeven_eligible": pos.progress_breakeven_eligible,
        "defer_stale_exit_until_ts": pos.defer_stale_exit_until_ts,
        "peak_favorable_r": pos.peak_favorable_r,
        "peak_adverse_r": pos.peak_adverse_r,
        "current_r": pos.current_r,
        "bars_held": pos.bars_held,
        "creation_timestamp": pos.creation_timestamp,
        "exit_conditions_met": pos.exit_conditions_met,
        "last_exit_check_timestamp": pos.last_exit_check_timestamp,
        "breakeven_level": pos.breakeven_level,
        "trailing_start_r": pos.trailing_start_r,
        "max_adverse_r_since_entry": pos.max_adverse_r_since_entry,
        "max_adverse_r_since_tp1": pos.max_adverse_r_since_tp1,
        "last_rdecay_peak": pos.last_rdecay_peak,
        "rdecay_override_active": pos.rdecay_override_active,
        "current_ratio": pos.current_ratio,
        "peak_favorable_r_beyond_tp1": pos.peak_favorable_r_beyond_tp1,
        "max_adverse_r_since_tp1_post": pos.max_adverse_r_since_tp1_post,
        "ratio_since_tp1": pos.ratio_since_tp1,
        "tp1_ratio_reset_timestamp": pos.tp1_ratio_reset_timestamp,
        "weak_post_tp1_detected": pos.weak_post_tp1_detected,
        "weak_post_tp1_since": pos.weak_post_tp1_since,
        "consecutive_low_ratio_checks": pos.consecutive_low_ratio_checks,
        "post_tp1_probation_start": pos.post_tp1_probation_start,
        "tp1a_hit": pos.tp1a_hit,
    }

    return PositionV1(**pos_dict)


class TradingBot:
    """
    Main trading bot that orchestrates all components.

    Components:
    - PositionTracker: Manages active positions
    - ExitConditionEngine: Monitors and triggers exits
    - OrderManager: Handles order execution
    - StateManager: Persists state across restarts
    - CapitalManager: Tracks capital per symbol
    - AdaptiveRiskIntegration: Tier-based position sizing

    Supports both single-symbol and multi-symbol operation:
    - Single-symbol: Pass StrategyConfig directly (backward compatible)
    - Multi-symbol: Pass Dict[str, StrategyConfig] with per-symbol mode routing
    """

    def __init__(
        self,
        config: StrategyConfig | Dict[str, StrategyConfig],
        simulation_mode: bool | None = None,
    ):
        """
        Initialize the trading bot.

        Args:
            config: Single StrategyConfig (single-symbol mode) or Dict[str, StrategyConfig] (multi-symbol mode)
            simulation_mode: DEPRECATED - mode is now read from StrategyConfig.mode field.
                           For backward compatibility: if provided, overrides config.mode for single-symbol mode.
        """
        # Preserve legacy simulation_mode flag on the instance for tests
        # and backward compatibility.
        self.simulation_mode = (
            bool(simulation_mode) if simulation_mode is not None else False
        )

        # Detect single-symbol vs multi-symbol mode
        if isinstance(config, dict):
            # Multi-symbol mode
            self.strategy_configs: Dict[str, StrategyConfig] = config
            self.multi_symbol_mode = True
            # In multi-symbol mode, simulation_mode parameter is ignored (use per-symbol mode)
            if simulation_mode is not None:
                logger.warning(
                    "simulation_mode parameter ignored in multi-symbol mode. Use per-symbol config.mode instead."
                )
        else:
            # Single-symbol mode (backward compatible)
            # Some tests pass a MagicMock StrategyConfig without a `symbol_id`
            # attribute. Attempt to infer or set a sensible default to remain
            # compatible with those tests.
            if not hasattr(config, "symbol_id"):
                inferred = None
                if hasattr(config, "symbols") and config.symbols:
                    try:
                        inferred = config.symbols[0]
                    except Exception:
                        inferred = None
                # Fall back to a generic identifier if nothing else available
                if not inferred:
                    inferred = "DEFAULT"
                try:
                    setattr(config, "symbol_id", inferred)
                except Exception:
                    # If we cannot set attribute (unlikely for MagicMock), continue
                    pass

            self.strategy_configs: Dict[str, StrategyConfig] = {
                config.symbol_id: config
            }
            self.multi_symbol_mode = False
            # Apply simulation_mode override for backward compatibility
            if simulation_mode is not None:
                config.mode = "local_sim" if simulation_mode else "live"
                logger.info(
                    f"Using legacy simulation_mode={simulation_mode}, mapped to mode={config.mode}"
                )

        self.is_running = False

        # Data directory for persistence (use first config's data_dir if available)
        first_config = next(iter(self.strategy_configs.values()))
        if hasattr(first_config, "data_dir"):
            self.data_dir = (
                Path(first_config.data_dir)
                if isinstance(first_config.data_dir, str)
                else first_config.data_dir
            )
        else:
            self.data_dir = Path("data_futures")

        # Initialize components
        self.positions: Dict[str, Position] = {}  # Active positions by symbol
        self.position_tracker = PositionTracker()  # For metrics tracking
        self.trailing_calculator = TrailingStopCalculator()  # For trailing stops
        self.state_manager = StateManager(data_dir=self.data_dir)
        self.capital_manager = CapitalManager(
            data_dir=self.data_dir, strategy_configs=self.strategy_configs
        )
        self.risk_manager = AdaptiveRiskIntegration(
            data_dir=self.data_dir, capital_manager=self.capital_manager
        )
        self.global_risk_manager = GlobalRiskManager(
            capital_manager=self.capital_manager,
            max_drawdown_pct=0.20,  # 20% portfolio drawdown halt
        )
        self.volatility_filter = VolatilityFilter()  # Entry filter
        self.cost_filter = CostFilter()  # Entry filter
        self.trade_history: List[Dict[str, Any]] = []  # Trade history for analytics
        self.grid_trade_history: List[Dict[str, Any]] = []  # Closed grid trades
        self._grid_history_lock = asyncio.Lock()
        self.grid_orchestrators: Dict[str, Any] = {}  # Active grid sessions by symbol
        self.grid_states: Dict[str, Any] = {}  # Persistent grid states

        # Initialize Telegram notifier (EXACT from bot_v1)
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.notifier = Notifier(token=telegram_token, chat_id=telegram_chat_id)
        logger.info(
            f"Telegram notifier: {'Enabled' if telegram_token and telegram_chat_id else 'Disabled'}"
        )

        # Wire capital depletion alerts
        async def _capital_alert(symbol: str, message: str):
            await self.notifier.send(f"\U0001f6a8 {message}")

        self.capital_manager.set_critical_alert_callback(_capital_alert)

        # Initialize exchanges based on required modes
        self.live_exchange: Optional[LiveExchange] = None
        self.sim_exchange: Optional[SimulatedExchange] = None

        # Check if any symbol requires live mode
        live_symbols = [
            sym for sym, cfg in self.strategy_configs.items() if cfg.mode == "live"
        ]
        sim_symbols = [
            sym for sym, cfg in self.strategy_configs.items() if cfg.mode != "live"
        ]

        # Initialize shared market data cache (Phase 2: Optimization)
        enable_cache = os.getenv("ENABLE_MARKET_DATA_CACHE", "true").lower() == "true"
        if enable_cache:
            cache_ttl = int(os.getenv("MARKET_DATA_CACHE_TTL", "30"))
            self.market_data_cache = MarketDataCache(
                default_ttl=cache_ttl, max_size=500
            )
            logger.info(
                f"📦 Shared market data cache initialized (TTL={cache_ttl}s, max_size=500)"
            )
        else:
            self.market_data_cache = None
            logger.info("Market data cache disabled")

        # Initialize order state manager for ALL tracking (Phase 5: Unified State)
        from bot_v2.execution.order_state_manager import OrderStateManager

        self.order_state_manager = OrderStateManager(data_dir=self.data_dir)

        # Initialize simulated exchange (always available)
        fee = Decimal("0.0002")  # 0.02% Binance Futures maker fee (limit orders)
        self.sim_exchange = SimulatedExchange(
            fee=fee,
            cache=self.market_data_cache,
            order_state_manager=self.order_state_manager,
        )
        logger.info(f"🔧 Simulated exchange initialized for {len(sim_symbols)} symbols")

        # Initialize live exchange if needed
        if live_symbols:
            # Get credentials from first config or environment
            exchange_name = getattr(
                first_config, "exchange_name", os.getenv("EXCHANGE_NAME", "binance")
            )
            api_key = getattr(first_config, "api_key", os.getenv("FUTURES_API_KEY", ""))
            api_secret = getattr(
                first_config, "api_secret", os.getenv("FUTURES_API_SECRET", "")
            )

            if not api_key or not api_secret:
                raise ValueError(
                    f"Live mode required for {len(live_symbols)} symbols but API credentials missing. "
                    f"Set FUTURES_API_KEY and FUTURES_API_SECRET environment variables."
                )

            self.live_exchange = LiveExchange(
                name=exchange_name,
                key=api_key,
                secret=api_secret,
                cache=self.market_data_cache,
                order_state_manager=self.order_state_manager,
            )
            logger.info(
                f"🚀 Live exchange initialized for {len(live_symbols)} symbols: {', '.join(live_symbols)}"
            )
        else:
            logger.info("No symbols in live mode, live exchange not initialized")

        # Initialize order managers for both exchanges
        # We'll route to the correct one based on symbol mode
        self.sim_order_manager = OrderManager(
            self.sim_exchange, order_state_manager=self.order_state_manager
        )
        if self.live_exchange:
            self.live_order_manager = OrderManager(
                self.live_exchange, order_state_manager=self.order_state_manager
            )
        else:
            self.live_order_manager = None

        # Signal queue for webhook signals
        self.signal_queue: asyncio.Queue = asyncio.Queue()

        # Performance tracking
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = Decimal("0")

        # Heartbeat and daily summary tracking (EXACT from bot_v1)
        self.last_heartbeat_time = 0.0
        self.last_summary_sent = datetime.now(timezone.utc) - timedelta(days=1)
        self.last_reconciliation_time = 0.0  # Phase 2: Periodic reconciliation
        self.last_prune_time = 0.0  # Phase 5: State pruning
        self.order_state_prune_interval_sec = int(
            os.getenv("BOTV2_ORDER_STATE_PRUNE_INTERVAL_SECS", "21600")
        )
        self._start_time = datetime.now(timezone.utc)  # Track uptime

        # In-memory caches (performance)
        # ATR cache keyed by (symbol, timeframe) -> {"last_bar_ts": int, "atr": Decimal}
        self._atr_cache: dict[tuple[str, str], dict] = {}
        # Optional price cache: keyed by symbol -> {"price": Decimal, "ts": float}
        self._price_cache: dict[str, dict] = {}
        # Debounce map for status notifications: (symbol,event) -> last_sent_ts
        self._status_debounce: dict[tuple[str, str], float] = {}
        # Grid latency warning throttling (avoid per-tick warning spam)
        self._grid_latency_last_warn_ts = 0.0
        self._grid_latency_last_warn_ms = 0.0
        self._grid_latency_suppressed_count = 0

        # Concurrency control (Phase 1: Bounded Concurrency)
        max_concurrency = int(os.getenv("MAX_SIGNAL_CONCURRENCY", "1"))
        self._signal_semaphore = asyncio.Semaphore(max_concurrency)
        self._symbol_locks: Dict[str, asyncio.Lock] = {}

        # Leverage cache (Phase 2: Optimization)
        self._leverage_cache: Dict[str, int] = {}

        # Signal deduplication (Phase 3: Burst Deduplication)
        self._dedup_window = float(os.getenv("DEDUP_WINDOW_SECONDS", "0.0"))
        self._recent_signals: Dict[Tuple[str, str], float] = {}

        logger.info(
            f"✅ TradingBot initialized successfully (multi-symbol={self.multi_symbol_mode}, {len(self.strategy_configs)} symbols)"
        )
        # Ensure SignalProcessor is always initialized
        self._init_signal_processor()

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol format to match strategy configs.
        Uses centralized symbol_utils for consistent format handling.

        Args:
            symbol: Trading symbol in any format (UNIUSDT, UNI/USDT, etc.)

        Returns:
            Normalized symbol that matches strategy_configs keys

        Raises:
            ValueError: If symbol not found in strategy configs
        """
        # If already in configs, return as-is
        if symbol in self.strategy_configs:
            return symbol

        # Try to find matching symbol using utility
        matched = match_symbol_format(symbol, list(self.strategy_configs.keys()))
        if matched:
            logger.debug(f"Normalized symbol: {symbol} -> {matched}")
            return matched

        # Fallback: try config format normalization (UNI/USDT standard)
        normalized = normalize_to_config_format(symbol)
        if normalized in self.strategy_configs:
            logger.debug(
                f"Normalized symbol via config format: {symbol} -> {normalized}"
            )
            return normalized

        # Symbol not found - raise error
        available_symbols = ", ".join(list(self.strategy_configs.keys()))
        raise ValueError(
            f"Symbol '{symbol}' not found in strategy configs. "
            f"Available symbols: {available_symbols}"
        )

    def _get_config(self, symbol: str) -> StrategyConfig:
        """
        Get strategy config for a symbol.

        Args:
            symbol: The trading symbol

        Returns:
            StrategyConfig for the symbol

        Raises:
            ValueError: If symbol config not found
        """
        config = self.strategy_configs.get(symbol)
        if not config:
            raise ValueError(
                f"[{symbol}] No strategy config found. Symbol not loaded in bot."
            )
        return config

    def _get_exchange_for_symbol(self, symbol: str):
        """
        Get the correct exchange for a symbol based on its mode configuration.

        Args:
            symbol: The trading symbol

        Returns:
            ExchangeInterface for the symbol (LiveExchange or SimulatedExchange)

        Raises:
            RuntimeError: If live mode requested but live exchange not available
        """
        config = self.strategy_configs.get(symbol)
        if not config:
            logger.error(
                f"[{symbol}] No strategy config found, defaulting to simulated exchange"
            )
            return self.sim_exchange

        if config.mode == "live":
            if not self.live_exchange:
                raise RuntimeError(
                    f"[{symbol}] is configured for LIVE mode but live exchange is not available. "
                    f"Check API credentials."
                )
            return self.live_exchange
        else:  # local_sim or paper
            return self.sim_exchange

    # Alias for backward compatibility
    def _get_exchange(self, symbol: str):
        """Alias for _get_exchange_for_symbol."""
        return self._get_exchange_for_symbol(symbol)

    def _get_order_manager_for_symbol(self, symbol: str):
        """
        Get the correct order manager for a symbol based on its mode configuration.

        Args:
            symbol: The trading symbol

        Returns:
            OrderManager for the symbol (live or simulated)

        Raises:
            RuntimeError: If live mode requested but live order manager not available
        """
        config = self.strategy_configs.get(symbol)
        if not config:
            logger.error(
                f"[{symbol}] No strategy config found, defaulting to simulated order manager"
            )
            return self.sim_order_manager

        if config.mode == "live":
            if not self.live_order_manager:
                raise RuntimeError(
                    f"[{symbol}] is configured for LIVE mode but live order manager is not available. "
                    f"Check API credentials."
                )
            return self.live_order_manager
        else:  # local_sim or paper
            return self.sim_order_manager

    def _get_config_for_symbol(self, symbol: str) -> StrategyConfig:
        """
        Get strategy config for a symbol.

        Args:
            symbol: The trading symbol

        Returns:
            StrategyConfig for the symbol

        Raises:
            KeyError: If symbol not found in strategy_configs
        """
        if symbol not in self.strategy_configs:
            raise KeyError(f"No strategy configuration found for symbol {symbol}")
        return self.strategy_configs[symbol]

        logger.info("✅ TradingBot initialized successfully")

    def _get_exchange_step_size(self, exchange: Any, symbol: str) -> Optional[Decimal]:
        """
        Get market lot-size step for amount normalization preview.

        Args:
            exchange: Exchange instance (LiveExchange/SimulatedExchange)
            symbol: Symbol in config format (e.g., BTC/USDT)

        Returns:
            Step size as Decimal if available, otherwise None
        """
        if not hasattr(exchange, "exchange") or exchange.exchange is None:
            return None

        try:
            market = exchange.exchange.market(symbol)
            info = market.get("info") if isinstance(market, dict) else None
            if info and isinstance(info, dict):
                for f in info.get("filters", []) or []:
                    if f.get("filterType") in ("LOT_SIZE", "MARKET_LOT_SIZE"):
                        try:
                            step_size = Decimal(str(f.get("stepSize")))
                            if step_size > Decimal("0"):
                                return step_size
                        except Exception:
                            continue
        except Exception:
            logger.debug(
                f"[{symbol}] Could not fetch exchange step size",
                exc_info=True,
            )

        return None

    def _preview_normalized_order_amount(
        self,
        exchange: Any,
        symbol: str,
        requested_amount: Decimal,
    ) -> Decimal:
        """
        Preview amount after exchange lot-size/precision normalization.

        This mirrors OrderManager normalization to pre-empt zero-sized orders.
        """
        if requested_amount <= Decimal("0"):
            return Decimal("0")

        step_size = self._get_exchange_step_size(exchange, symbol)
        if step_size is not None:
            steps = (requested_amount / step_size).to_integral_value(
                rounding=ROUND_DOWN
            )
            return steps * step_size

        try:
            if hasattr(exchange, "exchange") and exchange.exchange is not None:
                market = exchange.exchange.market(symbol)
                prec = market.get("precision", {}).get("amount")
                if isinstance(prec, int) and prec >= 0:
                    q = Decimal("1").scaleb(-prec)
                    return (requested_amount // q) * q
        except Exception:
            logger.debug(
                f"[{symbol}] Could not preview precision normalization",
                exc_info=True,
            )

        return requested_amount

    async def initialize(self) -> None:
        """Initialize bot by loading persisted state."""
        logger.info("Initializing bot state...")

        # Load persisted states (Phase 1: Multi-State restoration)
        try:
            (
                self.positions,
                _,  # Capitals loaded by CapitalManager
                self.trade_history,
                self.grid_states,
                self.grid_trade_history,
            ) = self.state_manager.load_states()

            logger.info(
                f"Successfully restored state: {len(self.positions)} positions, "
                f"{len(self.grid_states)} grid sessions, "
                f"{len(self.grid_trade_history)} grid closed trades."
            )
        except Exception as e:
            logger.error(
                f"Failed to load state during initialization: {e}", exc_info=True
            )
            # Fallback to standard loading if multi-load fails
            self.positions = self.state_manager.load_positions()
            self.trade_history = self.state_manager.load_trade_history()
            self.grid_states = {}
            self.grid_trade_history = self.state_manager.load_grid_trade_history()

        # Initialize Grid Orchestrators for all symbols
        from bot_v2.grid.orchestrator import GridOrchestrator

        for symbol, config in self.strategy_configs.items():
            if config.grid_enabled:
                order_manager = self._get_order_manager_for_symbol(symbol)
                exchange = self._get_exchange_for_symbol(symbol)
                orchestrator = GridOrchestrator(
                    symbol=symbol,
                    config=config,
                    order_manager=order_manager,
                    exchange=exchange,
                    risk_manager=self.risk_manager,
                    capital_manager=self.capital_manager,
                    on_grid_trade_closed=self._on_grid_trade_closed,
                    on_grid_fill=self._on_grid_fill,
                )

                # Restore high-level state if available
                if symbol in self.grid_states:
                    state = self.grid_states[symbol]
                    # Note: active_orders recovery now handled via OrderStateManager inside start()
                    orchestrator.centre_price = state.centre_price
                    orchestrator.session_fill_count = int(state.grid_fills) + int(
                        state.counter_fills
                    )

                    if state.is_active:
                        logger.info(
                            f"[{symbol}] Grid session was active in previous state. Resuming..."
                        )
                        # We don't set is_active=True here yet, let start() handle it and recover orders

                self.grid_orchestrators[symbol] = orchestrator

                # AUTO-START: Start if was active OR if grid_enabled is set to true
                is_previously_active = (
                    self.grid_states.get(symbol).is_active
                    if symbol in self.grid_states
                    else False
                )

                if config.grid_enabled or is_previously_active:
                    logger.info(f"[{symbol}] Starting grid orchestrator...")
                    await orchestrator.start()

                logger.info(f"[{symbol}] Grid Orchestrator ready.")

        # Initialize exchange connection (load markets)
        if self.live_exchange:
            try:
                logger.info("Setting up live exchange connection...")
                await self.live_exchange.setup()
            except Exception as e:
                logger.warning(f"Failed to setup live exchange: {e}")

        # Pre-load market data cache for all symbols (Phase 2 optimization)
        if self.market_data_cache and self.live_exchange:
            all_symbols = list(self.strategy_configs.keys())
            try:
                # Use public_exchange if available to avoid IP restrictions on read-only data
                exchange_to_use = getattr(
                    self.live_exchange, "public_exchange", self.live_exchange.exchange
                )
                await self.market_data_cache.preload_symbols(
                    all_symbols, exchange_to_use
                )
                logger.info("Market data cache pre-loaded for all symbols")
            except Exception as e:
                logger.warning(f"Failed to pre-load market data cache: {e}")
        elif self.market_data_cache and self.sim_exchange:
            # For simulation mode, use sim exchange (though it may not have real data)
            all_symbols = list(self.strategy_configs.keys())
            try:
                # Use public_exchange for simulation
                exchange_to_use = getattr(
                    self.sim_exchange,
                    "public_exchange",
                    getattr(self.sim_exchange, "exchange", None),
                )
                if exchange_to_use:
                    await self.market_data_cache.preload_symbols(
                        all_symbols, exchange_to_use
                    )
                    logger.info(
                        "Market data cache pre-loaded for all symbols (simulation)"
                    )
                else:
                    logger.warning(
                        "Could not find exchange object in SimulatedExchange"
                    )
            except Exception as e:
                logger.warning(f"Failed to pre-load market data cache: {e}")
        else:
            logger.debug(
                "Market data cache pre-loading skipped (cache disabled or no exchange)"
            )

        # Reconcile positions with exchange (Phase 1: Safety)
        try:
            await self._reconcile_positions_with_exchange()
        except Exception as e:
            logger.error(
                f"Position reconciliation failed: {e}. Continuing with persisted state.",
                exc_info=True,
            )

    async def _run_periodic_reconciliation(self) -> None:
        """Run periodic order reconciliation."""
        # Allow disabling reconciliation without code changes via environment variable
        if os.getenv("ENABLE_RECONCILIATION", "true").lower() not in (
            "1",
            "true",
            "yes",
        ):
            # Reconciliation disabled — skip quietly to avoid log spam
            logger.debug(
                "Order reconciliation is disabled via ENABLE_RECONCILIATION env var"
            )
            return

        reconcile_interval = int(os.getenv("RECONCILE_INTERVAL_SECONDS", "300"))
        current_time = time.time()

        if current_time - self.last_reconciliation_time > reconcile_interval:
            if self.live_order_manager:
                try:
                    logger.info("Running periodic order reconciliation...")
                    report = await self.live_order_manager.reconcile_orders()
                    # Log summary of report
                    verified = report.get("verified_count", 0)
                    mismatches = len(report.get("status_mismatches", []))
                    missing = len(report.get("missing_on_exchange", []))
                    logger.info(
                        f"Reconciliation complete: {verified} verified, {mismatches} mismatches, {missing} missing"
                    )
                except Exception as e:
                    logger.error(f"Reconciliation failed: {e}")

            self.last_reconciliation_time = current_time

    async def _reconcile_positions_with_exchange(self) -> None:
        """
        Reconcile persisted positions against actual exchange state.
        Startup-only operation. Errors do not prevent bot startup.
        """
        if not self.live_exchange:
            return

        live_positions = {
            sym: pos
            for sym, pos in self.positions.items()
            if sym in self.strategy_configs
            and self.strategy_configs[sym].mode == "live"
        }

        if not live_positions:
            logger.info("No live positions to reconcile")
            return

        logger.info(
            f"Reconciling {len(live_positions)} live positions with exchange..."
        )
        ghost_positions = []

        for symbol, position in live_positions.items():
            try:
                exchange_amount = await self.live_exchange.get_position_amount(symbol)

                if exchange_amount is None:
                    logger.warning(
                        f"[{symbol}] RECONCILE: Could not query exchange. Keeping persisted position."
                    )
                    continue

                persisted_amount = position.current_amount

                if exchange_amount == Decimal("0") and persisted_amount > Decimal("0"):
                    logger.error(
                        f"[{symbol}] RECONCILE: GHOST POSITION! Persisted: {persisted_amount}, Exchange: 0"
                    )
                    ghost_positions.append(symbol)
                    await self.notifier.send(
                        f"Warning: GHOST POSITION: {symbol} has {persisted_amount} in state but 0 on exchange. Removed."
                    )
                elif abs(float(exchange_amount) - float(persisted_amount)) > 0.001:
                    logger.warning(
                        f"[{symbol}] RECONCILE: Amount mismatch! Persisted: {persisted_amount}, Exchange: {exchange_amount}. Using exchange value."
                    )
                    # Update position amount to match exchange
                    self.positions[symbol] = position.copy(
                        current_amount=exchange_amount
                    )
                else:
                    logger.info(f"[{symbol}] RECONCILE: OK")
            except Exception as e:
                logger.error(
                    f"[{symbol}] RECONCILE: Error: {e}. Keeping persisted position.",
                    exc_info=True,
                )

        for symbol in ghost_positions:
            del self.positions[symbol]

        if ghost_positions:
            self.state_manager.save_positions(self.positions)
            logger.info(
                f"Reconciliation complete. Removed {len(ghost_positions)} ghost positions."
            )
        else:
            logger.info("Reconciliation complete. All positions verified.")

    async def run(self) -> None:
        """
        Main bot loop.

        Responsibilities:
        1. Process incoming webhook signals
        2. Monitor positions for exit conditions
        3. Execute orders
        4. Update capital
        5. Persist state
        """
        await self.initialize()
        self.is_running = True
        logger.info("🤖 TradingBot is now running")

        # Send enhanced startup notification (EXACT from bot_v1 lines 2340-2398)
        try:
            # Calculate total capital across all symbols
            all_capitals = self.capital_manager.get_all_capitals()
            total_capital = sum(all_capitals.values())

            # Create startup timestamp
            startup_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            # Separate live and simulation symbols
            live_symbols = []
            sim_symbols = []

            for symbol, config in self.strategy_configs.items():
                if config.mode == "live":
                    live_symbols.append(symbol)
                else:
                    sim_symbols.append(symbol)

            # Build symbol status sections
            symbol_lines = []

            if live_symbols:
                symbol_lines.append("🔴 Live Trading:")
                for symbol in sorted(live_symbols):
                    symbol_lines.append(f"   • {symbol}")

            if sim_symbols:
                symbol_lines.append("🟢 Local Simulation:")
                for symbol in sorted(sim_symbols):
                    symbol_lines.append(f"   • {symbol}")

            symbol_status = "\n".join(symbol_lines)

            # Determine mode
            has_live = len(live_symbols) > 0
            has_sim = len(sim_symbols) > 0

            if has_live and has_sim:
                mode_text = "DUAL TRADING"
            elif has_live:
                mode_text = "LIVE TRADING"
            else:
                mode_text = "SIMULATION"

            startup_msg = (
                f"🚀 TRADING BOT ONLINE\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"⏰ Started: {startup_time}\n"
                f"💰 Capital: ${total_capital:.0f} USDT\n"
                f"🎯 Mode: {mode_text}\n\n"
                f"📊 Symbol Status:\n{symbol_status}\n\n"
                f"📈 **Active Positions:** {len(self.positions)}\n\n"
                f"🎯 Ready for signals\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )

            await self.notifier.send(startup_msg)
            logger.info("Startup notification sent successfully")
        except Exception as e:
            logger.error(f"Failed to send startup notification: {e}", exc_info=True)

        # Initialize heartbeat timing (EXACT from bot_v1 line 2401)
        self.last_heartbeat_time = time.time()  # Prevent immediate heartbeat

        try:
            while self.is_running:
                # 0. Evaluate Portfolio-wide Risk (Phase 5: Global Safety)
                if not await self.global_risk_manager.evaluate_portfolio_risk():
                    logger.critical(
                        f"🛑 HALTING BOT: {self.global_risk_manager.halt_reason}"
                    )
                    # Stop all grid orchestrators
                    for orch in self.grid_orchestrators.values():
                        if orch.is_active:
                            await orch.stop(
                                reason=f"Portfolio Halt: {self.global_risk_manager.halt_reason}"
                            )
                    self.is_running = False
                    break

                # Process pending signals
                await self._process_signals()

                # Monitor positions for exits
                await self._monitor_positions()

                # Tick Grid Orchestrators (Phase 3 Integration) - Parallelized
                await self._run_grid_orchestrators_tick()

                # Periodic order reconciliation (Phase 2: Optimization)
                await self._run_periodic_reconciliation()

                # Send periodic heartbeat (EXACT from bot_v1 lines 2409-2411)
                current_time = time.time()
                if current_time - self.last_heartbeat_time > HEARTBEAT_INTERVAL_SECONDS:
                    await self._send_heartbeat()
                    self.last_heartbeat_time = current_time

                # Persist state
                await self._persist_state()

                # Periodic Maintenance (Pruning every 6 hours)
                if (
                    current_time - self.last_prune_time
                    > self.order_state_prune_interval_sec
                ):
                    logger.info("Running periodic order state pruning...")
                    # Run in background to avoid blocking heartbeat
                    asyncio.create_task(self.order_state_manager.prune_archive())
                    self.last_prune_time = current_time

                # Sleep to avoid busy loop (adaptive when idle)
                idle_sleep = float(os.getenv("BOTV2_IDLE_SLEEP_SECS", "1.0"))
                if not self.positions and self.signal_queue.empty():
                    # Slightly longer sleep when idle if configured
                    idle_sleep = max(
                        idle_sleep,
                        float(os.getenv("BOTV2_IDLE_SLEEP_IDLE_SECS", "1.5")),
                    )
                await asyncio.sleep(idle_sleep)

        except Exception as e:
            logger.error(f"Error in main bot loop: {e}", exc_info=True)
            raise
        finally:
            self.is_running = False
            logger.info("🛑 TradingBot stopped")

    async def shutdown(self) -> None:
        """Gracefully shutdown the TradingBot and its resources.

        This closes any open exchange connections and performs async cleanup
        that can't safely be done in the main `run()` finally block because
        shutdown may be initiated from outside the bot's task.
        """
        logger.info(
            "Initiating TradingBot graceful shutdown: closing exchanges and cleaning up resources."
        )
        # mark not running so main loop will exit if it's still running
        self.is_running = False
        try:
            stop_tasks = [
                orchestrator.stop(reason="TradingBot shutdown")
                for orchestrator in self.grid_orchestrators.values()
                if getattr(orchestrator, "is_active", False)
            ]
            if stop_tasks:
                await asyncio.gather(*stop_tasks, return_exceptions=True)

            if self.live_exchange:
                try:
                    await self.live_exchange.close()
                    logger.info("Live exchange closed successfully during shutdown.")
                except Exception as e:
                    logger.warning(
                        f"Exception while closing live exchange during shutdown: {e}"
                    )
            if self.sim_exchange:
                try:
                    await self.sim_exchange.close()
                    logger.info(
                        "Simulated exchange closed successfully during shutdown."
                    )
                except Exception as e:
                    logger.warning(
                        f"Exception while closing simulated exchange during shutdown: {e}"
                    )
        except Exception as e:
            logger.error(f"Error during TradingBot.shutdown(): {e}", exc_info=True)

    async def handle_webhook_signal(self, signal: Dict[str, Any]) -> None:
        """
        Handle incoming webhook signal.

        Args:
            signal: Signal dict with 'action', 'symbol', and optional 'metadata'
        """
        action = signal.get("action", "").lower()
        symbol = signal.get("symbol")

        # Validate symbol if it's a trade action
        if action in ["buy", "sell", "exit"] and symbol:
            try:
                # Use internal normalization to check if symbol exists
                self._normalize_symbol(symbol)
            except ValueError as e:
                logger.warning(f"Rejecting signal for unknown symbol: {symbol}")
                raise e

        logger.info(f"📥 Received signal: {action.upper()} {symbol}")

        # Queue the signal for processing
        await self.signal_queue.put(signal)

    # SignalProcessor integration
    def _init_signal_processor(self):
        from bot_v2.signals.signal_processor import SignalProcessor

        self.signal_processor = SignalProcessor(
            signal_queue=self.signal_queue,
            logger=logger,
            positions=self.positions,
            notifier=self.notifier,
            strategy_configs=self.strategy_configs,
            volatility_filter=self.volatility_filter,
            cost_filter=self.cost_filter,
            capital_manager=self.capital_manager,
            risk_manager=self.risk_manager,
            get_order_manager_for_symbol=self._get_order_manager_for_symbol,
            normalize_symbol=self._normalize_symbol,
            handle_entry_signal_callback=self._handle_entry_signal,
            handle_exit_signal_callback=self._handle_exit_signal,
        )

    async def _process_signals(self) -> None:
        """Process all pending signals in the queue with bounded concurrency."""
        tasks = []
        while not self.signal_queue.empty():
            try:
                # Drain the queue
                signal = self.signal_queue.get_nowait()

                # Dedup (Phase 3: Burst Deduplication)
                symbol = signal.get("symbol")
                action = signal.get("action")
                if symbol and action:
                    # Normalize symbol for dedup key
                    norm_symbol = self._normalize_symbol(symbol)
                    key = (norm_symbol, action)
                    last_time = self._recent_signals.get(key, 0)
                    if time.time() - last_time < self._dedup_window:
                        logger.info(
                            f"Dropping duplicate signal {key} (within {self._dedup_window}s)"
                        )
                        continue
                    self._recent_signals[key] = time.time()

                # Schedule task with semaphore
                task = asyncio.create_task(self._process_signal_with_semaphore(signal))
                tasks.append(task)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"Error draining signal queue: {e}", exc_info=True)

        if tasks:
            # Wait for all tasks to complete
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_signal_with_semaphore(self, signal: Dict[str, Any]) -> None:
        """Wrapper to acquire semaphore before processing signal."""
        async with self._signal_semaphore:
            await self._process_single_signal(signal)

    async def _process_single_signal(self, signal: Dict[str, Any]) -> None:
        """
        Process a single trading signal with per-symbol locking.

        Args:
            signal: Signal dict with 'action', 'symbol', and optional 'metadata'
        """
        # Initialize performance tracking FIRST (Phase 1: Measurement)
        signal_id = f"signal_{int(time.time() * 1000)}"
        tracker = LatencyTracker(signal_id) if ENABLE_LATENCY_TRACKING else None

        if tracker:
            tracker.checkpoint("dequeued_from_queue")

        action = signal.get("action", "").lower()
        symbol = signal.get("symbol")
        metadata = signal.get(
            "metadata"
        )  # Extract metadata for xATR, quality metrics, etc.

        # Normalize symbol format (handle both BTCUSDT and BTC/USDT)
        symbol = self._normalize_symbol(symbol)

        # Update signal_id with symbol info
        if tracker:
            tracker.signal_id = f"{symbol}_{action}_{int(time.time())}"

        if tracker:
            tracker.checkpoint("signal_start")

        # Ensure lock exists for symbol
        lock = self._symbol_locks.setdefault(symbol, asyncio.Lock())

        async with lock:
            # Profile signal processing if enabled
            profiler_context = profile_signal_processing(
                signal_id, enabled=ENABLE_PERFORMANCE_PROFILING
            )

            with profiler_context:
                if action == "buy":
                    await self._handle_entry_signal(
                        symbol, PositionSide.LONG, metadata, tracker
                    )
                elif action == "sell":
                    await self._handle_entry_signal(
                        symbol, PositionSide.SHORT, metadata, tracker
                    )
                elif action == "exit":
                    await self._handle_exit_signal(symbol, tracker)
                else:
                    logger.warning(f"Unknown action: {action}")

                if tracker:
                    tracker.checkpoint("signal_complete")
                    # Log latency report
                    perf_details = (
                        os.getenv("LOG_PERF_DETAILS", "true").lower() == "true"
                    )
                    if perf_details:
                        logger.info(f"[PERF] {tracker.report(detailed=True)}")
                        # Log metrics for monitoring
                        metrics = tracker.get_metrics()
                        logger.info(f"[METRICS] {signal_id}: {metrics}")
                    else:
                        # Log summary only
                        total_ms = tracker.get_total_elapsed()
                        price_ms = (
                            tracker.get_delta("before_price_fetch", "after_price_fetch")
                            or 0
                        )
                        ohlcv_ms = (
                            tracker.get_delta("after_price_fetch", "after_ohlcv_fetch")
                            or 0
                        )
                        order_ms = (
                            tracker.get_delta(
                                "before_order_execution", "after_order_execution"
                            )
                            or 0
                        )

                        logger.info(
                            f"[PERF] {signal_id}: total={total_ms:.1f}ms, "
                            f"price={price_ms:.1f}ms, ohlcv={ohlcv_ms:.1f}ms, order={order_ms:.1f}ms"
                        )

    async def _handle_entry_signal(
        self,
        symbol: str,
        side: PositionSide,
        metadata: Optional[Dict[str, Any]] = None,
        tracker: Optional[LatencyTracker] = None,
    ) -> None:
        """
        Handle entry signal - EXACTLY matches original bot flow.

        Args:
            symbol: Trading symbol
            side: Position side (LONG or SHORT)
            metadata: Optional signal metadata (xatr_stop, etc.)
            tracker: Optional latency tracker for performance measurement
        """
        if tracker:
            tracker.checkpoint("entry_signal_start")

        # Check if already in position
        existing_position = self.positions.get(symbol)
        if existing_position:
            # Check if flip signal (opposite side)
            if existing_position.side != side:
                logger.info(
                    f"[{symbol}] FLIP SIGNAL detected: {existing_position.side.value} → {side.value}"
                )

                # CRITICAL: Force close with market order (don't rely on position query)
                await self._force_close_position(existing_position, "FLIP_SIGNAL")

                # Brief cooldown before opening new position (EXACT from bot_v1)
                await asyncio.sleep(2)

                logger.info(f"[{symbol}] Opening new {side.value} position after flip")
                # Continue to entry logic below
            else:
                logger.info(
                    f"[{symbol}] Already in {side.value} position, ignoring duplicate signal"
                )
                return

        if tracker:
            tracker.checkpoint("after_position_check")

        # Get symbol-specific config and exchange
        config = self._get_config(symbol)
        exchange = self._get_exchange(symbol)

        if tracker:
            tracker.checkpoint("before_price_fetch")

        # Parallel fetch of Price and OHLCV (Phase 2: Optimization)
        price_task = asyncio.create_task(exchange.get_market_price(symbol))
        ohlcv_task = asyncio.create_task(
            exchange.fetch_ohlcv(symbol, config.timeframe, config.atr_period * 2)
        )

        price, ohlcv_df = await asyncio.gather(price_task, ohlcv_task)

        if tracker:
            tracker.checkpoint(
                "after_price_fetch"
            )  # Keeping original checkpoint name for compatibility
            tracker.checkpoint("after_ohlcv_fetch")

        if not price or price <= Decimal("0"):
            logger.error(
                f"[{symbol}] Aborting trade: Invalid price of '{price}' received"
            )
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        if ohlcv_df is None or ohlcv_df.empty:
            logger.error(f"[{symbol}] Failed to fetch OHLCV data")
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        # Calculate ATR from OHLCV
        from ta.volatility import AverageTrueRange

        atr_indicator = AverageTrueRange(
            high=ohlcv_df["high"],
            low=ohlcv_df["low"],
            close=ohlcv_df["close"],
            window=config.atr_period,
        )
        entry_atr = Decimal(str(atr_indicator.average_true_range().iloc[-1]))

        if not entry_atr or entry_atr <= Decimal("0"):
            logger.error(
                f"[{symbol}] Aborting trade: Valid ATR could not be calculated ({entry_atr})"
            )
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        if tracker:
            tracker.checkpoint("after_atr_calc")

        # Apply volatility filter (EXACT from bot_v1)
        # if not await self.volatility_filter.is_volatile_enough(symbol, config, price, entry_atr, exchange):
        #    logger.warning(f"[{symbol}] Trade rejected: Volatility filter")
        #    await self._send_status_to_generator(symbol, "REJECTED")
        #    return

        # Apply cost floor filter (EXACT from bot_v1)
        if not self.cost_filter.is_cost_floor_met(config, price, entry_atr):
            logger.warning(f"[{symbol}] Trade rejected: Cost floor filter")
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        if tracker:
            tracker.checkpoint("after_filters")

        # Get capital and adaptive risk parameters
        capital = await self.capital_manager.get_capital(symbol)

        if capital <= Decimal("0"):
            logger.warning(f"[{symbol}] Trade REJECTED: Capital is $0 - trading halted")
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        # Calculate position parameters using adaptive risk management
        adaptive_params = await self.risk_manager.calculate_position_params(
            symbol=symbol,
            capital=capital,
            current_price=price,
            atr=entry_atr,
            active_positions=self._get_active_positions_dict(),
        )

        if tracker:
            tracker.checkpoint("after_risk_calc")

        if not adaptive_params["allowed"]:
            reason = adaptive_params.get("reason", "Unknown")
            if adaptive_params.get("kill_switch_active"):
                logger.error(
                    f"[{symbol}] KILL SWITCH ACTIVE - trading halted: {reason}"
                )
            else:
                logger.warning(f"[{symbol}] Adaptive risk BLOCKED trade: {reason}")
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        # Use adaptive risk allocation and leverage (tier-based)
        tier_name = adaptive_params["tier"]
        capital_allocation_pct = Decimal(str(adaptive_params["capital_allocation_pct"]))
        leverage = Decimal(str(adaptive_params["leverage"]))
        tier_max_leverage = adaptive_params.get("tier_max_leverage", leverage)
        kelly_reason = adaptive_params.get("kelly_reason", "")

        # Check if tier changed during this evaluation and sync to capital_manager
        tier_changed = adaptive_params.get("tier_changed", False)
        old_tier = adaptive_params.get("old_tier")
        if tier_changed and old_tier:
            # Sync the tier change to capital_manager immediately
            await self.capital_manager.set_tier(symbol, tier_name)
            logger.info(
                f"✅ [{symbol}] Tier synced to capital_manager: {old_tier} → {tier_name}"
            )

        # Enhanced logging showing both Kelly and tier max leverage
        leverage_info = f"{leverage}x leverage (Kelly, max {tier_max_leverage}x)"
        logger.info(
            f"[{symbol}] Adaptive Risk: {tier_name} tier - "
            f"{capital_allocation_pct:.1f}% allocation @ {leverage_info}"
        )
        if kelly_reason:
            logger.info(f"[{symbol}] {kelly_reason}")

        # Calculate position size using adaptive risk parameters
        notional = capital * (capital_allocation_pct / Decimal("100")) * leverage
        # Second trade leverage override (raise to tier max if qualified)
        try:
            # Load dedicated feature config (new location)
            import json as _json
            from pathlib import Path as _Path

            feature_path = _Path("config") / "second_trade_override.feature.json"
            feature_cfg = {}
            if feature_path.exists():
                try:
                    with open(feature_path) as _f:
                        feature_cfg = _json.load(_f)
                    logger.debug(
                        f"[{symbol}] loaded_feature_cfg path={feature_path} enabled={feature_cfg.get('enabled')} scope={feature_cfg.get('scope')}"
                    )
                except Exception as _e:
                    logger.error(
                        f"[{symbol}] failed_loading_feature_cfg path={feature_path} error={_e}"
                    )
            else:
                logger.debug(f"[{symbol}] feature_cfg_missing path={feature_path}")
            # Apply override via CapitalManager
            leverage = self.capital_manager.apply_second_trade_override(
                symbol=symbol.replace("/", ""),  # Consistent symbol format
                leverage=leverage,
                tier_max_leverage=Decimal(str(tier_max_leverage)),
                state_manager=self.state_manager,
                feature_cfg=feature_cfg,
            )
            logger.debug(f"[{symbol}] post_override_leverage {leverage}x")
            # Recompute notional if leverage changed
            notional = capital * (capital_allocation_pct / Decimal("100")) * leverage
        except Exception as e:
            logger.error(
                f"[{symbol}] Failed applying second trade leverage override: {e}"
            )

        # Check minimum notional requirement (EXACT from bot_v1)
        min_notional = Decimal(str(os.getenv("BOT_MIN_NOTIONAL_USD", "5.0")))
        if notional < min_notional:
            logger.warning(
                f"[{symbol}] Aborting trade: Notional ({notional:.2f} USD) < minimum ({min_notional} USD)"
            )
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        order_amount = notional / price

        preview_amount = self._preview_normalized_order_amount(
            exchange=exchange,
            symbol=symbol,
            requested_amount=order_amount,
        )
        if preview_amount <= Decimal("0"):
            step_size = self._get_exchange_step_size(exchange, symbol)
            min_required_notional = (
                step_size * price if step_size is not None else Decimal("0")
            )
            min_required_notional_str = (
                f"{min_required_notional:.2f}"
                if min_required_notional > Decimal("0")
                else "unknown"
            )
            logger.warning(
                f"[{symbol}] Aborting trade: Requested amount {order_amount} "
                f"normalizes to 0 at exchange lot/precision limits. "
                f"Approx minimum notional required: {min_required_notional_str} USDT"
            )
            await self._send_status_to_generator(symbol, "REJECTED")
            return

        # Set leverage for live exchanges (EXACT from bot_v1)
        if isinstance(exchange, LiveExchange):
            try:
                leverage_val = int(leverage)
                # Check cache to avoid redundant calls (Phase 2: Optimization)
                cached_leverage = self._leverage_cache.get(symbol)

                if cached_leverage != leverage_val:
                    logger.info(
                        f"[{symbol}] Setting leverage to {leverage_val}x (cached={cached_leverage})"
                    )
                    await exchange.exchange.set_leverage(leverage_val, symbol)
                    self._leverage_cache[symbol] = leverage_val
                else:
                    logger.debug(
                        f"[{symbol}] Leverage already set to {leverage_val}x (cached)"
                    )
            except Exception as e:
                logger.error(f"[{symbol}] Failed to set leverage: {e}")

        # Calculate stop loss and take profit levels
        # First get xATR from metadata if available (EXACT from bot_v1)
        xatr_stop = None
        if metadata and isinstance(metadata, dict):
            xatr_val = metadata.get("xatr_stop")
            if xatr_val is not None:
                try:
                    xatr_stop = Decimal(str(xatr_val))
                except (ValueError, TypeError):
                    xatr_stop = None

        # Calculate initial stop levels (will be refined with xATR if available)
        if side == PositionSide.LONG:
            tp1a_price = price + (
                entry_atr * config.tp1a_atr_mult
            )  # Quick scalp target
            tp1_price = price + (entry_atr * config.tp1_atr_mult)  # Main target
        else:  # SHORT
            tp1a_price = price - (
                entry_atr * config.tp1a_atr_mult
            )  # Quick scalp target
            tp1_price = price - (entry_atr * config.tp1_atr_mult)  # Main target

        # Execute entry order through OrderManager to capture metadata
        trade_side = TradeSide.BUY if side == PositionSide.LONG else TradeSide.SELL
        order_manager = self._get_order_manager_for_symbol(symbol)

        if tracker:
            tracker.checkpoint("before_order_execution")

        try:
            order = await order_manager.create_market_order(
                symbol_id=symbol,
                side=trade_side,
                amount=order_amount,
                config=config,
                current_price=price,
            )

            if tracker:
                tracker.checkpoint("after_order_execution")

            if not order or not order.get("average") or not order.get("filled"):
                logger.error(
                    f"[{symbol}] Entry order failed or returned invalid structure"
                )
                await self._send_status_to_generator(symbol, "REJECTED")
                return

            # Extract order details (matches original bot)
            avg_price = Decimal(str(order.get("average")))
            filled_amount = Decimal(str(order.get("filled")))
            fee_cost = Decimal(str((order.get("fee") or {}).get("cost", "0")))

            if filled_amount <= Decimal("0"):
                logger.error(f"[{symbol}] Entry order filled with 0 amount")
                await self._send_status_to_generator(symbol, "REJECTED")
                return

            # Calculate soft SL with hybrid xATR approach (EXACT from bot_v1 lines 3085-3160)
            if xatr_stop is not None:
                # Calculate desired stop distance from config
                desired_distance = entry_atr * config.soft_sl_atr_mult

                # Calculate how far xATR already is from entry
                xatr_distance_from_entry = abs(avg_price - xatr_stop)

                # Buffer should fill the gap to reach desired distance
                buffer_needed = desired_distance - xatr_distance_from_entry

                # Minimum buffer of 0.5 ATR for breathing room
                buffer = max(buffer_needed, entry_atr * Decimal("0.5"))

                if trade_side == TradeSide.BUY:
                    soft_sl_price = xatr_stop - buffer
                else:  # SELL
                    soft_sl_price = xatr_stop + buffer

                # Verify stop is in correct direction
                stop_in_wrong_direction = False
                if trade_side == TradeSide.BUY and soft_sl_price >= avg_price:
                    logger.warning(
                        f"[{symbol}] xATR stop ({soft_sl_price:.4f}) above entry for LONG. Using ATR mult."
                    )
                    stop_in_wrong_direction = True
                elif trade_side == TradeSide.SELL and soft_sl_price <= avg_price:
                    logger.warning(
                        f"[{symbol}] xATR stop ({soft_sl_price:.4f}) below entry for SHORT. Using ATR mult."
                    )
                    stop_in_wrong_direction = True

                if not stop_in_wrong_direction:
                    # Apply 1-6% bounds for safety
                    min_stop_distance = avg_price * Decimal("0.01")
                    max_stop_distance = avg_price * Decimal("0.06")

                    actual_distance = abs(avg_price - soft_sl_price)
                    if actual_distance < min_stop_distance:
                        soft_sl_price = (
                            avg_price - min_stop_distance
                            if trade_side == TradeSide.BUY
                            else avg_price + min_stop_distance
                        )
                    elif actual_distance > max_stop_distance:
                        soft_sl_price = (
                            avg_price - max_stop_distance
                            if trade_side == TradeSide.BUY
                            else avg_price + max_stop_distance
                        )

                    logger.info(
                        f"[{symbol}] Using hybrid xATR stop: xATR={xatr_stop:.4f}, buffer={buffer:.4f}, final={soft_sl_price:.4f}"
                    )
                else:
                    # Fallback to ATR multiple
                    sl_dist = entry_atr * config.soft_sl_atr_mult
                    soft_sl_price = (
                        avg_price - sl_dist
                        if trade_side == TradeSide.BUY
                        else avg_price + sl_dist
                    )
                    logger.info(
                        f"[{symbol}] Fallback to ATR multiple stop: {config.soft_sl_atr_mult}x ATR"
                    )
            else:
                # No xATR available, use ATR multiple
                sl_dist = entry_atr * config.soft_sl_atr_mult
                soft_sl_price = (
                    avg_price - sl_dist
                    if trade_side == TradeSide.BUY
                    else avg_price + sl_dist
                )
                logger.info(
                    f"[{symbol}] Using ATR multiple stop: {config.soft_sl_atr_mult}x ATR"
                )

            # Calculate hard stop
            if side == PositionSide.LONG:
                hard_sl_price = avg_price - (entry_atr * config.hard_sl_atr_mult)
            else:
                hard_sl_price = avg_price + (entry_atr * config.hard_sl_atr_mult)

            # Create position object
            entry_time = datetime.now(timezone.utc)
            position_id = f"{symbol}_{int(entry_time.timestamp())}"
            entry_order_id = str(order.get("id", "unknown"))

            position = Position(
                symbol_id=symbol,
                side=side,
                entry_price=avg_price,
                entry_time=entry_time,
                initial_amount=filled_amount,
                entry_atr=entry_atr,
                initial_risk_atr=entry_atr,
                total_entry_fee=fee_cost,
                position_id=position_id,
                entry_order_id=entry_order_id,
                soft_sl_price=soft_sl_price,
                hard_sl_price=hard_sl_price,
                tp1a_price=tp1a_price,  # Add quick scalp target
                tp1_price=tp1_price,
                leverage=leverage,
                tier_name=tier_name,
                capital_allocation_pct=capital_allocation_pct,
            )

            # Store position
            self.positions[symbol] = position
            logger.info(
                f"✅ [{symbol}] Entered {side.value} position @ {avg_price:.2f}, "
                f"amount: {filled_amount:.4f}, SL: {soft_sl_price:.2f}, TP: {tp1_price:.2f}"
            )

            # Notify signal generator of entry - use POSITION_ENTERED (not ENTRY) to match signal generator expectations
            await self._send_status_to_generator(
                symbol,
                "POSITION_ENTERED",
                extra_payload={
                    "position_id": position_id,
                    "entry_order_id": entry_order_id,
                    "side": side.value if hasattr(side, "value") else str(side),
                    "entry_price": float(avg_price),
                    "entry_atr": float(entry_atr),
                },
            )

            # Send Telegram notification with tier information
            await self._send_entry_notification(
                symbol,
                side,
                avg_price,
                filled_amount,
                notional,
                capital,
                soft_sl_price,
                tp1a_price,
                tp1_price,
                entry_atr,
                tier_name,
                capital_allocation_pct,
                leverage,
                config,
            )

        except Exception as e:
            logger.error(f"[{symbol}] Error executing entry order: {e}", exc_info=True)
            await self._send_status_to_generator(symbol, "REJECTED")

    async def _handle_exit_signal(
        self, symbol: str, tracker: Optional[LatencyTracker] = None
    ) -> None:
        """
        Handle exit signal.

        Args:
            symbol: Trading symbol
            tracker: Optional latency tracker for performance measurement
        """
        if tracker:
            tracker.checkpoint("exit_signal_start")

        position = self.positions.get(symbol)
        if not position:
            logger.info(f"No position to exit for {symbol}")
            return

        await self._exit_position(position, "MANUAL_EXIT")

        if tracker:
            tracker.checkpoint("exit_signal_complete")

    async def _monitor_positions(self) -> None:
        """Monitor all positions for exit conditions (EXACT logic from bot_v1)."""
        positions = list(self.positions.values())

        if not positions:
            return

        # Fetch all prices and ATRs in parallel for current positions
        import asyncio as _asyncio

        async def _fetch_symbol_data(pos: Position):
            price_task = _asyncio.create_task(self._get_current_price(pos.symbol_id))
            atr_task = _asyncio.create_task(self._get_current_atr(pos.symbol_id))
            price, atr = await _asyncio.gather(
                price_task, atr_task, return_exceptions=False
            )
            return pos.symbol_id, price, atr

        results = await _asyncio.gather(
            *[_fetch_symbol_data(p) for p in positions], return_exceptions=False
        )

        # Cache a single current_bar_ts for this monitoring tick
        current_bar_ts = int(time.time() * 1000)

        for position in positions:
            # Extract fetched data
            sym_row = next((r for r in results if r[0] == position.symbol_id), None)
            if not sym_row:
                logger.warning(
                    f"[{position.symbol_id}] Missing fetch results; skipping"
                )
                continue
            _, current_price, current_atr = sym_row

            if (
                current_price is None
                or current_price <= Decimal("0")
                or current_atr is None
                or current_atr <= Decimal("0")
            ):
                logger.warning(
                    f"[{position.symbol_id}] Skipping monitoring - invalid price or ATR"
                )
                continue

            # Update position metrics using tracker (returns updated position)
            # Get symbol-specific config for timeframe
            symbol_config = self._get_config(position.symbol_id)
            # Prefer passing timeframe for accurate bars_held; fall back to 2-arg call for test mocks
            try:
                updated_position = self.position_tracker.update_all_metrics(
                    position, current_price, symbol_config.timeframe
                )
            except TypeError:
                updated_position = self.position_tracker.update_all_metrics(
                    position, current_price
                )
            self.positions[position.symbol_id] = updated_position

            # CRITICAL: Trailing stop activation (from bot_v1 lines 3797-3808)
            # Get symbol-specific config
            symbol_config = self._get_config(position.symbol_id)
            # Be defensive in tests where config may be a MagicMock
            try:
                trailing_start_r = Decimal(str(symbol_config.trailing_start_r))
            except Exception:
                trailing_start_r = Decimal(
                    "9999"
                )  # effectively disables activation in such tests

            if not updated_position.is_trailing_active and (
                updated_position.current_r >= trailing_start_r
                or updated_position.tp1a_hit
            ):
                # Activate trailing
                updated_position = updated_position.copy(is_trailing_active=True)

                # Calculate initial trailing stop price
                trail_distance = current_atr * symbol_config.trail_sl_atr_mult
                if updated_position.side == PositionSide.LONG:
                    trail_price = (
                        updated_position.peak_price_since_entry - trail_distance
                    )
                else:
                    trail_price = (
                        updated_position.peak_price_since_entry + trail_distance
                    )

                updated_position = updated_position.copy(trailing_sl_price=trail_price)
                self.positions[position.symbol_id] = updated_position

                logger.info(
                    f"[{position.symbol_id}] TRAILING STOP activated at "
                    f"{updated_position.current_r:.2f}R. Initial trail: {trail_price:.4f}"
                )

            # Update trailing stop if active (using TrailingStopCalculator)
            if updated_position.is_trailing_active:
                # Create config for trailing calculator
                from bot_v2.position.trailing_stop import TrailingStopConfig

                trail_config = TrailingStopConfig(
                    trail_sl_atr_mult=symbol_config.trail_sl_atr_mult,
                    trailing_start_r=symbol_config.trailing_start_r,
                )

                new_trail = self.trailing_calculator.calculate_trailing_stop(
                    position=updated_position,
                    config=trail_config,
                    current_atr=current_atr,
                    current_price=current_price,
                )
                # Only log significant trailing stop changes (>0.1% move)
                if new_trail and hasattr(new_trail, "stop_price"):
                    old_trail = updated_position.trailing_sl_price
                    new_trail_price = new_trail.stop_price
                    if old_trail is None or abs(
                        (new_trail_price - old_trail) / old_trail
                    ) > Decimal("0.001"):
                        logger.info(
                            f"[{position.symbol_id}] Trailing stop updated: {old_trail} -> {new_trail_price}"
                        )
                    updated_position = updated_position.copy(
                        trailing_sl_price=new_trail_price
                    )
                    self.positions[position.symbol_id] = updated_position

            # Create exit engine for this specific check (stateless per-evaluation pattern)
            # Get symbol-specific config for exit engine
            symbol_config = self._get_config(position.symbol_id)
            # Convert Position (dataclass) to PositionState for old bot exit engine
            pos_v1 = _convert_position_to_v1(updated_position)
            exit_engine = ExitConditionEngine(
                position=pos_v1,
                strategy=symbol_config,
                current_price=current_price,
                current_atr=current_atr,
                current_bar_ts=current_bar_ts,
            )

            # Check exit conditions
            exit_result = exit_engine.evaluate_all_exits()

            if exit_result:
                # Handle partial close (TP1a, AdverseScaleOut) vs full close
                # Partial close if amount < current amount (regardless of reason)
                if exit_result.amount < updated_position.current_amount:
                    await self._partial_close_position(updated_position, exit_result)
                    # CRITICAL: Skip further checks for this position in this cycle
                    # The updated position state (with tp1a_hit=True, etc.) will be used in the next cycle
                    # This prevents repeated partial closes within a single monitoring iteration
                    continue
                else:
                    await self._exit_position(updated_position, exit_result.name)

    async def _get_current_price(self, symbol: str) -> Decimal:
        """
        Get current market price for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current price
        """
        try:
            # Get the correct exchange for this symbol
            exchange = self._get_exchange(symbol)

            # Use exchange's get_market_price method (works for both real and simulated)
            # Optional tiny TTL cache to avoid duplicate requests within the same tick
            ttl_ms = int(os.getenv("BOTV2_PRICE_TTL_MS", "0"))
            if ttl_ms > 0:
                cached = self._price_cache.get(symbol)
                now_ms = time.time() * 1000
                if cached and (now_ms - cached.get("ts", 0)) <= ttl_ms:
                    return cached["price"]

            price = await exchange.get_market_price(symbol)
            if price is None or price <= Decimal("0"):
                logger.error(f"[{symbol}] Failed to fetch price")
                return Decimal("0")
            # store in cache if enabled
            if ttl_ms > 0:
                self._price_cache[symbol] = {"price": price, "ts": time.time() * 1000}
            return price
        except Exception as e:
            logger.error(f"[{symbol}] Error fetching current price: {e}")
            return Decimal("0")

    async def _get_current_atr(self, symbol: str) -> Decimal:
        """
        Get current ATR for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Current ATR value
        """
        try:
            # Get symbol-specific config and exchange
            config = self._get_config(symbol)
            exchange = self._get_exchange(symbol)

            # Fetch OHLCV data using exchange's fetch_ohlcv method (works for both real and simulated)
            ohlcv_df = await exchange.fetch_ohlcv(
                market_id=symbol,
                timeframe=config.timeframe,
                limit=config.atr_period + 10,
            )

            if ohlcv_df is None or len(ohlcv_df) < config.atr_period:
                logger.error(f"[{symbol}] Insufficient OHLCV data for ATR")
                return Decimal("0")
            # ATR cache check using last bar timestamp
            cache_key = (symbol, config.timeframe)
            try:
                last_bar_ts = (
                    int(ohlcv_df["timestamp"].iloc[-1])
                    if "timestamp" in ohlcv_df.columns
                    else None
                )
            except Exception:
                last_bar_ts = None

            cached = self._atr_cache.get(cache_key)
            if (
                cached
                and last_bar_ts is not None
                and cached.get("last_bar_ts") == last_bar_ts
            ):
                return cached.get("atr", Decimal("0"))

            # Calculate ATR using ta library (OHLCV already in DataFrame format from exchange)
            from ta.volatility import AverageTrueRange

            atr_indicator = AverageTrueRange(
                high=ohlcv_df["high"],
                low=ohlcv_df["low"],
                close=ohlcv_df["close"],
                window=config.atr_period,
            )
            current_atr = Decimal(str(atr_indicator.average_true_range().iloc[-1]))

            if not current_atr or current_atr <= Decimal("0"):
                logger.error(f"[{symbol}] Invalid ATR calculated: {current_atr}")
                return Decimal("0")
            # Update ATR cache
            if last_bar_ts is not None:
                self._atr_cache[cache_key] = {
                    "last_bar_ts": last_bar_ts,
                    "atr": current_atr,
                }
            return current_atr

        except Exception as e:
            logger.error(f"[{symbol}] Error calculating current ATR: {e}")
            return Decimal("0")

    async def _partial_close_position(self, position: Position, exit_result) -> None:
        """
        Handle partial position close (TP1a scalp or AdverseScaleOut) and apply
        post-close protections consistent with the legacy bot.

        Args:
            position: Position to partially close
            exit_result: ExitCondition with amount to close
        """
        try:
            current_price = await self._get_current_price(position.symbol_id)
            if current_price <= Decimal("0"):
                logger.error(
                    f"[{position.symbol_id}] Cannot partial close - invalid price"
                )
                return

            close_amount = exit_result.amount

            # Check minimum notional
            min_notional = Decimal(str(os.getenv("BOT_MIN_NOTIONAL_USD", "5.0")))
            notional = close_amount * current_price

            # If this is a TP1a event and we've already handled TP1a for this position,
            # quietly skip to avoid repeated attempts and logs (idempotency guard).
            exit_is_tp1a = (getattr(exit_result, "name", "") == "TP1a") or (
                isinstance(getattr(exit_result, "reason", None), str)
                and "TP1a" in exit_result.reason
            )
            if exit_is_tp1a and position.tp1a_hit:
                logger.debug(
                    f"[{position.symbol_id}] TP1a already handled (tp1a_hit=True); skipping duplicate partial close attempt"
                )
                return

            if notional < min_notional:
                # If the remaining post-close notional would be below the minimum, perform a full exit instead
                remaining_amount = position.current_amount - close_amount
                remaining_notional = remaining_amount * current_price
                if remaining_notional <= min_notional:
                    logger.info(
                        f"[{position.symbol_id}] Remaining position after requested partial ({remaining_notional:.2f} USD) <= min notional ({min_notional} USD); executing full exit instead"
                    )
                    await self._exit_position(
                        position, getattr(exit_result, "name", str(exit_result.reason))
                    )
                    return

                # Otherwise, mark TP1a handled (if applicable) and persist to prevent infinite retry spam
                # Convert the WARNING to INFO on first occurrence (we still want visibility)
                logger.info(
                    f"[{position.symbol_id}] Skipping partial close: Notional ({notional:.2f} USD) < minimum ({min_notional} USD) - marking as handled"
                )

                # FIX: Update state to prevent infinite loop if TP1a was triggered
                # Accept verbose reasons like 'TP1a scalp hit at 0.2791 (30%)'
                if exit_is_tp1a:
                    logger.info(
                        f"[{position.symbol_id}] Marking TP1a as hit despite skipping order (to prevent loop)"
                    )

                    # We need strategy config for BE calculation
                    strategy = self._get_config(position.symbol_id)

                    # Calculate BE level (same logic as success case)
                    buffer = position.entry_atr * strategy.breakeven_offset_atr
                    if position.side == PositionSide.LONG:
                        raw_soft_sl = position.entry_price + buffer
                        new_soft_sl = max(raw_soft_sl, position.entry_price)
                    else:
                        raw_soft_sl = position.entry_price - buffer
                        new_soft_sl = min(raw_soft_sl, position.entry_price)

                    updated_position = position.copy(
                        status=PositionStatus.PARTIALLY_CLOSED,
                        tp1a_hit=True,
                        time_of_tp1=(
                            datetime.now(timezone.utc)
                            if position.time_of_tp1 is None
                            else position.time_of_tp1
                        ),
                        post_tp1_probation_start=datetime.now(timezone.utc),
                        tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
                        peak_price_since_tp1=Decimal(str(current_price)),
                        soft_sl_price=new_soft_sl,
                    )
                    self.positions[position.symbol_id] = updated_position
                    self.state_manager.save_positions(self.positions)

                return

            # Get the correct order manager for this symbol (live or sim)
            order_manager = self._get_order_manager_for_symbol(position.symbol_id)
            config = self._get_config(position.symbol_id)

            # Execute partial close order through OrderManager to capture metadata
            # Use reduceOnly and explicit positionSide for hedged accounts
            order = await order_manager.create_market_order(
                symbol_id=position.symbol_id,
                side=(
                    TradeSide.SELL
                    if position.side == PositionSide.LONG
                    else TradeSide.BUY
                ),
                amount=close_amount,
                params={
                    "reduceOnly": True,
                    "positionSide": (
                        "LONG" if position.side == PositionSide.LONG else "SHORT"
                    ),
                },
                config=config,
                current_price=current_price,
            )

            if order:
                # Use ACTUAL filled amount from exchange instead of requested amount
                actual_filled = Decimal(str(order.get("filled", close_amount)))
                actual_avg_price = Decimal(str(order.get("average", current_price)))

                # Extract exit fee
                exit_fee = Decimal(str((order.get("fee") or {}).get("cost", "0")))

                # Calculate proportional entry fee
                entry_fee_share = Decimal("0")
                if position.initial_amount > Decimal("0"):
                    entry_fee_share = (
                        actual_filled / position.initial_amount
                    ) * position.total_entry_fee

                # Safety check: Ensure order actually filled
                if actual_filled <= Decimal("0"):
                    logger.error(
                        f"❌ [{position.symbol_id}] TP1 order returned zero fill! "
                        f"Order may have failed. Status: {order.get('status', 'unknown')}"
                    )
                    return

                # Log discrepancy if filled amount differs from requested
                if abs(actual_filled - close_amount) > Decimal("0.0001"):
                    logger.warning(
                        f"⚠️ [{position.symbol_id}] TP1 fill discrepancy: "
                        f"requested={close_amount}, filled={actual_filled}, "
                        f"diff={close_amount - actual_filled}"
                    )

                # Calculate partial Gross PnL using ACTUAL filled data
                if position.side == PositionSide.LONG:
                    gross_pnl = (
                        actual_avg_price - position.entry_price
                    ) * actual_filled
                else:
                    gross_pnl = (
                        position.entry_price - actual_avg_price
                    ) * actual_filled

                # Calculate Net PnL
                net_partial_pnl = gross_pnl - entry_fee_share - exit_fee

                # Calculate remaining amount using ACTUAL filled amount
                remaining_amount = position.current_amount - actual_filled

                logger.info(
                    f"✅ [{position.symbol_id}] TP1 partial close: "
                    f"filled={actual_filled} @ {actual_avg_price}, "
                    f"remaining={remaining_amount}, "
                    f"Gross PnL=${gross_pnl:.2f}, Net PnL=${net_partial_pnl:.2f} "
                    f"(Fees: Entry=${entry_fee_share:.2f}, Exit=${exit_fee:.2f})"
                )

                # Start with common updates
                updated_position = position.copy(
                    current_amount=remaining_amount,
                    realized_profit=position.realized_profit + net_partial_pnl,
                )

                # Fetch strategy and ATR for protections (per-symbol config)
                strategy = self._get_config(position.symbol_id)
                current_atr = await self._get_current_atr(position.symbol_id)
                if not current_atr or current_atr <= Decimal("0"):
                    current_atr = position.entry_atr

                # Apply reason-specific state transitions
                logger.info(f"[DEBUG] exit_result.reason = '{exit_result.reason}'")
                # Treat any verbose 'TP1a' reason as a TP1a event
                if isinstance(exit_result.reason, str) and "TP1a" in exit_result.reason:
                    # Mark TP1a and switch to partially closed state
                    # Enforce breakeven protection as in legacy bot
                    buffer = position.entry_atr * strategy.breakeven_offset_atr
                    if position.side == PositionSide.LONG:
                        raw_soft_sl = position.entry_price + buffer
                        new_soft_sl = max(raw_soft_sl, position.entry_price)
                    else:
                        raw_soft_sl = position.entry_price - buffer
                        new_soft_sl = min(raw_soft_sl, position.entry_price)

                    # Initialize post-TP1 tracking fields
                    updated_position = updated_position.copy(
                        status=PositionStatus.PARTIALLY_CLOSED,
                        tp1a_hit=True,
                        time_of_tp1=(
                            datetime.now(timezone.utc)
                            if position.time_of_tp1 is None
                            else position.time_of_tp1
                        ),
                        post_tp1_probation_start=datetime.now(timezone.utc),
                        tp1_ratio_reset_timestamp=datetime.now(timezone.utc),
                        peak_price_since_tp1=Decimal(str(current_price)),
                        soft_sl_price=new_soft_sl,
                    )

                elif exit_result.reason == "AdverseScaleOut":
                    # Mark scale-out and enable post-scaleout protections
                    # 1) Move soft SL to breakeven + offset
                    be_offset = strategy.scaleout_be_offset_atr
                    be_price = (
                        position.entry_price + (position.entry_atr * be_offset)
                        if position.side == PositionSide.LONG
                        else position.entry_price - (position.entry_atr * be_offset)
                    )
                    if position.side == PositionSide.LONG:
                        be_price = max(be_price, position.entry_price)
                    else:
                        be_price = min(be_price, position.entry_price)

                    # 2) Arm salvage trailing stop from execution price
                    trail_distance = current_atr * strategy.scaleout_trail_atr_mult
                    exec_price = Decimal(str(current_price))
                    if position.side == PositionSide.LONG:
                        trail_price = exec_price - trail_distance
                    else:
                        trail_price = exec_price + trail_distance

                    # 3) Temporarily suspend MAE-driven exits for N bars
                    suspend_until = None
                    if position.last_checked_bar_ts:
                        tf_map_ms = {
                            "1m": 60_000,
                            "3m": 180_000,
                            "5m": 300_000,
                            "15m": 900_000,
                            "30m": 1_800_000,
                            "1h": 3_600_000,
                            "2h": 7_200_000,
                            "4h": 14_400_000,
                            "1d": 86_400_000,
                        }
                        tf_ms = tf_map_ms.get(strategy.timeframe, 1_800_000)
                        suspend_until = position.last_checked_bar_ts + (
                            strategy.mae_suspend_bars_after_scaleout * tf_ms
                        )

                    updated_position = updated_position.copy(
                        scaled_out_on_adverse=True,
                        adverse_scaleout_timestamp=datetime.now(timezone.utc),
                        breakeven_level=be_price,
                        soft_sl_price=be_price,
                        is_trailing_active=True,
                        trailing_sl_price=trail_price,
                        mae_breach_counter=0,
                        scaleout_suspend_until_bar_ts=(
                            suspend_until
                            if suspend_until
                            else position.scaleout_suspend_until_bar_ts
                        ),
                    )

                # Save updated position
                self.positions[position.symbol_id] = updated_position
                logger.info(
                    f"[DEBUG] Saving position with tp1a_hit={updated_position.tp1a_hit}, status={updated_position.status.value}"
                )
                self.state_manager.save_positions(self.positions)

                # Update capital with partial close profit (CRITICAL FIX: Issue #24)
                # Using Net PNL to ensure accurate capital tracking
                await self.capital_manager.update_capital(
                    position.symbol_id, net_partial_pnl
                )

                # Update statistics
                self.total_pnl += net_partial_pnl
                if net_partial_pnl > 0:
                    self.winning_trades += 1

                logger.info(
                    f"✂️ Partial close ({exit_result.reason}): {position.symbol_id} | "
                    f"Closed: {close_amount:.4f} ({exit_result.amount / position.initial_amount * 100:.0f}%) | "
                    f"Remaining: {remaining_amount:.4f} | "
                    f"Net Partial PnL: ${net_partial_pnl:.2f} | "
                    f"Price: {current_price:.4f}"
                )

                # Send Telegram notification
                # await self.notifier.send_partial_close_notification(
                #     symbol=position.symbol_id,
                #     side=position.side.value,
                #     close_amount=close_amount,
                #     close_percent=float(
                #         exit_result.amount / position.initial_amount * 100
                #     ),
                #     remaining_amount=remaining_amount,
                #     partial_pnl=float(net_partial_pnl),
                #     price=float(current_price),
                #     reason=exit_result.reason,
                # )  # Filtered: partial close notifications disabled

        except Exception as e:
            logger.error(
                f"[{position.symbol_id}] Error in partial close: {e}", exc_info=True
            )

    async def _get_exchange_position(self, symbol_id: str) -> Optional[Decimal]:
        """
        Query exchange for actual open position amount.

        Args:
            symbol_id: Symbol identifier (e.g., "BTC/USDT")

        Returns:
            Actual position amount on exchange or None if query fails
        """
        try:
            # Get the correct exchange for this symbol (live or simulated)
            exchange = self._get_exchange_for_symbol(symbol_id)

            # Only query if exchange has position amount method (LiveExchange only)
            if hasattr(exchange, "get_position_amount"):
                market_id = exchange.format_market_id(symbol_id)
                if market_id:
                    return await exchange.get_position_amount(market_id)
            return None
        except Exception as e:
            logger.error(
                f"Failed to get exchange position for {symbol_id}: {e}", exc_info=True
            )
            return None

    async def _force_close_position(self, position: Position, reason: str) -> None:
        """
        Force close a position with a market order (for flip signals).
        Unlike _exit_position, this ALWAYS sends a close order regardless of position query.

        Args:
            position: Position to exit
            reason: Exit reason string
        """
        logger.info(
            f"🔄 [{position.symbol_id}] Force closing {position.side.value} position "
            f"(amount: {position.current_amount}) for {reason}"
        )

        # Get current price
        current_price = await self._get_current_price(position.symbol_id)

        try:
            # Get the correct order manager and config for this symbol (live or sim)
            order_manager = self._get_order_manager_for_symbol(position.symbol_id)
            config = self._get_config(position.symbol_id)

            # ALWAYS create and execute the exit order (no position query check)
            exit_side = (
                TradeSide.SELL if position.side == PositionSide.LONG else TradeSide.BUY
            )

            # Get current price for order
            current_price = await self._get_current_price(position.symbol_id)

            # Create market order to close position
            order = await order_manager.create_market_order(
                symbol_id=position.symbol_id,
                side=exit_side,
                amount=position.current_amount,
                params={
                    "reduceOnly": True,
                    "positionSide": (
                        "LONG" if position.side == PositionSide.LONG else "SHORT"
                    ),
                },
                config=config,
                current_price=current_price,
            )

            if order:
                # Use actual filled data from exchange
                actual_filled = Decimal(
                    str(order.get("filled", position.current_amount))
                )
                actual_avg_price = Decimal(str(order.get("average", current_price)))

                logger.info(
                    f"✅ [{position.symbol_id}] Force close executed: {actual_filled} @ {actual_avg_price}"
                )

                # Calculate realized PnL
                exit_price = actual_avg_price
                if position.side == PositionSide.LONG:
                    pnl = (exit_price - position.entry_price) * actual_filled
                else:
                    pnl = (position.entry_price - exit_price) * actual_filled

                total_pnl = pnl + position.realized_profit

                # === UPDATE PERFORMANCE METRICS BEFORE RECORDING TRADE ===
                # Update MFE using peak or tracked peak since entry if available
                try:
                    if position.side == PositionSide.LONG:
                        if position.peak_price_since_entry:
                            mfe_price = max(
                                position.peak_price_since_entry, position.entry_price
                            )
                            position.mfe = max(
                                position.mfe, mfe_price - position.entry_price
                            )
                        # MAE: if exit is adverse compared to entry
                        if exit_price < position.entry_price:
                            position.mae = max(
                                position.mae, position.entry_price - exit_price
                            )
                    else:
                        # SHORT: treat peak_price_since_entry as trough for favorable excursion
                        if position.peak_price_since_entry:
                            position.mfe = max(
                                position.mfe,
                                position.entry_price - position.peak_price_since_entry,
                            )
                        if exit_price > position.entry_price:
                            position.mae = max(
                                position.mae, exit_price - position.entry_price
                            )
                except Exception as e:
                    logger.debug(
                        f"[{position.symbol_id}] Error updating MFE/MAE before history: {e}"
                    )
                # Add to trade history
                self._add_trade_to_history(position, exit_price, total_pnl, reason)

                # Update performance and check tier transitions
                self._update_performance_metrics(position.symbol_id)
                await self._check_tier_transition(position.symbol_id)

                # Remove position from tracking
                if position.symbol_id in self.positions:
                    del self.positions[position.symbol_id]

                # Send notifications
                await self._send_status_to_generator(position.symbol_id, "CLOSED")
                await self._send_exit_notification(position, exit_price, pnl, reason)

            else:
                logger.error(
                    f"❌ [{position.symbol_id}] Force close order failed or not filled"
                )

        except Exception as e:
            logger.error(
                f"❌ [{position.symbol_id}] Force close failed: {e}", exc_info=True
            )

    async def _exit_position(self, position: Position, reason: str) -> None:
        """
        Exit a position.

        Args:
            position: Position to exit
            reason: Exit reason string
        """
        import asyncio
        import os

        from bot_v2.models.exceptions import OrderExecutionError

        # Parallel fetch of Price and Position Amount (Phase 2: Optimization)
        price_task = asyncio.create_task(self._get_current_price(position.symbol_id))
        pos_task = asyncio.create_task(self._get_exchange_position(position.symbol_id))

        current_price, exchange_amount = await asyncio.gather(price_task, pos_task)

        order_manager = self._get_order_manager_for_symbol(position.symbol_id)
        config = self._get_config(position.symbol_id)
        logger.info(
            f"[{position.symbol_id}] Exchange position query result: {exchange_amount} "
            f"(bot tracking: {position.current_amount})"
        )

        if exchange_amount is not None and exchange_amount > Decimal("0"):
            exit_amount = exchange_amount
            if abs(exit_amount - position.current_amount) > Decimal("0.0001"):
                logger.warning(
                    f"⚠️ [{position.symbol_id}] Position mismatch detected: "
                    f"bot_tracking={position.current_amount}, exchange={exchange_amount} - using exchange amount for exit"
                )
        else:
            exit_amount = position.current_amount
            if exchange_amount is not None and exchange_amount <= Decimal("0.0001"):
                logger.warning(
                    f"⚠️ [{position.symbol_id}] Exchange query returned 0, but sending exit order anyway (amount={exit_amount}) to prevent orphaned positions"
                )

        # Retry parameters
        max_retries = int(os.environ.get("EXIT_ORDER_MAX_RETRIES", 3))
        base_delay = 1.0
        backoff = 2.0
        attempt = 0
        last_exception = None
        order = None

        while attempt < max_retries:
            try:
                logger.info(
                    f"[{position.symbol_id}] Exit order attempt {attempt + 1}/{max_retries} (delay={base_delay * (backoff**attempt):.1f}s)"
                )
                order = await order_manager.create_market_order(
                    symbol_id=position.symbol_id,
                    side=(
                        TradeSide.SELL
                        if position.side == PositionSide.LONG
                        else TradeSide.BUY
                    ),
                    amount=exit_amount,
                    params={
                        "reduceOnly": True,
                        "positionSide": (
                            "LONG" if position.side == PositionSide.LONG else "SHORT"
                        ),
                    },
                    config=config,
                    current_price=current_price,
                )
                if order:
                    actual_filled = Decimal(str(order.get("filled", exit_amount)))
                    actual_avg_price = Decimal(str(order.get("average", current_price)))
                    actual_remaining = Decimal(str(order.get("remaining", 0)))
                    if actual_filled > Decimal("0"):
                        break  # Success
                    else:
                        logger.error(
                            f"❌ [{position.symbol_id}] Exit order returned zero fill! "
                            f"Order may have failed. Status: {order.get('status', 'unknown')}"
                        )
                else:
                    logger.error(
                        f"❌ Failed to exit position for {position.symbol_id}: No order returned"
                    )
            except OrderExecutionError as e:
                logger.error(
                    f"[{position.symbol_id}] Exit order execution error (attempt {attempt + 1}): {e}"
                )
                last_exception = e
            except Exception as e:
                logger.error(
                    f"[{position.symbol_id}] Unexpected error during exit order (attempt {attempt + 1}): {e}",
                    exc_info=True,
                )
                last_exception = e
            attempt += 1
            if attempt < max_retries:
                delay = base_delay * (backoff ** (attempt - 1))
                logger.info(
                    f"[{position.symbol_id}] Waiting {delay:.1f}s before next exit order retry..."
                )
                await asyncio.sleep(delay)

        # Final handling after retries
        if not order or (Decimal(str(order.get("filled", 0))) <= Decimal("0")):
            logger.critical(
                f"❌ [{position.symbol_id}] Exit order failed after {max_retries} attempts. Position remains open. Reason: {reason}"
            )
            if last_exception:
                logger.critical(
                    f"[{position.symbol_id}] Last exception: {last_exception}"
                )
            # Optionally send alert/notification here
            return

        # Success: continue with normal exit handling
        actual_filled = Decimal(str(order.get("filled", exit_amount)))
        actual_avg_price = Decimal(str(order.get("average", current_price)))
        actual_remaining = Decimal(str(order.get("remaining", 0)))
        if actual_remaining > Decimal("0.001"):
            logger.error(
                f"❌ [{position.symbol_id}] Exit INCOMPLETE! "
                f"requested={exit_amount}, filled={actual_filled}, "
                f"remaining={actual_remaining}"
            )
            # TODO: Could retry with remaining amount or alert for manual intervention
        if abs(actual_filled - exit_amount) > Decimal("0.0001"):
            logger.warning(
                f"⚠️ [{position.symbol_id}] Exit fill discrepancy: "
                f"requested={exit_amount}, filled={actual_filled}, "
                f"diff={exit_amount - actual_filled}"
            )

        # Extract exit fee
        exit_fee = Decimal(str((order.get("fee") or {}).get("cost", "0")))

        # Calculate proportional entry fee for the amount being closed
        entry_fee_share = Decimal("0")
        if position.initial_amount > Decimal("0"):
            entry_fee_share = (
                actual_filled / position.initial_amount
            ) * position.total_entry_fee

        # Calculate Gross PnL
        if position.side == PositionSide.LONG:
            gross_pnl = (actual_avg_price - position.entry_price) * actual_filled
        else:
            gross_pnl = (position.entry_price - actual_avg_price) * actual_filled

        # Calculate Net PnL
        net_pnl = gross_pnl - entry_fee_share - exit_fee

        total_pnl = net_pnl + position.realized_profit

        # Update capital with Net PNL
        await self.capital_manager.update_capital(position.symbol_id, net_pnl)

        self.total_trades += 1
        if total_pnl > 0:
            self.winning_trades += 1
        self.total_pnl += net_pnl

        # Pass Net PNL to history and notifications
        self._add_trade_to_history(position, actual_avg_price, total_pnl, reason)
        self._update_performance_metrics(position.symbol_id)
        await self._check_tier_transition(position.symbol_id)
        if position.symbol_id in self.positions:
            del self.positions[position.symbol_id]
            # Persist immediately to ensure active_positions.json reflects the removal
            try:
                await self._persist_state()
                logger.debug(
                    f"Persisted state after exiting position {position.symbol_id}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to persist state after exiting {position.symbol_id}: {e}",
                    exc_info=True,
                )
        logger.info(
            f"✅ Exited {position.side.value} position: {position.symbol_id} | "
            f"Filled: {actual_filled} @ {actual_avg_price} | "
            f"Gross PnL: ${gross_pnl:.2f} | Net PnL: ${net_pnl:.2f} | "
            f"Total Net PnL: ${total_pnl:.2f} | "
            f"Fees: Entry=${entry_fee_share:.2f}, Exit=${exit_fee:.2f} | "
            f"Remaining: {actual_remaining} | Reason: {reason}"
        )
        normalized_reason = self._normalize_exit_reason_for_generator(reason)
        await self._send_status_to_generator(position.symbol_id, normalized_reason)
        await self._send_exit_notification(position, current_price, net_pnl, reason)

    async def _persist_state(self) -> None:
        """Persist current state to disk when positions change."""
        # Persist core runtime state first.
        self.state_manager.save_positions(self.positions)
        self.state_manager.save_history(self.trade_history)
        # Guard against concurrent mutation from fill callbacks while serializing.
        async with self._grid_history_lock:
            history_snapshot = self.grid_trade_history.copy()
        await asyncio.to_thread(
            self.state_manager.save_grid_trade_history,
            history_snapshot,
        )

        # 2. Persist Grid States
        grid_states_to_save = {}
        from bot_v2.models.grid_state import GridState

        for symbol, orchestrator in self.grid_orchestrators.items():
            active_orders = {}
            for order_id in orchestrator.grid_order_ids:
                active_orders[str(order_id)] = orchestrator.order_metadata.get(
                    str(order_id), {}
                )

            grid_states_to_save[symbol] = GridState(
                symbol_id=symbol,
                is_active=orchestrator.is_active,
                centre_price=orchestrator.centre_price,
                active_orders=active_orders,
                grid_fills=int(orchestrator.session_fill_count),
                last_tick_time=datetime.now(timezone.utc),
            )

        if grid_states_to_save or self.grid_states:
            self.state_manager.save_grid_states(grid_states_to_save)
            # BUG FIX: Update self.grid_states incrementally instead of overwriting
            # This preserves inactive grids that may become active again
            for symbol, state in grid_states_to_save.items():
                self.grid_states[symbol] = state
            logger.debug(f"Persisted {len(grid_states_to_save)} grid sessions.")

        # Persist runtime exposure snapshot so grid activity remains visible even
        # when directional trade_history.json has no entries.
        exposure_snapshot: Dict[str, Any] = {}
        now_iso = datetime.now(timezone.utc).isoformat()
        for symbol, orchestrator in self.grid_orchestrators.items():
            centre_price = getattr(orchestrator, "centre_price", None)
            exposure_snapshot[symbol] = {
                "timestamp": now_iso,
                "is_active": bool(getattr(orchestrator, "is_active", False)),
                "centre_price": str(centre_price) if centre_price is not None else None,
                "open_order_count": int(
                    len(getattr(orchestrator, "grid_order_ids", set()))
                ),
                "session_fill_count": int(
                    getattr(orchestrator, "session_fill_count", 0)
                ),
                "session_buy_qty": str(
                    getattr(orchestrator, "session_buy_qty", Decimal("0"))
                ),
                "session_sell_qty": str(
                    getattr(orchestrator, "session_sell_qty", Decimal("0"))
                ),
                "session_realized_pnl_quote": str(
                    getattr(orchestrator, "session_realized_pnl_quote", Decimal("0"))
                ),
                "session_reinvest_count": int(
                    getattr(orchestrator, "session_reinvest_count", 0)
                ),
            }
        self.state_manager.save_grid_exposure_snapshot(exposure_snapshot)

    async def _on_grid_trade_closed(self, trade: Dict[str, Any]) -> None:
        """Persist a closed grid trade and update symbol-level capital/performance."""
        symbol = trade.get("symbol")
        pnl = Decimal(str(trade.get("pnl_usd", "0")))

        async with self._grid_history_lock:
            self.grid_trade_history.append(trade)
            history_snapshot = self.grid_trade_history.copy()
        await asyncio.to_thread(
            self.state_manager.save_grid_trade_history,
            history_snapshot,
        )
        await self.capital_manager.update_capital(symbol, pnl)
        self._update_performance_metrics(symbol)
        await self._check_tier_transition(symbol)

        logger.info(
            f"[{symbol}] Grid trade closed: pnl={pnl:+.4f}, "
            f"grid_history_count={len(self.grid_trade_history)}"
        )

    async def _on_grid_fill(self, fill_event: Dict[str, Any]) -> None:
        """Persist grid fill events to append-only JSONL log."""
        await asyncio.to_thread(
            self.state_manager.append_fill_log_event,
            fill_event,
        )

    async def _run_grid_orchestrators_tick(self) -> None:
        """Run one tick for all active grid orchestrators with concurrent data fetches."""
        tick_start = time.perf_counter()

        # DEBUG: Log is_active status for all orchestrators
        for symbol, orch in self.grid_orchestrators.items():
            is_active_value = getattr(orch, "is_active", "ATTR_MISSING")
            logger.debug(f"[DEBUG][GRID_TICK] {symbol} is_active={is_active_value}")

        active = [
            (symbol, orchestrator)
            for symbol, orchestrator in self.grid_orchestrators.items()
            if getattr(orchestrator, "is_active", False)
        ]

        logger.debug(
            f"[DEBUG][GRID_TICK] Active orchestrators: {[s for s, _ in active]}"
        )

        if not active:
            return

        # NOTE: Use concurrent fetches by default.
        # The sequential approach caused 10+ second ticks with multiple symbols.
        # Enable/disable via ENABLE_CONCURRENT_GRID_FETCH env var.
        ENABLE_CONCURRENT_GRID_FETCH = (
            os.getenv("ENABLE_CONCURRENT_GRID_FETCH", "true").lower() == "true"
        )
        fetch_timeout_secs = float(os.getenv("GRID_OHLCV_FETCH_TIMEOUT_SECS", "20"))

        if ENABLE_CONCURRENT_GRID_FETCH:

            async def fetch_with_timeout(symbol, config, exchange, timeout):
                try:
                    return await asyncio.wait_for(
                        exchange.fetch_ohlcv(symbol, config.timeframe, 100),
                        timeout=timeout,
                    )
                except Exception as exc:
                    return exc

            fetch_coroutines = [
                fetch_with_timeout(
                    symbol,
                    self.strategy_configs[symbol],
                    self._get_exchange_for_symbol(symbol),
                    fetch_timeout_secs,
                )
                for symbol, _ in active
            ]
            fetch_results = await asyncio.gather(
                *fetch_coroutines, return_exceptions=True
            )
        else:
            # Sequential fallback
            fetch_results = []
            for symbol, _ in active:
                config = self.strategy_configs[symbol]
                exchange = self._get_exchange_for_symbol(symbol)
                try:
                    ohlcv = await asyncio.wait_for(
                        exchange.fetch_ohlcv(symbol, config.timeframe, 100),
                        timeout=fetch_timeout_secs,
                    )
                except Exception as exc:
                    ohlcv = exc
                fetch_results.append(ohlcv)
        fetch_ms = (time.perf_counter() - tick_start) * 1000.0

        tick_tasks = []
        for idx, (symbol, orchestrator) in enumerate(active):
            ohlcv = fetch_results[idx]
            if isinstance(ohlcv, Exception):
                logger.warning(
                    f"[{symbol}] OHLCV fetch failed for grid tick: "
                    f"{type(ohlcv).__name__}: {ohlcv}"
                )
                continue
            if ohlcv is None:
                continue
            tick_tasks.append(
                self._process_grid_orchestrator_tick(symbol, orchestrator, ohlcv)
            )

        if tick_tasks:
            await asyncio.gather(*tick_tasks, return_exceptions=True)

        total_ms = (time.perf_counter() - tick_start) * 1000.0
        tick_ms = max(total_ms - fetch_ms, 0.0)
        # Only log performance warnings when exceeding threshold to reduce noise
        if ENABLE_GRID_LATENCY_LOGGING and total_ms >= GRID_LATENCY_WARN_MS:
            now = time.time()
            elapsed_since_last = now - self._grid_latency_last_warn_ts
            latency_jump_ms = abs(total_ms - self._grid_latency_last_warn_ms)
            should_log = (
                self._grid_latency_last_warn_ts == 0.0
                or elapsed_since_last >= GRID_LATENCY_WARN_INTERVAL_SECS
                or latency_jump_ms >= GRID_LATENCY_WARN_DELTA_MS
            )
            if should_log:
                logger.warning(
                    "[GRID][PERF] active=%d fetch_ms=%.1f tick_ms=%.1f total_ms=%.1f suppressed=%d",
                    len(active),
                    fetch_ms,
                    tick_ms,
                    total_ms,
                    self._grid_latency_suppressed_count,
                )
                self._grid_latency_last_warn_ts = now
                self._grid_latency_last_warn_ms = total_ms
                self._grid_latency_suppressed_count = 0
            else:
                self._grid_latency_suppressed_count += 1

    async def _process_grid_orchestrator_tick(
        self,
        symbol: str,
        orchestrator: Any,
        ohlcv: Any,
    ) -> None:
        """Process a single grid orchestrator tick."""
        symbol_start = time.perf_counter()
        try:
            exchange = self._get_exchange_for_symbol(symbol)
            current_price = Decimal(str(ohlcv.iloc[-1]["close"]))
            candle_high = Decimal(str(ohlcv.iloc[-1]["high"]))
            candle_low = Decimal(str(ohlcv.iloc[-1]["low"]))

            # 1. Check for simulated fills before ticking logic.
            if hasattr(exchange, "check_fills"):
                open_orders_by_exchange_id = {
                    rec.exchange_order_id: rec
                    for rec in self.order_state_manager.get_open_orders()
                    if rec.exchange_order_id
                }
                filled_ids = await exchange.check_fills(
                    symbol,
                    current_price,
                    candle_high=candle_high,
                    candle_low=candle_low,
                )
                for oid in filled_ids:
                    order_record = open_orders_by_exchange_id.get(oid)

                    if order_record:
                        fill_avg_price = order_record.avg_price or str(current_price)

                        from bot_v2.models.enums import TradeSide as _TradeSide

                        side_enum = (
                            _TradeSide.BUY
                            if order_record.side == "BUY"
                            else _TradeSide.SELL
                        )

                        await orchestrator.handle_fill(
                            oid,
                            Decimal(str(fill_avg_price)),
                            Decimal(str(order_record.quantity)),
                            side_enum,
                        )

                        await self.order_state_manager.update_order_status(
                            order_record.local_id,
                            "FILLED",
                            order_record.quantity,
                            fill_avg_price,
                        )

            # 2. Tick orchestrator logic (Regime/Drift).
            await orchestrator.tick(ohlcv, current_price=current_price)

            if ENABLE_GRID_LATENCY_LOGGING:
                symbol_ms = (time.perf_counter() - symbol_start) * 1000.0
                logger.debug(
                    "[GRID][PERF][%s] symbol_tick_ms=%.1f close=%s",
                    symbol,
                    symbol_ms,
                    current_price,
                )
        except Exception as e:
            logger.error(
                f"[{symbol}] Error in grid orchestrator tick: {e}", exc_info=True
            )

    def _get_active_positions_dict(self) -> Dict[str, Any]:
        """Get active positions formatted for risk manager."""
        return {
            symbol: {"notional": float(pos.entry_price * pos.initial_amount)}
            for symbol, pos in self.positions.items()
        }

    def _get_grid_status_summary(self) -> Dict[str, Any]:
        """Get a summary of all grid orchestrators."""
        total_grids = len(self.grid_orchestrators)
        active_grids = 0
        grid_details = []

        for symbol, orch in self.grid_orchestrators.items():
            is_active = getattr(orch, "is_active", False)
            fills = getattr(orch, "session_fill_count", 0)
            centre = getattr(orch, "centre_price", None)

            if is_active:
                active_grids += 1

            grid_details.append(
                {
                    "symbol": symbol,
                    "is_active": is_active,
                    "fills": fills,
                    "centre_price": centre,
                }
            )

        return {"total": total_grids, "active": active_grids, "details": grid_details}

    def _format_uptime(self) -> str:
        """Format uptime duration."""
        if not hasattr(self, "_start_time") or not self._start_time:
            return "N/A"

        now = datetime.now(timezone.utc)
        delta = now - self._start_time

        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"

    async def get_summary_message(self, hours: int = 24) -> str:
        """
        Generate daily/custom performance summary on demand with combined signal + grid trades.

        Args:
            hours: Number of hours to look back (default: 24)

        Returns:
            Formatted summary string
        """
        try:
            now = datetime.now(timezone.utc)
            summary_period = now - timedelta(hours=hours)

            # Get signal trades
            signal_trades = self.trade_history

            # Get grid trades
            grid_trades = (
                list(self.grid_trade_history)
                if hasattr(self, "grid_trade_history")
                else []
            )

            # Combine and filter trades from last N hours
            def filter_trades_by_time(trades, period):
                filtered = []
                for t in trades:
                    if "timestamp" in t:
                        try:
                            if isinstance(t["timestamp"], str):
                                trade_time = datetime.fromisoformat(
                                    t["timestamp"].replace("Z", "+00:00")
                                )
                            elif isinstance(t["timestamp"], datetime):
                                trade_time = t["timestamp"]
                            else:
                                continue

                            if trade_time >= period:
                                filtered.append(t)
                        except Exception:
                            continue
                return filtered

            recent_signal_trades = filter_trades_by_time(signal_trades, summary_period)
            recent_grid_trades = filter_trades_by_time(grid_trades, summary_period)

            # All combined trades
            recent_trades = recent_signal_trades + recent_grid_trades

            if not recent_trades:
                period_desc = f"{hours}h" if hours <= 48 else f"{hours // 24}d"
                return f"📈 Performance Summary ({period_desc}): No closed trades."

            # Helper function
            def to_decimal(val):
                if val is None:
                    return Decimal(0)
                try:
                    if isinstance(val, (int, float)):
                        return Decimal(str(val))
                    if isinstance(val, str):
                        return Decimal(val)
                    return val
                except Exception:
                    return Decimal(0)

            # Calculate metrics for all trades
            total_pnl = sum(to_decimal(t.get("pnl_usd", 0)) for t in recent_trades)
            total_r = sum(
                to_decimal(t.get("realized_r_multiple", 0)) for t in recent_trades
            )
            win_count = sum(
                1 for t in recent_trades if to_decimal(t.get("pnl_usd", 0)) > 0
            )
            loss_count = len(recent_trades) - win_count
            win_rate = (win_count / len(recent_trades) * 100) if recent_trades else 0

            # Grid vs Signal breakdown
            grid_trades_count = len(recent_grid_trades)
            signal_trades_count = len(recent_signal_trades)
            grid_pnl = sum(to_decimal(t.get("pnl_usd", 0)) for t in recent_grid_trades)
            signal_pnl = sum(
                to_decimal(t.get("pnl_usd", 0)) for t in recent_signal_trades
            )

            # Average metrics
            avg_win = Decimal(0)
            avg_loss = Decimal(0)
            if win_count > 0:
                wins = [
                    to_decimal(t.get("pnl_usd", 0))
                    for t in recent_trades
                    if to_decimal(t.get("pnl_usd", 0)) > 0
                ]
                avg_win = sum(wins) / len(wins) if wins else Decimal(0)
            if loss_count > 0:
                losses = [
                    to_decimal(t.get("pnl_usd", 0))
                    for t in recent_trades
                    if to_decimal(t.get("pnl_usd", 0)) < 0
                ]
                avg_loss = sum(losses) / len(losses) if losses else Decimal(0)

            profit_factor = Decimal("999")
            if avg_loss != 0:
                pf = (
                    (avg_win * win_count) / (abs(avg_loss) * loss_count)
                    if loss_count > 0
                    else Decimal("999")
                )
                profit_factor = pf if pf > 0 else Decimal("999")

            # Duration analysis
            durations = []
            for t in recent_trades:
                if "time_to_exit_sec" in t:
                    try:
                        durations.append(float(t["time_to_exit_sec"]) / 3600)
                    except Exception:
                        continue
            avg_duration = sum(durations) / len(durations) if durations else 0

            # Per-symbol performance
            symbol_performance = {}
            for t in recent_trades:
                symbol = t.get("symbol", "Unknown")
                if symbol not in symbol_performance:
                    symbol_performance[symbol] = {
                        "trades": 0,
                        "wins": 0,
                        "losses": 0,
                        "total_pnl": Decimal(0),
                    }
                perf = symbol_performance[symbol]
                perf["trades"] += 1
                pnl = to_decimal(t.get("pnl_usd", 0))
                perf["total_pnl"] += pnl
                if pnl > 0:
                    perf["wins"] += 1
                else:
                    perf["losses"] += 1

            # Exit reasons
            exit_reasons = {}
            for t in recent_trades:
                reason = t.get("exit_reason", "Unknown")
                if reason not in exit_reasons:
                    exit_reasons[reason] = {"count": 0, "total_pnl": Decimal(0)}
                exit_reasons[reason]["count"] += 1
                exit_reasons[reason]["total_pnl"] += to_decimal(t.get("pnl_usd", 0))

            # Performance status
            if total_pnl > 50:
                performance_status = "🔥 Excellent"
            elif total_pnl > 0:
                performance_status = "🟢 Profitable"
            elif total_pnl > -50:
                performance_status = "🟡 Mixed"
            else:
                performance_status = "🔴 Challenging"

            # Period description
            if hours == 24:
                period_desc = "Last 24h"
            elif hours < 48:
                period_desc = f"Last {hours}h"
            elif hours == 168:
                period_desc = "Last 7 days"
            else:
                period_desc = f"Last {hours // 24}d"

            # Get current capital and calculate return
            all_capitals = self.capital_manager.get_all_capitals() or {}
            total_capital = sum(all_capitals.values())
            pnl_percentage = (
                (total_pnl / total_capital * 100) if total_capital > 0 else Decimal("0")
            )

            # Build message
            lines = [
                "📈 PERFORMANCE SUMMARY",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"📅 {period_desc}",
                f"🎯 Status: {performance_status}",
                "",
                "📊 Trade Overview",
                f"   • Total: {len(recent_trades)} trades ({signal_trades_count} signal, {grid_trades_count} grid)",
                f"   • Win Rate: {win_rate:.1f}% ({win_count}W / {loss_count}L)",
                f"   • Avg Duration: {avg_duration:.1f}h",
                "",
                "💰 Financial",
                f"   • Net P&L: {self.notifier.format_currency(total_pnl)} ({pnl_percentage:+.2f}%)",
                f"   • Signal P&L: {self.notifier.format_currency(signal_pnl)}",
                f"   • Grid P&L: {self.notifier.format_currency(grid_pnl)}",
                f"   • Net R: {self.notifier.format_r_multiple(total_r)}",
                f"   • Avg Win: {self.notifier.format_currency(avg_win)} | Avg Loss: {self.notifier.format_currency(avg_loss)}",
                f"   • Profit Factor: {profit_factor:.2f}",
            ]

            # Add per-symbol if we have multiple symbols
            if len(symbol_performance) > 1:
                lines.append("")
                lines.append("📊 By Symbol")
                for symbol, perf in sorted(
                    symbol_performance.items(),
                    key=lambda x: x[1]["total_pnl"],
                    reverse=True,
                ):
                    wr = (
                        (perf["wins"] / perf["trades"] * 100)
                        if perf["trades"] > 0
                        else 0
                    )
                    lines.append(
                        f"   • {symbol}: {perf['trades']} trades | {self.notifier.format_currency(perf['total_pnl'])} | {wr:.0f}% WR"
                    )

            # Add top exit reasons
            if exit_reasons:
                lines.append("")
                lines.append("🚪 Top Exit Reasons")
                for reason, stats in sorted(
                    exit_reasons.items(), key=lambda x: x[1]["total_pnl"], reverse=True
                )[:4]:
                    emoji = "🟢" if stats["total_pnl"] > 0 else "🔴"
                    lines.append(
                        f"   • {reason}: {stats['count']} | {emoji} {self.notifier.format_currency(stats['total_pnl'])}"
                    )

            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("📈 Keep systematic trading!")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error generating performance summary: {e}", exc_info=True)
            return (
                "⚠️ PERFORMANCE SUMMARY\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "❌ Error generating summary\n"
                "📊 Please try again later\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 Bot continues running normally"
            )

    async def get_status_message(self) -> str:
        """
        Generate formatted status message.

        Returns:
            Formatted status string
        """
        positions = list(self.positions.values())

        # Header
        has_live = any(cfg.mode == "live" for cfg in self.strategy_configs.values())
        mode = "🚀 LIVE" if has_live else "⚙️ SIMULATION"
        uptime = self._format_uptime()

        # Get capital info
        all_capitals = self.capital_manager.get_all_capitals() or {}
        total_capital = sum(all_capitals.values())

        # Get risk status
        risk_summary = self.global_risk_manager.get_risk_summary()
        tier_status = self.risk_manager.get_all_tiers_status()

        # Get grid status
        grid_status = self._get_grid_status_summary()

        # Get signal queue depth
        signal_queue_depth = self.signal_queue.qsize()

        lines = [
            "═══════════════════════════",
            f"  BOT STATUS - {mode}",
            "═══════════════════════════",
            "",
            f"⏱️ Uptime: {uptime}",
            f"📥 Signal Queue: {signal_queue_depth} pending",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "💰 CAPITAL & RISK",
            f"   Total: ${total_capital:.0f}",
        ]

        # Add tier status
        if tier_status:
            lines.append("   ⚡ Tiers:")
            for symbol, info in tier_status.items():
                kill = " ❌" if info.get("kill_switch_active") else ""
                lines.append(
                    f"      {symbol}: {info['tier']} ({info['allocation_pct']}%, {info['leverage']}x){kill}"
                )

        # Add risk summary
        kill_status = "❌ TRIGGERED" if risk_summary["is_halted"] else "✅ Clear"
        lines.append(f"   🛑 Kill Switch: {kill_status}")
        if risk_summary["is_halted"]:
            lines.append(f"      Reason: {risk_summary['halt_reason']}")
        lines.append(
            f"   📉 Drawdown: {risk_summary['current_drawdown_pct']:.1f}% / {risk_summary['max_drawdown_allowed_pct']:.0f}%"
        )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(
            f"📈 POSITIONS: {len(positions)} | 🔲 GRIDS: {grid_status['active']}/{grid_status['total']}"
        )
        lines.append("")

        # Active positions
        if positions:
            for pos in positions:
                try:
                    current_price = await self._get_current_price(pos.symbol_id)
                    if current_price is None or current_price == Decimal("0"):
                        current_price = pos.entry_price

                    amt = getattr(
                        pos,
                        "current_amount",
                        getattr(pos, "initial_amount", Decimal("0")),
                    )
                    if amt is None:
                        amt = Decimal("0")

                    if pos.side == PositionSide.LONG:
                        pnl = (current_price - pos.entry_price) * amt
                        pct = (
                            ((current_price - pos.entry_price) / pos.entry_price * 100)
                            if pos.entry_price and pos.entry_price > 0
                            else Decimal("0")
                        )
                        side_emoji = "📈"
                    else:
                        pnl = (pos.entry_price - current_price) * amt
                        pct = (
                            ((pos.entry_price - current_price) / pos.entry_price * 100)
                            if pos.entry_price and pos.entry_price > 0
                            else Decimal("0")
                        )
                        side_emoji = "📉"

                    pnl_display = self.notifier.format_currency(Decimal(pnl))
                    pct_display = f"{pct:+.2f}%"

                    # Duration
                    now = datetime.now(timezone.utc)
                    duration = self.notifier.format_duration(pos.entry_time, now)

                    # R multiple
                    r_val = getattr(pos, "current_r", Decimal("0"))
                    r_display = self.notifier.format_r_multiple(r_val)

                    lines.append(
                        f"   {side_emoji} {pos.symbol_id} {pos.side.value.upper()}"
                    )
                    lines.append(
                        f"      Entry: ${pos.entry_price:.2f} | Now: ${current_price:.2f}"
                    )
                    lines.append(
                        f"      P&L: {pnl_display} ({pct_display}) | R: {r_display}"
                    )
                    lines.append(f"      Duration: {duration}")
                    lines.append("")
                except Exception as e:
                    logger.error(f"Error computing status for {pos.symbol_id}: {e}")
                    lines.append(f"   ⚠️ {pos.symbol_id}: Error - {e}")
                    lines.append("")
        else:
            lines.append("   No active positions")

        # Add grid status if any
        if grid_status["total"] > 0:
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🔲 ACTIVE GRIDS")
            for detail in grid_status["details"]:
                if detail["is_active"]:
                    centre_str = (
                        f"${detail['centre_price']:.0f}"
                        if detail["centre_price"]
                        else "N/A"
                    )
                    lines.append(
                        f"   {detail['symbol']}: Centre {centre_str} | Fills: {detail['fills']}"
                    )

            # Show inactive grids count
            inactive = grid_status["total"] - grid_status["active"]
            if inactive > 0:
                lines.append(f"   (Inactive: {inactive})")

        lines.append("═══════════════════════════")

        return "\n".join(lines)

    async def stop(self) -> None:
        """Gracefully stop the bot."""
        logger.info("Stopping bot...")
        self.is_running = False

        stop_tasks = [
            orchestrator.stop(reason="TradingBot stop")
            for orchestrator in self.grid_orchestrators.values()
            if getattr(orchestrator, "is_active", False)
        ]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        # Persist final state
        await self._persist_state()

        logger.info("Bot stopped successfully")

    # ==================== Telegram Notifications (from bot_v1) ====================

    async def _send_entry_notification(
        self,
        symbol: str,
        side: PositionSide,
        avg_price: Decimal,
        filled_amount: Decimal,
        notional: Decimal,
        capital: Decimal,
        soft_sl_price: Decimal,
        tp1a_price: Decimal,
        tp1_price: Decimal,
        entry_atr: Decimal,
        tier_name: str,
        capital_allocation_pct: Decimal,
        leverage: Decimal,
        config: StrategyConfig,
    ) -> None:
        """
        Send Telegram notification for position entry with tier information.

        Args:
            symbol: Trading symbol
            side: Position side (LONG/SHORT)
            avg_price: Entry price
            filled_amount: Position size
            notional: Notional value
            capital: Current capital
            soft_sl_price: Stop loss price
            tp1a_price: Quick scalp target
            tp1_price: Main target
            entry_atr: Entry ATR value
            tier_name: Risk tier name
            capital_allocation_pct: Capital allocation percentage from tier
            leverage: Leverage from tier
            config: Strategy configuration for this symbol
        """
        try:
            # Mode indicator (check symbol mode)
            is_live = self.strategy_configs[symbol].mode == "live"
            mode_indicator = "🔴 LIVE" if is_live else "🟢 SIM"

            side_text = "Long" if side == PositionSide.LONG else "Short"
            side_emoji = "📈" if side == PositionSide.LONG else "📉"

            # Calculate risk amount
            risk_per_unit = abs(avg_price - soft_sl_price)
            risk_usd = risk_per_unit * filled_amount

            symbol_text = self.notifier.escape_markdown(symbol)

            msg = (
                f"🚀 NEW POSITION\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{side_emoji} {symbol_text} {side_text} {mode_indicator}\n"
                f"💰 Entry: ${avg_price:.4f} | Qty: {filled_amount:.4f}\n"
                f"🛡️ SL: ${soft_sl_price:.4f} | Risk: ${risk_usd:.0f}\n"
                f"🎯 TP1a: ${tp1a_price:.4f} | TP1: ${tp1_price:.4f}\n"
                f"🏆 Tier: {tier_name} | {capital_allocation_pct:.1f}% capital\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Keep systematic trading!"
            )

            # await self.notifier.send(msg)  # Filtered: new position notifications disabled

        except Exception as e:
            logger.error(f"Error sending entry notification: {e}", exc_info=True)

    async def _send_exit_notification(
        self, position: Position, exit_price: Decimal, pnl: Decimal, reason: str
    ) -> None:
        """
        Send Telegram notification for position exit.

        Args:
            position: Position being exited
            exit_price: Exit price
            pnl: Profit/loss
            reason: Exit reason
        """
        try:
            # Calculate metrics
            pnl_pct = (pnl / (position.entry_price * position.initial_amount)) * 100
            duration = self.notifier.format_duration(position.entry_time)

            # R-multiple
            r_multiple = position.current_r

            pnl_emoji = "🟢" if pnl > 0 else "🔴"
            side_emoji = "📈" if position.side == PositionSide.LONG else "📉"

            symbol_text = self.notifier.escape_markdown(position.symbol_id)
            side_text = "Long" if position.side == PositionSide.LONG else "Short"

            msg = (
                f"🚪 POSITION CLOSED\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{side_emoji} {symbol_text} {side_text}\n"
                f"💰 Entry: ${position.entry_price:.4f} | Exit: ${exit_price:.4f}\n"
                f"{pnl_emoji} P&L: ${pnl:.2f} ({pnl_pct:+.2f}%)\n"
                f"📊 R-Multiple: {self.notifier.format_r_multiple(r_multiple)}\n"
                f"⏱️ Duration: {duration} | Reason: {reason}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 Keep systematic trading!"
            )

            await self.notifier.send(msg)

        except Exception as e:
            logger.error(f"Error sending exit notification: {e}", exc_info=True)

    async def _send_heartbeat(self) -> None:
        """
        Send hourly heartbeat with position status, grid status, and risk metrics.
        """
        try:
            logger.debug("Sending heartbeat...")
            active_count = len(self.positions)
            now = datetime.now(timezone.utc)
            now_str = now.strftime("%Y-%m-%d %H:%M UTC")
            uptime = self._format_uptime()

            # Get grid status
            grid_status = self._get_grid_status_summary()

            # Get risk status
            risk_summary = self.global_risk_manager.get_risk_summary()
            tier_status = self.risk_manager.get_all_tiers_status()

            # Exchange connection status
            exchange_status = (
                "✅ Connected"
                if self.live_exchange
                else "⚠️ Live exchange not configured"
            )

            # Signal queue
            signal_queue_depth = self.signal_queue.qsize()

            # Calculate system metrics
            capitals = [
                await self.capital_manager.get_capital(sym)
                for sym in self.capital_manager._capitals.keys()
            ]
            total_capital = sum(capitals)
            total_unrealized = Decimal("0")

            # --- Heartbeat with no positions ---
            if active_count == 0:
                tier_summary = (
                    ", ".join([f"{s}:{t['tier']}" for s, t in tier_status.items()])
                    if tier_status
                    else "N/A"
                )
                dd_pct = risk_summary["current_drawdown_pct"]

                status_msg = (
                    f"💓 SYSTEM STATUS\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⏰ {now_str}\n"
                    f"⏱️ Uptime: {uptime}\n\n"
                    f"💰 Capital: ${total_capital:.0f}\n"
                    f"📊 Positions: 0 | 🔲 Grids: {grid_status['active']}/{grid_status['total']}\n"
                    f"📥 Signals: {signal_queue_depth} queued\n\n"
                    f"🤖 Exchange: {exchange_status}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ Tiers: {tier_summary}\n"
                    f"🛑 Kill Switch: {'❌ TRIGGERED' if risk_summary['is_halted'] else '✅ Clear'}\n"
                    f"📉 Drawdown: {dd_pct:.1f}% / {risk_summary['max_drawdown_allowed_pct']:.0f}%"
                )
                await self.notifier.send(status_msg)
            else:
                # --- Heartbeat with active positions ---
                position_lines = []

                for pos in list(self.positions.values()):
                    try:
                        current_price = await self._get_current_price(pos.symbol_id)

                        if not current_price or current_price <= Decimal("0"):
                            position_lines.append(f"⚠️ {pos.symbol_id} - Price error")
                            continue

                        # Calculate unrealized PnL
                        pnl_dir = 1 if pos.side == PositionSide.LONG else -1
                        pnl_unrealized = (
                            (current_price - pos.entry_price)
                            * pos.current_amount
                            * pnl_dir
                        )
                        total_unrealized += pnl_unrealized

                        # Calculate position duration
                        duration = self.notifier.format_duration(pos.entry_time, now)

                        # Get profit/loss indicator
                        pnl_indicator = (
                            "📈"
                            if pnl_unrealized > 0
                            else "📉"
                            if pnl_unrealized < 0
                            else "➡️"
                        )
                        side_icon = "LONG" if pos.side == PositionSide.LONG else "SHORT"

                        # R multiple
                        r_val = getattr(pos, "current_r", Decimal("0"))
                        r_display = self.notifier.format_r_multiple(r_val)

                        symbol_text = self.notifier.escape_markdown(pos.symbol_id)

                        position_lines.append(
                            f"{pnl_indicator} {symbol_text} {side_icon}\n"
                            f"   P&L: ${pnl_unrealized:.2f} | {duration} | R: {r_display}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error processing position {pos.symbol_id} in heartbeat: {e}"
                        )
                        position_lines.append(f"⚠️ {pos.symbol_id} - Error")

                # Create system overview
                has_live = any(
                    cfg.mode == "live" for cfg in self.strategy_configs.values()
                )
                mode_text = "LIVE" if has_live else "SIMULATION"

                tier_summary = (
                    ", ".join([f"{s}:{t['tier']}" for s, t in tier_status.items()])
                    if tier_status
                    else "N/A"
                )
                dd_pct = risk_summary["current_drawdown_pct"]

                overview = (
                    f"💓 SYSTEM STATUS\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"⏰ {now_str}\n"
                    f"⏱️ Uptime: {uptime}\n\n"
                    f"💰 Capital: ${total_capital:.0f} | Unrealized: ${total_unrealized:.2f}\n"
                    f"📊 Positions: {active_count} ({mode_text}) | 🔲 Grids: {grid_status['active']}/{grid_status['total']}\n"
                    f"📥 Signals: {signal_queue_depth}\n\n"
                    f"🤖 Exchange: {exchange_status}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(position_lines) + "\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ Tiers: {tier_summary}\n"
                    f"🛑 Kill Switch: {'❌ TRIGGERED' if risk_summary['is_halted'] else '✅ Clear'}\n"
                    f"📉 Drawdown: {dd_pct:.1f}% / {risk_summary['max_drawdown_allowed_pct']:.0f}%"
                )

                await self.notifier.send(overview)

            # --- Daily Performance Summary ---
            if (
                now.hour == 0
                and now.minute < 5
                and (now - self.last_summary_sent).days >= 1
            ):
                self.last_summary_sent = now

                # Use the improved get_summary_message method
                summary_msg = await self.get_summary_message(hours=24)
                await self.notifier.send(summary_msg)

        except Exception as e:
            logger.error(f"Error sending heartbeat: {e}", exc_info=True)

    async def _send_status_to_generator(
        self, symbol: str, event_type: str, extra_payload: dict = None
    ) -> None:
        """
        Send status updates to ALL signal generators for coordination.

        Sends to both DTS generator (port 8000) and NY Breakout generator (port 8001).
        Generators gracefully ignore symbols they don't track.

        Args:
            symbol: Symbol identifier
            event_type: Type of event (ENTRY, EXIT, REJECTED, etc.)
            extra_payload: Optional dictionary of extra data to merge into the payload
        """
        # Collect all generator URLs from environment
        generator_urls = [
            os.getenv("SIGNAL_GENERATOR_STATUS_URL"),  # DTS generator (port 8000)
        ]

        # Filter out None values
        generator_urls = [url for url in generator_urls if url]

        if not generator_urls:
            logger.debug(f"[{symbol}] No signal generator URLs configured")
            return

        payload = {"symbol": symbol, "event": event_type}

        # Add position details if available
        if symbol in self.positions:
            pos = self.positions[symbol]
            payload.update(
                {
                    "position_id": getattr(pos, "position_id", None),
                    "entry_time": (
                        pos.entry_time.isoformat() if pos.entry_time else None
                    ),
                    "entry_order_id": getattr(pos, "entry_order_id", None),
                    "side": (
                        pos.side.value if hasattr(pos.side, "value") else str(pos.side)
                    ),
                    "entry_price": float(pos.entry_price) if pos.entry_price else None,
                    "quantity": (
                        float(pos.initial_amount) if pos.initial_amount else None
                    ),
                }
            )

        # Merge extra payload if provided (overrides existing keys)
        if extra_payload:
            payload.update(extra_payload)

        # Send to all generators concurrently
        async with httpx.AsyncClient() as client:
            tasks = []
            for url in generator_urls:
                tasks.append(
                    self._send_to_single_generator(
                        client, url, symbol, event_type, payload
                    )
                )

            # Gather all results (exceptions caught per-generator)
            await asyncio.gather(*tasks, return_exceptions=True)

    def _normalize_exit_reason_for_generator(self, reason: str) -> str:
        """
        Normalize exit reason to format expected by signal generator.

        Maps bot_v2 exit reasons to signal generator format (SCREAMING_SNAKE_CASE).
        Based on old bot's ExitReason.normalize_reason() logic.

        Args:
            reason: Exit reason from bot_v2

        Returns:
            Normalized reason for signal generator
        """
        # Mapping from bot_v2 reasons to signal generator format
        REASON_MAPPING = {
            "HardSL": "HARD_SL_HIT",
            "SoftSL": "SOFT_SL_HIT",
            "BreakevenStop": "BREAKEVEN_STOP_HIT",
            "TrailExit": "TRAIL_EXIT",
            "TimeExit": "TIME_EXIT",
            "MaeMfeImbalance": "MAE_MFE_IMBALANCE",
            "StaleTrade": "STALE_TRADE_EXIT",
            "AbsoluteStaleExit": "STALE_TRADE_EXIT",
            "CatastrophicStop": "CATASTROPHIC_STOP_HIT",
            "TP1": "TP1_PARTIAL",
            "TP1a": "TP1_PARTIAL",
            # IMPORTANT: TP1b represents a final exit of remaining position after TP1a partial.
            # Generators should treat this as a definitive close, not a partial.
            "TP1b": "POSITION_EXITED",
            "AdverseScaleOut": "ADVERSE_SCALE_OUT",
            "AggressivePeakExit": "AGGRESSIVE_PEAK_EXIT",
            "FlipSignal": "FLIP_SIGNAL",
        }

        # Return mapped value or original if not found
        return REASON_MAPPING.get(reason, reason)

    async def _send_to_single_generator(
        self,
        client: httpx.AsyncClient,
        url: str,
        symbol: str,
        event_type: str,
        payload: dict,
    ) -> None:
        """
        Helper method to send status to a single generator endpoint.

        Args:
            client: HTTP client instance
            url: Generator status URL
            symbol: Symbol identifier
            event_type: Event type
            payload: Status payload
        """
        try:
            await client.post(url, json=payload, timeout=10)
            logger.info(f"[{symbol}] Sent status '{event_type}' to {url}")
        except httpx.RequestError as e:
            logger.error(f"[{symbol}] Failed to send status to {url}: {e}")

    def _update_performance_metrics(self, symbol: str) -> None:
        """
        Recalculate and save performance metrics after a trade closes.

        If metrics calculation fails, keeps existing cached metrics to avoid
        stale tier classification. Logs errors for investigation.

        Args:
            symbol: Trading symbol
        """
        try:
            from bot_v2.risk.adaptive_risk_manager import PerformanceAnalyzer

            # Combine directional and grid histories for symbol-level performance tracking.
            combined_history = [*self.trade_history, *self.grid_trade_history]

            # Get capital for this symbol to use as initial_capital for metrics
            all_capitals = self.capital_manager.get_all_capitals()
            initial_capital = float(all_capitals.get(symbol, Decimal("100.0")))

            metrics = PerformanceAnalyzer.calculate_metrics(
                symbol, combined_history, initial_capital=initial_capital
            )

            # Update cache and save (access the wrapped risk_manager)
            self.risk_manager.risk_manager.performance_cache[symbol] = metrics
            self.risk_manager.risk_manager._save_state()

            logger.info(
                f"[{symbol}] Performance updated: {metrics.total_trades} trades, "
                f"PF={metrics.profit_factor:.2f}, WR={metrics.win_rate:.1%}"
            )
        except Exception as e:
            logger.error(
                f"⚠️  [{symbol}] Failed to update performance metrics: {e}. "
                f"Using cached metrics for tier classification.",
                exc_info=True,
            )

            # Check if metrics exist in cache
            if symbol not in self.risk_manager.risk_manager.performance_cache:
                logger.warning(
                    f"⚠️  [{symbol}] No cached metrics available! "
                    f"Position sizing will use PROBATION tier until metrics can be calculated."
                )

                # Send notification for persistent failures
                try:
                    from datetime import datetime

                    # asyncio.create_task(
                    #     self.notifier.send_text(
                    #         f"⚠️ Performance Metrics Error\n\n"
                    #         f"Symbol: {symbol}\n"
                    #         f"Failed to update metrics after trade close.\n"
                    #         f"Using {'cached' if symbol in self.risk_manager.risk_manager.performance_cache else 'default PROBATION'} tier.\n"
                    #         f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    #         f"Action: Check logs for details."
                    #     )
                    # )  # Filtered: metrics error notifications disabled
                except Exception as notify_error:
                    logger.error(
                        f"Failed to send metrics error notification: {notify_error}"
                    )

    async def _check_tier_transition(self, symbol: str) -> None:
        """
        Re-classify tier and check for transitions after trade closure.

        This triggers full tier re-classification using updated metrics and hysteresis logic,
        then detects any tier changes and sends notifications.

        Args:
            symbol: Trading symbol to check
        """
        logger.info(f"[{symbol}] Re-classifying tier after trade closure...")
        try:
            # Re-classify tier using full hysteresis logic
            # This ensures tier is evaluated with updated metrics and trade counters
            await self._reclassify_tier_after_exit(symbol)

            # Now get the freshly classified tier
            tier_info = self.risk_manager.get_tier_info(symbol)
            current_tier = tier_info["tier"]
            logger.info(f"[{symbol}] Risk manager reports tier: {current_tier}")

            # Get stored tier from capital manager
            stored_tier = await self.capital_manager.get_tier(symbol)
            logger.info(
                f"[{symbol}] Stored tier: {stored_tier}, Current tier: {current_tier}"
            )

            # Check if tier changed
            if current_tier != stored_tier:
                # Update stored tier
                await self.capital_manager.set_tier(symbol, current_tier)

                # Get last notified tier to avoid duplicate notifications
                last_notified = await self.capital_manager.get_last_notified_tier(
                    symbol
                )

                if current_tier != last_notified:
                    # Send tier transition notification
                    await self._send_tier_notification(
                        symbol, stored_tier, current_tier, tier_info
                    )

                    # Update last notified tier
                    await self.capital_manager.set_last_notified_tier(
                        symbol, current_tier
                    )

                    logger.info(
                        f"✅ [{symbol}] Tier transition detected and notified: {stored_tier} → {current_tier}"
                    )
            else:
                logger.info(f"[{symbol}] Tier unchanged: {current_tier}")

        except Exception as e:
            logger.error(
                f"⚠️  [{symbol}] Failed to check tier transition: {e}", exc_info=True
            )

    async def _reclassify_tier_after_exit(self, symbol: str) -> None:
        """
        Re-classify tier after trade exit using full hysteresis logic.

        This method calls the risk manager's classification logic to ensure
        the tier is properly evaluated with updated metrics and trade counters.

        Args:
            symbol: Trading symbol to re-classify
        """
        try:
            from bot_v2.risk.adaptive_risk_manager import RiskTierClassifier

            # Get current capital
            await self.capital_manager.get_capital(symbol)

            # Get updated metrics from performance cache
            metrics = self.risk_manager.risk_manager.performance_cache.get(symbol)

            if not metrics:
                logger.warning(
                    f"[{symbol}] No metrics available for tier re-classification"
                )
                return

            # Get tier history from CapitalManager
            tier_hist = await self.capital_manager.get_tier_history(symbol)

            logger.info(
                f"[{symbol}] Re-classifying with metrics: trades={metrics.total_trades}, "
                f"PF={metrics.profit_factor:.2f}, current_tier={tier_hist.get('current_tier')}"
            )

            # Classify with hysteresis
            tier = RiskTierClassifier.classify(metrics, tier_hist)
            logger.info(f"[{symbol}] Re-classified as {tier.name}")

            # Update tier history in CapitalManager
            old_tier_name = tier_hist.get("current_tier")

            if old_tier_name != tier.name:
                # Tier changed: reset counters
                from datetime import datetime, timezone

                new_tier_data = {
                    "current_tier": tier.name,
                    "tier_entry_time": datetime.now(timezone.utc).isoformat(),
                    "trades_in_tier": 0,
                    "consecutive_losses_in_tier": 0,
                    "last_transition_time": datetime.now(timezone.utc).isoformat(),
                    "previous_tier": old_tier_name,
                    "last_total_trades": metrics.total_trades,
                }
                await self.capital_manager.update_tier_history(symbol, new_tier_data)
                logger.info(
                    f"[{symbol}] Tier transition recorded: {old_tier_name} → {tier.name}"
                )

                # Send tier transition notification
                last_notified = await self.capital_manager.get_last_notified_tier(
                    symbol
                )
                if tier.name != last_notified:
                    tier_info = {
                        "tier": tier.name,
                        "metrics": {
                            "total_trades": metrics.total_trades,
                            "profit_factor": metrics.profit_factor,
                            "sharpe_ratio": getattr(metrics, "sharpe_ratio", 0),
                            "win_rate": metrics.win_rate,
                        },
                        "capital_allocation": tier.capital_allocation,
                        "max_leverage": tier.max_leverage,
                    }
                    await self._send_tier_notification(
                        symbol, old_tier_name, tier.name, tier_info
                    )
                    await self.capital_manager.set_last_notified_tier(symbol, tier.name)
            else:
                # Same tier: increment counters
                trades_in_tier = tier_hist.get("trades_in_tier", 0) + 1
                consecutive_losses = max(0, metrics.current_consecutive_losses)

                updated_tier_data = {
                    "current_tier": tier.name,
                    "tier_entry_time": tier_hist.get("tier_entry_time"),
                    "trades_in_tier": trades_in_tier,
                    "consecutive_losses_in_tier": consecutive_losses,
                    "last_transition_time": tier_hist.get("last_transition_time"),
                    "previous_tier": tier_hist.get("previous_tier"),
                    "last_total_trades": metrics.total_trades,
                }
                await self.capital_manager.update_tier_history(
                    symbol, updated_tier_data
                )
                logger.info(
                    f"[{symbol}] Tier counters updated: trades_in_tier={trades_in_tier}, "
                    f"consecutive_losses={consecutive_losses}"
                )

            # Update tier cache in risk manager
            self.risk_manager.risk_manager.tier_cache[symbol] = tier

        except Exception as e:
            logger.error(
                f"⚠️  [{symbol}] Failed to re-classify tier after exit: {e}",
                exc_info=True,
            )

    async def _send_tier_notification(
        self, symbol: str, old_tier: str, new_tier: str, tier_info: Dict[str, Any]
    ) -> None:
        """
        Send Telegram notification for tier transition.

        Args:
            symbol: Trading symbol
            old_tier: Previous tier
            new_tier: New tier
            tier_info: Current tier information dict
        """
        try:
            # Get metrics from tier_info
            metrics = tier_info.get("metrics", {})
            if metrics:
                total_trades = metrics.get("total_trades", 0)
                pf = metrics.get("profit_factor", 0)
                sharpe = metrics.get("sharpe_ratio", 0)
                win_rate = (
                    metrics.get("win_rate", 0) * 100 if metrics.get("win_rate") else 0
                )
            else:
                total_trades = 0
                pf = 0
                sharpe = 0
                win_rate = 0

            # Determine if promotion or demotion
            tier_order = [
                "PROBATION",
                "CONSERVATIVE",
                "STANDARD",
                "AGGRESSIVE",
                "CHAMPION",
            ]
            old_idx = tier_order.index(old_tier) if old_tier in tier_order else 0
            new_idx = tier_order.index(new_tier) if new_tier in tier_order else 0

            is_promotion = new_idx > old_idx

            # Get new tier allocation and leverage
            capital_alloc = tier_info.get("capital_allocation", 0) * 100
            max_leverage = tier_info.get("max_leverage", 1)

            # Create notification message
            if is_promotion:
                emoji = "📈"
                title = "TIER PROMOTION"
                color = "🟢"
            else:
                emoji = "📉"
                title = "TIER DEMOTION"
                color = "🔴"

            symbol_text = self.notifier.escape_markdown(symbol)

            msg = (
                f"{emoji} *{title}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *Symbol:* `{symbol_text}`\n"
                f"{color} *Tier Change:* `{old_tier}` → `{new_tier}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Performance Metrics:*\n"
                f"  • Total Trades: `{total_trades}`\n"
                f"  • Profit Factor: `{pf:.2f}`\n"
                f"  • Sharpe Ratio: `{sharpe:.2f}`\n"
                f"  • Win Rate: `{win_rate:.1f}%`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *New Tier Settings:*\n"
                f"  • Capital Allocation: `{capital_alloc:.1f}%`\n"
                f"  • Max Leverage: `{max_leverage}x`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            )

            await self.notifier.send(msg)

            logger.info(
                f"✅ [{symbol}] Tier transition notification sent: {old_tier} → {new_tier}"
            )

        except Exception as e:
            logger.error(
                f"⚠️  [{symbol}] Failed to send tier notification: {e}", exc_info=True
            )

    def _add_trade_to_history(
        self, position: Position, exit_price: Decimal, pnl: Decimal, exit_reason: str
    ) -> None:
        """
        Add completed trade to history (EXACT from bot_v1).

        Args:
            position: Position object
            exit_price: Exit price
            pnl: Profit/loss in USD
            exit_reason: Reason for exit
        """
        initial_risk_usd = position.initial_risk_atr * position.initial_amount
        realized_r = (
            pnl / initial_risk_usd if initial_risk_usd != Decimal("0") else Decimal("0")
        )

        # Calculate MFE/MAE in R multiples
        mfe_r = (
            (position.mfe * position.initial_amount) / initial_risk_usd
            if initial_risk_usd > 0
            else Decimal("0")
        )
        mae_r = (
            (position.mae * position.initial_amount) / initial_risk_usd
            if initial_risk_usd > 0
            else Decimal("0")
        )

        # Calculate time to TP1 if it was hit
        time_to_tp1_sec = None
        if position.tp1a_hit and position.post_tp1_probation_start:
            time_to_tp1_sec = (
                position.post_tp1_probation_start - position.entry_time
            ).total_seconds()

        history_entry = {
            "timestamp": datetime.now(timezone.utc),
            "type": "exit",
            "symbol": position.symbol_id,
            "position_id": getattr(position, "position_id", None),
            "entry_order_id": getattr(position, "entry_order_id", None),
            "side": position.side.value,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "pnl_usd": pnl,
            "exit_reason": exit_reason,
            "realized_r_multiple": realized_r,
            "mfe_r": mfe_r,
            "mae_r": mae_r,
            "mfe_usd": position.mfe * position.initial_amount,
            "mae_usd": position.mae * position.initial_amount,
            "entry_time": position.entry_time,
            "time_to_tp1_sec": time_to_tp1_sec,
            "time_to_exit_sec": (
                datetime.now(timezone.utc) - position.entry_time
            ).total_seconds(),
            "weak_post_tp1": position.weak_post_tp1_detected,
            # CRITICAL FIELDS (post-TP1 analysis)
            "tp1a_hit": position.tp1a_hit,
            "tp1_price": position.tp1_price,
            "tp1a_price": position.tp1a_price,
            "peak_favorable_r": position.peak_favorable_r,
            "peak_favorable_r_beyond_tp1": position.peak_favorable_r_beyond_tp1,
            "current_r": position.current_r,
            "max_adverse_r_since_tp1": position.max_adverse_r_since_tp1,
            "ratio_since_tp1": position.ratio_since_tp1,
            "current_ratio": position.current_ratio,
            # HIGH PRIORITY FIELDS (detailed tracking)
            "is_trailing_active": position.is_trailing_active,
            "trailing_sl_price": position.trailing_sl_price,
            "trailing_start_r": position.trailing_start_r,
            "initial_amount": position.initial_amount,
            "current_amount": position.current_amount,
            "realized_profit": position.realized_profit,
            "bars_held": position.bars_held,
            "peak_price_since_tp1": position.peak_price_since_tp1,
            # ENTRY CONTEXT FIELDS (risk management)
            "leverage": position.leverage,
            "tier_name": position.tier_name,
            "capital_allocation_pct": position.capital_allocation_pct,
        }

        # Log weak post-TP1 exits
        if position.weak_post_tp1_detected and position.weak_post_tp1_since:
            weak_duration = (
                datetime.now(timezone.utc) - position.weak_post_tp1_since
            ).total_seconds() / 60
            capture_pct = float(realized_r / mfe_r * 100) if mfe_r > 0 else 0
            logger.info(
                f"[{position.symbol_id}] WEAK POST-TP1 EXIT: Was weak for {weak_duration:.1f} min, "
                f"captured {capture_pct:.1f}% of peak ({realized_r:.4f}R / {mfe_r:.4f}R)"
            )

        self.trade_history.append(history_entry)
        self.state_manager.save_history(self.trade_history)
        logger.info(
            f"[{position.symbol_id}] Trade recorded to history: {pnl:+.2f} USD ({realized_r:+.3f}R)"
        )

        # Evaluate second trade leverage qualification (first completed trade of UTC day)
        self._evaluate_second_trade_leverage_qualification(
            position, history_entry, pnl, exit_reason
        )

    def _evaluate_second_trade_leverage_qualification(
        self,
        position: Position,
        history_entry: Dict[str, Any],
        pnl: Decimal,
        exit_reason: str,
    ) -> None:
        """
        Evaluate if this trade qualifies for second trade max leverage override.

        Args:
            position: The position that just closed
            history_entry: The history entry dict
            pnl: Realized PnL
            exit_reason: Reason for exit
        """
        try:
            import json as _json
            from pathlib import Path as _Path

            feature_path = _Path("config") / "second_trade_override.feature.json"
            feature_cfg = {}
            if feature_path.exists():
                with open(feature_path) as _f:
                    feature_cfg = _json.load(_f)

            if not feature_cfg.get("enabled", False):
                return

            allowed_reasons = set(feature_cfg.get("allowed_reasons", []))
            if exit_reason not in allowed_reasons:
                return

            if pnl <= Decimal("0"):
                return

            max_time_minutes = feature_cfg.get("max_time_minutes", 30)
            time_open_min = (
                datetime.now(timezone.utc) - position.entry_time
            ).total_seconds() / 60
            if time_open_min > max_time_minutes:
                return

            # Determine day key and whether this is first completed trade
            day_key = self.state_manager.make_day_key(history_entry["timestamp"])

            scope = feature_cfg.get("scope", "global")

            # Count prior exits today
            # If scope is per_symbol, we only care about prior exits for THIS symbol
            prior_exits_today = []
            target_date = history_entry["timestamp"].date()

            for t in self.trade_history[:-1]:
                ts = t.get("timestamp")
                if isinstance(ts, str):
                    try:
                        # Handle ISO format with potential Z suffix
                        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except ValueError:
                        continue

                if (
                    isinstance(ts, datetime)
                    and ts.date() == target_date
                    and t.get("type") == "exit"
                ):
                    prior_exits_today.append(t)

            if scope == "per_symbol":
                prior_exits_today = [
                    t
                    for t in prior_exits_today
                    if t.get("symbol") == position.symbol_id
                ]

            # Optional: Check R-multiple if configured (use value already calculated in history_entry)
            min_r_multiple = feature_cfg.get("require_min_pnl_r_multiple", 0)
            if min_r_multiple > 0:
                realized_r_multiple = history_entry.get(
                    "realized_r_multiple", Decimal("0")
                )
                # Convert to float for comparison if it's a Decimal
                if isinstance(realized_r_multiple, Decimal):
                    realized_r_multiple = float(realized_r_multiple)

                if realized_r_multiple < min_r_multiple:
                    logger.info(
                        f"[{position.symbol_id}] leverage_override_rejected_low_r day_key={day_key} "
                        f"realized_r={realized_r_multiple:.2f} min_r_required={min_r_multiple}"
                    )
                    return  # Does not meet R-multiple threshold

            # Check daily override limit (removed single-override restriction; now allow multiple with limit)
            scope_key_base = (
                "GLOBAL" if scope == "global" else position.symbol_id.replace("/", "")
            )

            override_count = self._count_daily_overrides(scope_key_base, day_key)
            max_overrides = feature_cfg.get("max_daily_overrides_per_symbol", 3)

            if override_count >= max_overrides:
                logger.info(
                    f"[{position.symbol_id}] leverage_override_rejected_limit_reached day_key={day_key} scope={scope_key_base} count={override_count} max={max_overrides}"
                )
                return  # Hit daily override limit

            # Generate unique scope_key with sequence number
            sequence_num = override_count + 1
            scope_key = f"{scope_key_base}_{sequence_num}"

            payload = {
                "qualified_at": datetime.now(timezone.utc).isoformat(),
                "reason": exit_reason,
                "time_open_min": round(time_open_min, 2),
                "pnl_usd": str(pnl),
                "scope": scope,
                "symbol": position.symbol_id if scope == "per_symbol" else None,
                "consumed": False,
                "rule_version": feature_cfg.get("rule_version", "1"),
            }

            self.state_manager.set_second_trade_override(day_key, scope_key, payload)
            logger.info(
                f"[{position.symbol_id}] leverage_override_qualified day_key={day_key} scope={scope_key} (seq {sequence_num}/{max_overrides}) payload={payload}"
            )

        except Exception as e:
            logger.error(
                f"[{position.symbol_id}] Error evaluating second trade leverage qualification: {e}"
            )

    def _count_daily_overrides(self, scope_key_prefix: str, day_key: str) -> int:
        """
        Count the number of daily overrides for a scope (symbol) today.

        Args:
            scope_key_prefix: Base symbol (e.g., 'XRPUSDT') without sequence suffix
            day_key: Date key (e.g., '20260307_UTC')

        Returns:
            Count of non-expired overrides for the symbol today
        """
        try:
            return self.state_manager.count_daily_overrides(day_key, scope_key_prefix)
        except Exception as e:
            logger.warning(
                f"Error counting daily overrides for {scope_key_prefix}: {e}"
            )
            return 0
