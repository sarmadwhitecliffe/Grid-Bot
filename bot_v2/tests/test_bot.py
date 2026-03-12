"""
Tests for the main TradingBot class.

Tests bot initialization, signal handling, position management, and lifecycle.
"""

import os
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from bot_v2.bot import TradingBot
from bot_v2.models.enums import PositionSide
from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig


def _make_strategy_config(symbol_id: str, mode: str = "local_sim") -> StrategyConfig:
    """Create deterministic test config objects with concrete values."""
    return StrategyConfig(
        symbol_id=symbol_id,
        mode=mode,
        initial_capital=Decimal("1000.00"),
        leverage=Decimal("1.0"),
        timeframe="5m",
        atr_period=14,
        trailing_start_r=Decimal("1.0"),
        trail_sl_atr_mult=Decimal("2.0"),
        tp1_atr_mult=Decimal("2.0"),
        cost_floor_multiplier=Decimal("1.5"),
        slippage_pct=Decimal("0.1"),
        grid_enabled=False,
    )


def _filled_order(price: str = "50000", amount: str = "0.01") -> dict:
    """Return an order payload matching OrderManager expectations."""
    return {
        "id": "test-order-1",
        "average": price,
        "filled": amount,
        "remaining": "0",
        "fee": {"cost": "0"},
        "status": "closed",
    }


def create_test_position(symbol="TEST/USDT", side=PositionSide.LONG, entry_price=50000.0):
    """Helper to create a test position with all required fields."""
    return Position(
        symbol_id=symbol,
        side=side,
        entry_price=Decimal(str(entry_price)),
        entry_time=datetime.now(timezone.utc),
        initial_amount=Decimal("0.01"),
        entry_atr=Decimal("2000.0"),
        initial_risk_atr=Decimal("2000.0"),
        total_entry_fee=Decimal("0.5"),
        soft_sl_price=Decimal(str(entry_price * 0.98)),
        hard_sl_price=Decimal(str(entry_price * 0.97)),
        tp1_price=Decimal(str(entry_price * 1.04)),
    )


@pytest.fixture
def mock_config():
    """Create a mock strategy configuration."""
    config = _make_strategy_config("TEST/USDT")
    return config


@pytest_asyncio.fixture
async def bot(mock_config, temp_data_dir):
    """Create a TradingBot instance for testing."""
    # TradingBot expects a Dict[str, StrategyConfig] in multi-symbol mode
    # or a single StrategyConfig in single-symbol mode.
    # We will use single-symbol mode but ensure TEST/USDT and FAKE/USDT are available.
    fake_config = _make_strategy_config("FAKE/USDT")
    btcusdt_config = _make_strategy_config("BTCUSDT")

    configs = {
        "TEST/USDT": mock_config,
        "FAKE/USDT": fake_config,
        "BTCUSDT": btcusdt_config
    }
    
    # We must set data_dir on the first config because TradingBot looks for it there
    mock_config.data_dir = temp_data_dir
    
    bot = TradingBot(configs)
    import pandas as pd

    base_ohlcv = pd.DataFrame(
        [
            [1630000000 + i * 300, 50000.0, 50100.0, 49900.0, 50050.0, 1000.0]
            for i in range(20)
        ],
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("50000.0"))
    bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=base_ohlcv)

    try:
        yield bot
    finally:
        await bot.shutdown()


class TestBotInitialization:
    """Test bot initialization."""

    @pytest.mark.asyncio
    async def test_bot_creates_successfully(self, bot):
        """Test that bot initializes all components."""
        assert bot.is_running is False
        assert bot.sim_exchange is not None
        assert bot.position_tracker is not None
        assert bot.positions is not None  # Dict for position storage
        assert bot.sim_order_manager is not None  # Order manager for simulation
        assert bot.capital_manager is not None
        assert bot.risk_manager is not None
        assert bot.signal_queue is not None

    @pytest.mark.asyncio
    async def test_bot_simulation_mode(self, mock_config, temp_data_dir):
        """Test bot in simulation mode."""
        mock_config.data_dir = temp_data_dir
        bot = TradingBot({"TEST/USDT": mock_config})
        try:
            assert bot.sim_exchange.__class__.__name__ == "SimulatedExchange"
        finally:
            await bot.shutdown()

    @pytest.mark.asyncio
    async def test_bot_live_mode(self, mock_config, temp_data_dir):
        """Test bot in live mode."""
        import os

        if not (os.getenv("FUTURES_API_KEY") and os.getenv("FUTURES_API_SECRET")):
            pytest.skip("Skipping live mode test: API credentials not set.")
        mock_config.data_dir = temp_data_dir
        mock_config.mode = "live"
        bot = TradingBot({"TEST/USDT": mock_config})
        try:
            assert bot.live_exchange.__class__.__name__ == "LiveExchange"
        finally:
            await bot.shutdown()

    @pytest.mark.asyncio
    async def test_bot_performance_tracking_initialized(self, bot):
        """Test that performance tracking is initialized."""
        assert bot.total_trades == 0
        assert bot.winning_trades == 0
        assert bot.total_pnl == Decimal("0")


class TestBotInitialize:
    """Test bot state initialization."""

    @pytest.mark.asyncio
    async def test_initialize_with_no_state(self, bot):
        """Test initialization with no persisted state."""
        await bot.initialize()
        assert len(bot.positions) == 0

    @pytest.mark.asyncio
    async def test_initialize_with_persisted_positions(self, bot):
        """Test initialization with persisted positions."""
        # Create and save a position
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        # Save to state manager
        bot.state_manager.save_positions(bot.positions)

        # Clear and reinitialize
        bot.positions = {}
        await bot.initialize()

        # Verify position was loaded
        assert "BTCUSDT" in bot.positions
        assert bot.positions["BTCUSDT"].symbol_id == "BTCUSDT"


class TestSignalHandling:
    """Test webhook signal handling."""

    @pytest.mark.asyncio
    async def test_handle_webhook_signal_queues_signal(self, bot):
        """Test that webhook signals are queued."""
        signal = {"action": "buy", "symbol": "TEST/USDT"}

        await bot.handle_webhook_signal(signal)

        assert bot.signal_queue.qsize() == 1

    @pytest.mark.asyncio
    async def test_process_buy_signal(self, bot):
        """Test processing a buy signal."""
        # Ensure atr_period is an int, not a MagicMock
        bot.strategy_configs["TEST/USDT"].atr_period = 14
        # Set required config attributes for cost_filter
        bot.strategy_configs["TEST/USDT"].tp1_atr_mult = Decimal("2.0")
        bot.strategy_configs["TEST/USDT"].cost_floor_multiplier = Decimal("1.5")
        bot.strategy_configs["TEST/USDT"].slippage_pct = Decimal("0.1")
        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("50000", "0.01")
        )
        bot._get_current_atr = AsyncMock(return_value=Decimal("1000.0"))
        # Patch exchange.fetch_ohlcv to return expected DataFrame
        import pandas as pd

        # Patch exchange.fetch_ohlcv to return DataFrame with >= 14 rows
        ohlcv_data = [
            [
                1630000000 + i * 300,
                50000.0 + i * 10,
                50100.0 + i * 10,
                49900.0 + i * 10,
                50050.0 + i * 10,
                1000.0 + i * 20,
            ]
            for i in range(14)
        ]
        ohlcv_df = pd.DataFrame(
            ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_df)
        bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("50000.0"))
        signal = {"action": "buy", "symbol": "TEST/USDT"}
        await bot.handle_webhook_signal(signal)
        await bot._process_signals()
        assert len(bot.positions) == 1
        assert "TEST/USDT" in bot.positions
        assert bot.positions["TEST/USDT"].side == PositionSide.LONG

    @pytest.mark.asyncio
    async def test_process_sell_signal(self, bot):
        """Test processing a sell signal."""
        # Ensure FAKE/USDT config exists and has required attributes
        bot.strategy_configs["FAKE/USDT"] = _make_strategy_config("FAKE/USDT")
        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("3000", "0.1")
        )
        bot._get_current_atr = AsyncMock(return_value=Decimal("100.0"))
        import pandas as pd

        ohlcv_data = [
            [
                1630000000 + i * 300,
                3000.0 + i * 10,
                3010.0 + i * 10,
                2990.0 + i * 10,
                3005.0 + i * 10,
                500.0 + i * 10,
            ]
            for i in range(14)
        ]
        ohlcv_df = pd.DataFrame(
            ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_df)
        bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("3000.0"))
        signal = {"action": "sell", "symbol": "FAKE/USDT"}
        await bot.handle_webhook_signal(signal)
        await bot._process_signals()
        assert len(bot.positions) == 1
        assert "FAKE/USDT" in bot.positions
        assert bot.positions["FAKE/USDT"].side == PositionSide.SHORT

    @pytest.mark.asyncio
    async def test_process_exit_signal(self, bot):
        """Test processing an exit signal."""
        # Create a position first
        position = create_test_position("TEST/USDT", PositionSide.LONG, 50000.0)
        bot.positions["TEST/USDT"] = position

        # Mock order execution
        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("50000", "0.01")
        )
        bot._get_current_price = AsyncMock(return_value=Decimal("50000.0"))

        # Send exit signal
        signal = {"action": "exit", "symbol": "TEST/USDT"}
        await bot.handle_webhook_signal(signal)
        await bot._process_signals()

        # Verify position was removed
        assert "TEST/USDT" not in bot.positions
        assert len(bot.positions) == 0

    @pytest.mark.asyncio
    async def test_ignore_entry_when_already_in_position(self, bot):
        """Test that entry signals are ignored if already in position."""
        # Create existing position
        position = create_test_position("TEST/USDT", PositionSide.LONG, 50000.0)
        bot.positions["TEST/USDT"] = position

        # Try to enter again
        signal = {"action": "buy", "symbol": "TEST/USDT"}
        await bot.handle_webhook_signal(signal)
        await bot._process_signals()

        # Still only one position
        assert len(bot.positions) == 1


class TestPositionManagement:
    """Test position entry and exit."""

    @pytest.mark.asyncio
    async def test_trailing_activates_on_tp1_hit_below_start_r(
        self, bot, sample_long_position
    ):
        """Trailing activates via TP1 hit even when current R is below start threshold."""

        pos = sample_long_position.copy(
            current_r=Decimal("0.70"),
            tp1a_hit=True,
            peak_price_since_entry=Decimal("107.0"),
            trailing_sl_price=None,
        )
        bot.positions[pos.symbol_id] = pos

        bot.strategy_configs[pos.symbol_id] = MagicMock()
        bot.strategy_configs[pos.symbol_id].trailing_start_r = Decimal("0.85")
        bot.strategy_configs[pos.symbol_id].trail_sl_atr_mult = Decimal("2.0")
        bot.strategy_configs[pos.symbol_id].timeframe = "5m"

        bot._get_current_price = AsyncMock(return_value=Decimal("107.0"))
        bot._get_current_atr = AsyncMock(return_value=Decimal("2.0"))
        bot.position_tracker.update_all_metrics = MagicMock(return_value=pos)

        await bot._monitor_positions()

        updated = bot.positions[pos.symbol_id]
        assert updated.is_trailing_active is True
        assert updated.trailing_sl_price is not None

    @pytest.mark.asyncio
    async def test_entry_creates_position_with_stops(self, bot):
        """Test that position entry creates stop loss and take profit."""
        # Ensure atr_period is an int, not a MagicMock
        bot.strategy_configs["BTCUSDT"].atr_period = 14
        # Set required config attributes for cost_filter
        bot.strategy_configs["BTCUSDT"].tp1_atr_mult = Decimal("2.0")
        bot.strategy_configs["BTCUSDT"].cost_floor_multiplier = Decimal("1.5")
        bot.strategy_configs["BTCUSDT"].slippage_pct = Decimal("0.1")
        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("50000", "0.01")
        )
        bot._get_current_atr = AsyncMock(return_value=Decimal("1000.0"))
        import pandas as pd

        ohlcv_data = [
            [
                1630000000 + i * 300,
                50000.0 + i * 10,
                50100.0 + i * 10,
                49900.0 + i * 10,
                50050.0 + i * 10,
                1000.0 + i * 20,
            ]
            for i in range(14)
        ]
        ohlcv_df = pd.DataFrame(
            ohlcv_data, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_df)
        bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("50000.0"))
        await bot._handle_entry_signal("BTCUSDT", PositionSide.LONG)
        positions = list(bot.positions.values())
        assert len(positions) == 1
        position = positions[0]
        assert position.soft_sl_price is not None
        assert position.tp1_price is not None

    @pytest.mark.asyncio
    async def test_entry_aborts_when_amount_normalizes_to_zero(self, bot):
        """Entry aborts before order placement if normalized amount is zero."""
        bot.strategy_configs["TEST/USDT"].atr_period = 14
        bot.strategy_configs["TEST/USDT"].tp1_atr_mult = Decimal("2.0")
        bot.strategy_configs["TEST/USDT"].cost_floor_multiplier = Decimal("1.5")
        bot.strategy_configs["TEST/USDT"].slippage_pct = Decimal("0.1")

        order_manager = MagicMock()
        order_manager.create_market_order = AsyncMock()
        bot._get_order_manager_for_symbol = MagicMock(return_value=order_manager)

        bot._preview_normalized_order_amount = MagicMock(return_value=Decimal("0"))
        bot._get_exchange_step_size = MagicMock(return_value=Decimal("0.001"))

        import pandas as pd

        ohlcv_data = [
            [
                1630000000 + i * 300,
                50000.0 + i * 10,
                50100.0 + i * 10,
                49900.0 + i * 10,
                50050.0 + i * 10,
                1000.0 + i * 20,
            ]
            for i in range(14)
        ]
        ohlcv_df = pd.DataFrame(
            ohlcv_data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("50000.0"))
        bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_df)

        await bot._handle_entry_signal("TEST/USDT", PositionSide.LONG)

        order_manager.create_market_order.assert_not_awaited()
        assert "TEST/USDT" not in bot.positions

    @pytest.mark.asyncio
    async def test_entry_error_sends_rejected_status(self, bot):
        """Order execution exceptions must emit REJECTED to clear signal state."""
        bot.strategy_configs["TEST/USDT"].atr_period = 14
        bot.strategy_configs["TEST/USDT"].tp1_atr_mult = Decimal("2.0")
        bot.strategy_configs["TEST/USDT"].cost_floor_multiplier = Decimal("1.5")
        bot.strategy_configs["TEST/USDT"].slippage_pct = Decimal("0.1")

        bot.capital_manager.get_capital = AsyncMock(return_value=Decimal("1000"))
        bot.risk_manager.calculate_position_params = AsyncMock(
            return_value={
                "allowed": True,
                "tier": "PROBATION",
                "capital_allocation_pct": 30.0,
                "leverage": 1,
                "tier_max_leverage": 2,
            }
        )
        bot.cost_filter.is_cost_floor_met = MagicMock(return_value=True)

        order_manager = MagicMock()
        order_manager.create_market_order = AsyncMock(side_effect=Exception("boom"))
        bot._get_order_manager_for_symbol = MagicMock(return_value=order_manager)

        bot._send_status_to_generator = AsyncMock()

        import pandas as pd

        ohlcv_data = [
            [
                1630000000 + i * 300,
                50000.0 + i * 10,
                50100.0 + i * 10,
                49900.0 + i * 10,
                50050.0 + i * 10,
                1000.0 + i * 20,
            ]
            for i in range(14)
        ]
        ohlcv_df = pd.DataFrame(
            ohlcv_data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        bot.sim_exchange.get_market_price = AsyncMock(return_value=Decimal("50000.0"))
        bot.sim_exchange.fetch_ohlcv = AsyncMock(return_value=ohlcv_df)

        await bot._handle_entry_signal("TEST/USDT", PositionSide.LONG)

        bot._send_status_to_generator.assert_any_call("TEST/USDT", "REJECTED")
        assert "TEST/USDT" not in bot.positions

    @pytest.mark.asyncio
    async def test_exit_updates_capital(self, bot):
        """Test that exiting a position updates capital."""
        # Create position
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        # Get initial capital
        initial_capital = await bot.capital_manager.get_capital("BTCUSDT")

        # Mock order execution and price fetch to simulate profit
        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("51000", "0.01")
        )
        bot._get_current_price = AsyncMock(return_value=Decimal("51000.0"))  # 2% profit

        # Exit position
        await bot._exit_position(position, "TAKE_PROFIT_1")

        # Capital should have increased
        final_capital = await bot.capital_manager.get_capital("BTCUSDT")
        assert final_capital > initial_capital

    @pytest.mark.asyncio
    async def test_exit_updates_statistics(self, bot):
        """Test that exiting updates trade statistics."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("51000", "0.01")
        )
        bot._get_current_price = AsyncMock(
            return_value=Decimal("51000.0")
        )  # Profitable exit

        initial_trades = bot.total_trades
        initial_winners = bot.winning_trades

        await bot._exit_position(position, "TAKE_PROFIT_1")

        assert bot.total_trades == initial_trades + 1
        assert bot.winning_trades == initial_winners + 1


class TestPositionMonitoring:
    """Test position monitoring for exits."""

    @pytest.mark.asyncio
    async def test_monitor_positions_checks_exits(self, bot):
        """Test that position monitoring checks exit conditions."""
        # Create position
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        bot.sim_order_manager.create_market_order = AsyncMock(
            return_value=_filled_order("48000", "0.01")
        )
        bot._get_current_price = AsyncMock(
            return_value=Decimal("48000.0")
        )  # Trigger stop loss
        bot._get_current_atr = AsyncMock(return_value=Decimal("2000.0"))

        # Mock position tracker to return position as-is
        with patch.object(
            bot.position_tracker, "update_all_metrics", side_effect=lambda p, price: p
        ):
            # Monitor positions (should trigger exit) - patch where it's used, not where it's defined
            with patch("bot_v2.bot.ExitConditionEngine") as mock_engine:
                mock_instance = mock_engine.return_value
                exit_condition = MagicMock()
                exit_condition.reason = "ATR_STOP_LOSS"
                exit_condition.priority = 80
                exit_condition.amount = Decimal("0.01")
                exit_condition.price = Decimal("48000.0")
                exit_condition.message = "Test stop loss"
                exit_condition.name = "ATR_STOP_LOSS"
                mock_instance.evaluate_all_exits.return_value = exit_condition
                await bot._monitor_positions()

        # Position should be exited
        assert "BTCUSDT" not in bot.positions
        assert len(bot.positions) == 0


class TestStatusMessage:
    """Test status message generation."""

    @pytest.mark.asyncio
    async def test_status_with_no_positions(self, bot):
        """Test status message with no active positions."""
        status = await bot.get_status_message()

        assert "Active Positions: None" in status
        assert "SIMULATION" in status
        # No longer check for performance stats since they're removed

    @pytest.mark.asyncio
    async def test_status_with_positions(self, bot):
        """Test status message with active positions."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position
        position.unrealized_pnl = Decimal("10.0")

        status = await bot.get_status_message()

        assert "Active Positions: 1" in status
        assert "BTCUSDT" in status
        assert "long" in status  # side is lowercase in enum value


class TestBotLifecycle:
    """Test bot run and stop lifecycle."""

    @pytest.mark.asyncio
    async def test_bot_run_sets_is_running(self, bot):
        """Test that run() sets is_running flag."""

        # Mock the main loop to exit immediately
        async def mock_sleep(duration):
            bot.is_running = False

        with patch("asyncio.sleep", side_effect=mock_sleep):
            await bot.run()

        # is_running should be False after stopping
        assert bot.is_running is False

    @pytest.mark.asyncio
    async def test_bot_stop_persists_state(self, bot):
        """Test that stop() persists state."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        await bot.stop()

        # Verify state was saved
        loaded_positions = bot.state_manager.load_positions()
        assert loaded_positions is not None
        assert len(loaded_positions) == 1
        assert "BTCUSDT" in loaded_positions


class TestStatePersistence:
    """Test state persistence."""

    @pytest.mark.asyncio
    async def test_persist_state_saves_positions(self, bot):
        """Test that _persist_state saves positions."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot.positions["BTCUSDT"] = position

        await bot._persist_state()

        # Load and verify
        loaded_positions = bot.state_manager.load_positions()
        assert len(loaded_positions) == 1
        assert "BTCUSDT" in loaded_positions
        assert loaded_positions["BTCUSDT"].symbol_id == "BTCUSDT"

    @pytest.mark.asyncio
    async def test_persist_state_saves_grid_exposure_snapshot(self, bot):
        """Test that _persist_state writes grid exposure runtime values."""
        orchestrator = MagicMock()
        orchestrator.is_active = True
        orchestrator.centre_price = Decimal("50000")
        orchestrator.grid_order_ids = {"order-1", "order-2"}
        orchestrator.session_fill_count = 3
        orchestrator.session_buy_qty = Decimal("0.020")
        orchestrator.session_sell_qty = Decimal("0.015")
        orchestrator.session_realized_pnl_quote = Decimal("12.5")
        bot.grid_orchestrators = {"BTCUSDT": orchestrator}

        await bot._persist_state()

        with open(bot.state_manager.grid_exposure_file, "r", encoding="utf-8") as f:
            exposure = json.load(f)

        assert "BTCUSDT" in exposure
        snapshot = exposure["BTCUSDT"]
        assert snapshot["is_active"] is True
        assert snapshot["centre_price"] == "50000"
        assert snapshot["open_order_count"] == 2
        assert snapshot["session_fill_count"] == 3
        assert snapshot["session_buy_qty"] == "0.020"
        assert snapshot["session_sell_qty"] == "0.015"
        assert snapshot["session_realized_pnl_quote"] == "12.5"

    @pytest.mark.asyncio
    async def test_persist_state_includes_inactive_grid_session(self, bot):
        """Test that stopped grid sessions are still persisted with is_active=False."""
        orchestrator = MagicMock()
        orchestrator.is_active = False
        orchestrator.centre_price = Decimal("50000")
        orchestrator.grid_order_ids = set()
        orchestrator.order_metadata = {}
        orchestrator.session_fill_count = 4
        orchestrator.session_buy_qty = Decimal("0")
        orchestrator.session_sell_qty = Decimal("0.010")
        orchestrator.session_realized_pnl_quote = Decimal("15.0")
        bot.grid_orchestrators = {"BTCUSDT": orchestrator}

        await bot._persist_state()

        grid_states = bot.state_manager.load_grid_states()
        assert "BTCUSDT" in grid_states
        persisted = grid_states["BTCUSDT"]
        assert persisted.is_active is False
        assert persisted.active_orders == {}
        assert persisted.grid_fills == 4


class TestExitOrderRetry:
    """Test exit order retry mechanism."""

    @pytest.fixture
    def mock_order_manager(self):
        """Create mock order manager."""
        manager = MagicMock()
        manager.create_market_order = AsyncMock()
        return manager

    @pytest.fixture
    async def bot_with_mocks(self, mock_config, temp_data_dir, mock_order_manager):
        """Create bot with mocked order manager."""
        mock_config.data_dir = temp_data_dir
        bot = TradingBot({"TEST/USDT": mock_config})
        bot._get_order_manager_for_symbol = MagicMock(return_value=mock_order_manager)
        bot._get_config = MagicMock(return_value=MagicMock())
        bot._get_current_price = AsyncMock(return_value=Decimal("50000.0"))
        bot._get_exchange_position = AsyncMock(return_value=Decimal("0.01"))
        bot.capital_manager.update_capital = AsyncMock()
        bot._update_performance_metrics = MagicMock()
        bot._check_tier_transition = AsyncMock()
        bot._send_status_to_generator = AsyncMock()
        bot._send_exit_notification = AsyncMock()
        yield bot
        await bot.shutdown()

    @pytest.mark.asyncio
    async def test_exit_order_retry_success_on_first_attempt(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test successful exit on first attempt."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)

        # Mock successful order
        mock_order_manager.create_market_order.return_value = {
            "id": "order-123",
            "filled": 0.01,
            "average": 50000.0,
            "remaining": 0,
        }

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order once
        assert mock_order_manager.create_market_order.call_count == 1
        # Position should be removed
        assert "BTCUSDT" not in bot_with_mocks.positions

    @pytest.mark.asyncio
    async def test_exit_order_retry_success_on_second_attempt(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test successful exit after first failure."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)

        # Mock first call fails, second succeeds
        mock_order_manager.create_market_order.side_effect = [
            None,  # First attempt fails
            {"id": "order-456", "filled": 0.01, "average": 50000.0, "remaining": 0},
        ]

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order twice
        assert mock_order_manager.create_market_order.call_count == 2
        # Position should be removed
        assert "BTCUSDT" not in bot_with_mocks.positions

    @pytest.mark.asyncio
    async def test_exit_order_retry_fails_all_attempts(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test exit fails after all retry attempts."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot_with_mocks.positions["BTCUSDT"] = position  # Add position to bot

        # Mock all attempts fail
        mock_order_manager.create_market_order.return_value = None

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order 3 times (default max_retries)
        assert mock_order_manager.create_market_order.call_count == 3
        # Position should remain (not removed)
        assert "BTCUSDT" in bot_with_mocks.positions

    @pytest.mark.asyncio
    async def test_exit_order_retry_with_zero_fill(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test exit treats zero fill as failure and retries."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)

        # Mock order returns but with zero fill
        mock_order_manager.create_market_order.side_effect = [
            {"id": "order-123", "filled": 0, "average": 0},  # Zero fill
            {"id": "order-456", "filled": 0.01, "average": 50000.0, "remaining": 0},
        ]

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order twice
        assert mock_order_manager.create_market_order.call_count == 2
        # Position should be removed
        assert "BTCUSDT" not in bot_with_mocks.positions

    @pytest.mark.asyncio
    async def test_exit_order_retry_exception_handling(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test exit handles exceptions and retries."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)

        # Mock first call raises exception, second succeeds
        mock_order_manager.create_market_order.side_effect = [
            Exception("Network error"),  # First attempt fails
            {"id": "order-456", "filled": 0.01, "average": 50000.0, "remaining": 0},
        ]

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order twice
        assert mock_order_manager.create_market_order.call_count == 2
        # Position should be removed
        assert "BTCUSDT" not in bot_with_mocks.positions

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"EXIT_ORDER_MAX_RETRIES": "5"})
    async def test_exit_order_retry_custom_max_retries(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test exit respects custom max retries from env var."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)
        bot_with_mocks.positions["BTCUSDT"] = position  # Add position to bot

        # Mock all attempts fail
        mock_order_manager.create_market_order.return_value = None

        await bot_with_mocks._exit_position(position, "TestExit")

        # Should call create_market_order 5 times
        assert mock_order_manager.create_market_order.call_count == 5
        # Position should remain
        assert "BTCUSDT" in bot_with_mocks.positions

    @pytest.mark.asyncio
    async def test_exit_order_retry_exponential_backoff(
        self, bot_with_mocks, mock_order_manager
    ):
        """Test that retry uses exponential backoff delays."""
        position = create_test_position("BTCUSDT", PositionSide.LONG, 50000.0)

        # Mock all attempts fail
        mock_order_manager.create_market_order.return_value = None

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep:
            await bot_with_mocks._exit_position(position, "TestExit")

            # Should sleep twice (after first and second attempts)
            assert mock_sleep.call_count == 2
            # Check delays: 1s, 2s
            mock_sleep.assert_any_call(1.0)
            mock_sleep.assert_any_call(2.0)
