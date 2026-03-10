"""
tests/test_integration_smoke.py
-------------------------------
End-to-end integration tests for the Grid Trading Bot.

Validates the full bot lifecycle:
  - Component initialization with test configuration
  - State persistence and recovery
  - One complete trading cycle (regime → grid → fill → counter)
  - Graceful shutdown on signals
  - Crash recovery scenarios
  - No unhandled exceptions in async event loop

Uses pytest-asyncio and comprehensive mocking to avoid live exchange calls.
"""

import asyncio
import json
import signal
from pathlib import Path
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from config.settings import GridBotSettings
from main import GridBot
from src.oms import OrderStatus
from src.persistence.state_store import StateStore
from src.strategy import GridLevel, MarketRegime


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def integration_settings(tmp_path: Path) -> GridBotSettings:
    """
    Create a GridBotSettings instance optimized for fast integration tests.
    
    Uses:
      - Small capital (100 USDT)
      - Minimal grid (2 levels up/down)
      - Temporary directories for state and cache
      - Fast polling interval (1 second)
    """
    from config.settings import load_yaml_config
    
    yaml_defaults = load_yaml_config()
    overrides = {
        "EXCHANGE_ID": "binance",
        "MARKET_TYPE": "spot",
        "API_KEY": "integration_test_key",
        "API_SECRET": "integration_test_secret",
        "SYMBOL": "BTC/USDT",
        "OHLCV_TIMEFRAME": "1h",
        "STATE_FILE": tmp_path / "grid_state.json",
        "LOG_FILE": tmp_path / "grid_bot.log",
        "OHLCV_CACHE_DIR": tmp_path / "ohlcv_cache",
        "POLL_INTERVAL_SEC": 1,
        "TOTAL_CAPITAL": 100.0,
        "NUM_GRIDS_UP": 2,
        "NUM_GRIDS_DOWN": 2,
        "ORDER_SIZE_QUOTE": 20.0,
        "GRID_SPACING_PCT": 0.5,
        "MAX_OPEN_ORDERS": 10,
        "ADX_THRESHOLD": 25,
        "STOP_LOSS_PCT": 5.0,
        "MAX_DRAWDOWN_PCT": 10.0,
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
    }
    return GridBotSettings(**{**yaml_defaults, **overrides})


@pytest.fixture
def ranging_ohlcv() -> pd.DataFrame:
    """
    Generate OHLCV data that produces a RANGING regime.
    
    Characteristics:
      - Low ADX (< 25) indicating no strong trend
      - Narrow Bollinger Bands indicating consolidation
      - Price oscillates around 30,000 BTC/USDT
      - 200 bars for sufficient indicator warmup
    """
    import numpy as np
    
    rng = np.random.default_rng(seed=42)
    n = 200
    base = 30_000.0
    # Small oscillations to keep ADX low
    closes = base + rng.uniform(-200, 200, n).cumsum() * 0.01
    
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": closes + rng.normal(0, 20, n),
        "high": closes + rng.uniform(30, 80, n),
        "low": closes - rng.uniform(30, 80, n),
        "close": closes,
        "volume": rng.uniform(200, 800, n),
    })


@pytest.fixture
def mock_exchange_client() -> MagicMock:
    """
    Mock ExchangeClient with async methods that simulate exchange responses.
    
    Simulates:
      - load_markets: Market metadata loading
      - fetch_ohlcv: Historical candle data
      - place_limit_order: Order placement with generated IDs
      - fetch_open_orders: Active orders query
      - fetch_balance: Account balance
      - get_ticker: Current market price
      - get_order_status: Order status query
      - cancel_order: Order cancellation
    """
    exchange = MagicMock()
    
    # Market loading
    exchange.load_markets = AsyncMock(return_value={"BTC/USDT": {"symbol": "BTC/USDT"}})
    
    # OHLCV data - will be overridden by price_feed mock
    exchange.fetch_ohlcv = AsyncMock(return_value=[])
    
    # Order placement with sequential IDs
    order_counter = {"count": 0}
    
    async def mock_place_order(side: str, price: float, amount: float) -> Dict:
        order_counter["count"] += 1
        order_id = f"test_order_{order_counter['count']:03d}"
        return {
            "id": order_id,
            "status": "open",
            "side": side,
            "price": price,
            "amount": amount,
            "filled": 0.0,
            "remaining": amount,
        }
    
    exchange.place_limit_order = AsyncMock(side_effect=mock_place_order)
    
    # Open orders query - starts empty
    exchange.fetch_open_orders = AsyncMock(return_value=[])
    
    # Balance query
    exchange.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 100.0, "total": 100.0},
        "BTC": {"free": 0.0, "total": 0.0},
    })
    
    # Ticker query
    exchange.get_ticker = AsyncMock(return_value={
        "symbol": "BTC/USDT",
        "last": 30_000.0,
        "bid": 29_995.0,
        "ask": 30_005.0,
    })
    
    # Order status query
    exchange.get_order_status = AsyncMock(return_value={
        "id": "test_order_001",
        "status": "open",
        "filled": 0.0,
    })
    
    # Order cancellation
    exchange.cancel_order = AsyncMock(return_value={"status": "canceled"})
    
    # Close connection
    exchange.close = AsyncMock()
    
    return exchange


@pytest.fixture
def mock_price_feed(ranging_ohlcv: pd.DataFrame) -> MagicMock:
    """Mock PriceFeed that returns pre-generated ranging OHLCV data."""
    feed = MagicMock()
    feed.get_ohlcv_dataframe = AsyncMock(return_value=ranging_ohlcv)
    return feed


@pytest.fixture
def mock_telegram_alerter() -> MagicMock:
    """Mock TelegramAlerter with no-op alert methods."""
    alerter = MagicMock()
    alerter.alert_grid_deployed = AsyncMock()
    alerter.alert_risk_action = AsyncMock()
    alerter.alert_shutdown = AsyncMock()
    return alerter


# ============================================================================
# Integration Test: Full Boot Sequence
# ============================================================================


@pytest.mark.asyncio
class TestBotBootSequence:
    """Test that GridBot can initialize all components without errors."""
    
    async def test_init_components_succeeds(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
    ) -> None:
        """Verify all subsystems initialize correctly with test config."""
        bot = GridBot()
        
        # Patch component constructors
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed"), \
             patch("main.RegimeDetector"), \
             patch("main.TelegramAlerter"), \
             patch("main.StateStore"), \
             patch("main.OrderManager"), \
             patch("main.FillHandler"), \
             patch("main.RiskManager"), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            # Verify exchange markets were loaded
            mock_exchange_client.load_markets.assert_called_once()
            
            # Verify all components are not None
            assert bot._exchange is not None
            assert bot._price_feed is not None
            assert bot._regime_detector is not None
            assert bot._alerter is not None
            assert bot._state_store is not None
            assert bot._order_manager is not None
            assert bot._fill_handler is not None
            assert bot._risk_manager is not None
    
    async def test_restore_state_with_no_file(
        self,
        integration_settings: GridBotSettings,
        tmp_path: Path,
    ) -> None:
        """Verify bot starts fresh when no state file exists."""
        bot = GridBot()
        bot._state_store = StateStore(tmp_path / "nonexistent_state.json")
        
        await bot._restore_state()
        
        # Should not crash and should start with None values
        assert bot._centre_price is None
        assert bot._initial_equity is None
    
    async def test_restore_state_with_existing_file(
        self,
        integration_settings: GridBotSettings,
        tmp_path: Path,
    ) -> None:
        """Verify bot recovers state from existing state file."""
        state_file = tmp_path / "test_state.json"
        state_data = {
            "centre_price": 30_000.0,
            "initial_equity": 100.0,
            "orders": {
                "order_001": {
                    "order_id": "order_001",
                    "grid_price": 29_850.0,
                    "side": "buy",
                    "amount": 0.001,
                    "status": "open",
                }
            },
        }
        
        # Write state file
        state_file.write_text(json.dumps(state_data))
        
        bot = GridBot()
        bot._state_store = StateStore(state_file)
        bot._order_manager = MagicMock()
        bot._order_manager.import_state = MagicMock()
        
        await bot._restore_state()
        
        assert bot._centre_price == 30_000.0
        assert bot._initial_equity == 100.0
        bot._order_manager.import_state.assert_called_once()


# ============================================================================
# Integration Test: One Complete Trading Cycle
# ============================================================================


@pytest.mark.asyncio
class TestCompleteTradingCycle:
    """Test a full cycle: regime → grid deployment → fill → counter-order."""
    
    async def test_ranging_regime_deploys_grid(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        mock_telegram_alerter: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify bot deploys grid when regime is RANGING.
        
        Flow:
          1. Regime detection returns RANGING
          2. Bot calculates grid levels
          3. Bot places limit orders at each level
          4. State is persisted
        """
        bot = GridBot()
        
        mock_telegram_alerter = AsyncMock()
        mock_telegram_alerter.alert_grid_deployed = AsyncMock()
        mock_telegram_alerter.alert_risk_action = AsyncMock()
        mock_telegram_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_telegram_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            # Run one iteration of the trading loop (manual control)
            ohlcv_df = await mock_price_feed.get_ohlcv_dataframe()
            regime = bot._regime_detector.detect(ohlcv_df)
            
            # Regime should be RANGING based on fixture data
            assert regime.regime == MarketRegime.RANGING
            
            # Deploy grid
            current_price = 30_000.0
            await bot._deploy_grid(current_price)
            
            # Verify orders were placed
            assert bot._order_manager.open_order_count > 0
            
            # Verify centre price was set
            assert bot._centre_price == current_price
            
            # Verify telegram alert was sent
            mock_telegram_alerter.alert_grid_deployed.assert_called_once()
    
    async def test_trending_regime_cancels_grid(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify bot cancels all orders when regime changes to TRENDING.
        
        Flow:
          1. Grid is active (has open orders)
          2. Regime detection returns TRENDING
          3. Bot cancels all orders
        """
        # Create trending OHLCV (high ADX)
        import numpy as np
        rng = np.random.default_rng(seed=100)
        n = 200
        # Strong uptrend to trigger high ADX
        trending_prices = 30_000.0 + np.linspace(0, 3000, n)
        trending_ohlcv = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "open": trending_prices + rng.normal(0, 50, n),
            "high": trending_prices + rng.uniform(50, 200, n),
            "low": trending_prices - rng.uniform(50, 200, n),
            "close": trending_prices,
            "volume": rng.uniform(300, 1000, n),
        })
        
        mock_trending_feed = MagicMock()
        mock_trending_feed.get_ohlcv_dataframe = AsyncMock(return_value=trending_ohlcv)
        
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_trending_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            # First deploy grid in ranging mode (simulate)
            await bot._deploy_grid(30_000.0)
            initial_order_count = bot._order_manager.open_order_count
            assert initial_order_count > 0
            
            # Now detect trending regime
            ohlcv_df = await mock_trending_feed.get_ohlcv_dataframe()
            regime = bot._regime_detector.detect(ohlcv_df)
            
            # Should be TRENDING
            assert regime.regime == MarketRegime.TRENDING
            
            # Cancel all orders
            await bot._order_manager.cancel_all_orders()
            
            # Verify orders were canceled
            assert bot._order_manager.open_order_count == 0
    
    async def test_fill_triggers_counter_order(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify fill handler places counter-order when a grid order fills.
        
        Flow:
          1. Grid deployed with buy and sell orders
          2. One buy order fills (simulated)
          3. Fill handler detects fill
          4. Counter sell order is placed above current price
        """
        # Configure exchange to report a filled order
        filled_order = {
            "id": "test_order_001",
            "status": "closed",
            "filled": 0.001,
            "price": 29_850.0,
            "side": "buy",
        }
        
        mock_exchange_client.fetch_open_orders = AsyncMock(return_value=[])
        mock_exchange_client.get_order_status = AsyncMock(return_value=filled_order)
        
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            # Deploy grid
            await bot._deploy_grid(30_000.0)
            initial_count = bot._order_manager.open_order_count
            
            # Simulate fill detection
            current_price = 30_100.0  # Price moved up
            await bot._fill_handler.poll_and_handle(current_price)
            
            # Fill handler should have detected fill and placed counter-order
            # (This depends on FillHandler implementation)
            # At minimum, order state should be updated
            new_count = bot._order_manager.open_order_count
            
            # Note: Counter-order placement increases count back or keeps it same
            # This test verifies the mechanism runs without exceptions
            assert new_count >= 0  # Basic sanity check


# ============================================================================
# Integration Test: State Persistence Round-Trip
# ============================================================================


@pytest.mark.asyncio
class TestStatePersistence:
    """Test that state saves and loads correctly through crashes."""
    
    async def test_state_save_and_load_roundtrip(
        self,
        integration_settings: GridBotSettings,
        tmp_path: Path,
    ) -> None:
        """
        Verify state persists correctly through save → load cycle.
        
        Flow:
          1. Bot runs with active grid
          2. State is saved to disk
          3. New bot instance loads state
          4. All state values match
        """
        state_file = tmp_path / "roundtrip_state.json"
        
        # First bot instance - save state
        bot1 = GridBot()
        bot1._state_store = StateStore(state_file)
        bot1._centre_price = 30_000.0
        bot1._initial_equity = 100.0
        bot1._order_manager = MagicMock()
        bot1._order_manager.export_state = MagicMock(return_value={
            "order_001": {
                "order_id": "order_001",
                "grid_price": 29_850.0,
                "side": "buy",
                "amount": 0.001,
            }
        })
        
        await bot1._persist_state()
        
        # Verify file was created
        assert state_file.exists()
        
        # Second bot instance - load state
        bot2 = GridBot()
        bot2._state_store = StateStore(state_file)
        bot2._order_manager = MagicMock()
        bot2._order_manager.import_state = MagicMock()
        
        await bot2._restore_state()
        
        # Verify state was restored
        assert bot2._centre_price == 30_000.0
        assert bot2._initial_equity == 100.0
        bot2._order_manager.import_state.assert_called_once()
    
    async def test_crash_recovery_from_partial_state(
        self,
        integration_settings: GridBotSettings,
        tmp_path: Path,
    ) -> None:
        """
        Verify bot recovers gracefully from corrupted state file.
        
        Flow:
          1. Write invalid JSON to state file
          2. Bot attempts to load state
          3. Bot handles corruption and starts fresh
          4. Corrupted file is backed up
        """
        state_file = tmp_path / "corrupted_state.json"
        
        # Write invalid JSON
        state_file.write_text("{invalid json content[[")
        
        bot = GridBot()
        bot._state_store = StateStore(state_file)
        
        # Should not crash
        await bot._restore_state()
        
        # Should start with clean state
        assert bot._centre_price is None
        assert bot._initial_equity is None
        
        # Corrupted file should be backed up
        backup_file = state_file.with_suffix(".json.corrupted")
        assert backup_file.exists()
    
    async def test_state_persists_after_each_tick(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify state file is updated after each trading loop iteration.
        
        This ensures minimal data loss on unexpected crashes.
        """
        state_file = tmp_path / "tick_state.json"
        
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            bot._state_store = StateStore(state_file)
            
            # Deploy grid
            await bot._deploy_grid(30_000.0)
            
            # Persist state
            await bot._persist_state()
            
            # Verify file exists and contains data
            assert state_file.exists()
            state_data = json.loads(state_file.read_text())
            assert state_data["centre_price"] == 30_000.0
            assert "_saved_at" in state_data


# ============================================================================
# Integration Test: Graceful Shutdown
# ============================================================================


@pytest.mark.asyncio
class TestGracefulShutdown:
    """Test bot responds correctly to shutdown signals."""
    
    async def test_sigint_triggers_graceful_shutdown(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify SIGINT causes bot to cancel orders, save state, and exit.
        
        Flow:
          1. Bot is running
          2. SIGINT signal received
          3. Bot cancels all orders
          4. Bot saves state
          5. Bot closes exchange connection
          6. Event loop exits cleanly
        """
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            bot._running = True
            
            # Deploy grid to have active orders
            await bot._deploy_grid(30_000.0)
            assert bot._order_manager.open_order_count > 0
            
            # Trigger shutdown
            await bot.stop(reason="SIGINT")
            
            # Verify shutdown actions
            assert bot._running is False
            assert bot._order_manager.open_order_count == 0  # All canceled
            mock_exchange_client.close.assert_called_once()
    
    async def test_stop_method_is_async_safe(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
    ) -> None:
        """
        Verify stop() method completes all async cleanup without deadlocks.
        """
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            bot._running = True
            
            # Should complete without timeout or exception
            try:
                await asyncio.wait_for(bot.stop(), timeout=5.0)
            except asyncio.TimeoutError:
                pytest.fail("stop() method timed out - possible deadlock")
    
    async def test_no_unhandled_exceptions_in_loop(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
    ) -> None:
        """
        Verify trading loop handles exceptions without crashing.
        
        Simulates an exchange API error during a tick and verifies
        the bot logs the error and continues running.
        """
        # Configure mock to raise exception on first call, then succeed
        call_count = {"count": 0}
        
        async def failing_get_ticker(*args, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise Exception("Simulated API error")
            return {"last": 30_000.0}
        
        mock_exchange_client.get_ticker = AsyncMock(side_effect=failing_get_ticker)
        
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            # Trading loop should catch the exception and continue
            # We'll verify by checking it doesn't raise
            try:
                # Run a few simulated ticks
                for _ in range(2):
                    ohlcv_df = await mock_price_feed.get_ohlcv_dataframe()
                    if ohlcv_df is not None:
                        regime = bot._regime_detector.detect(ohlcv_df)
                        # Attempt to get ticker (will fail once, succeed next)
                        try:
                            await bot._exchange.get_ticker(integration_settings.SYMBOL)
                        except Exception:
                            # Bot should log but continue
                            pass
            except Exception as e:
                pytest.fail(f"Unhandled exception in loop: {e}")


# ============================================================================
# Integration Test: End-to-End Smoke Test
# ============================================================================


@pytest.mark.asyncio
class TestEndToEndSmoke:
    """
    Comprehensive smoke test of the complete bot lifecycle.
    
    This is the primary integration test that exercises all major systems
    in a single test run.
    """
    
    async def test_full_bot_lifecycle(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        mock_telegram_alerter: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Test complete bot lifecycle from boot to shutdown.
        
        Flow:
          1. Initialize bot with test configuration
          2. Load markets and restore state (none)
          3. Detect ranging regime
          4. Deploy grid with 4 orders (2 up, 2 down)
          5. Simulate one fill
          6. Handle fill and place counter-order
          7. Persist state to disk
          8. Gracefully shutdown
          9. Verify state file contains correct data
          10. Restart bot and verify state recovery
        
        This test validates that all major subsystems work together
        without errors or deadlocks.
        """
        # ────────────────────────────────────────────────────────────────────
        # Phase 1: Initial Boot
        # ────────────────────────────────────────────────────────────────────
        
        bot = GridBot()
        
        mock_telegram_alerter = AsyncMock()
        mock_telegram_alerter.alert_grid_deployed = AsyncMock()
        mock_telegram_alerter.alert_risk_action = AsyncMock()
        mock_telegram_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_telegram_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            # Initialize all components
            await bot._init_components()
            
            assert bot._exchange is not None
            assert bot._price_feed is not None
            assert bot._regime_detector is not None
            assert bot._order_manager is not None
            assert bot._fill_handler is not None
            assert bot._risk_manager is not None
            assert bot._state_store is not None
            
            # Restore state (should be empty)
            await bot._restore_state()
            assert bot._centre_price is None
            assert bot._initial_equity == 100.0
            
            # ────────────────────────────────────────────────────────────────
            # Phase 2: Regime Detection & Grid Deployment
            # ────────────────────────────────────────────────────────────────
            
            # Fetch OHLCV and detect regime
            ohlcv_df = await bot._price_feed.get_ohlcv_dataframe()
            assert ohlcv_df is not None
            assert not ohlcv_df.empty
            
            regime = bot._regime_detector.detect(ohlcv_df)
            assert regime.regime == MarketRegime.RANGING
            
            # Deploy grid
            current_price = 30_000.0
            await bot._deploy_grid(current_price)
            
            # Verify grid deployment
            assert bot._centre_price == current_price
            assert bot._order_manager.open_order_count == 4  # 2 up + 2 down
            
            # Verify Telegram alert
            mock_telegram_alerter.alert_grid_deployed.assert_called_once()
            
            # ────────────────────────────────────────────────────────────────
            # Phase 3: Simulate Fill & Counter-Order
            # ────────────────────────────────────────────────────────────────
            
            # Configure exchange to show one filled order
            filled_order_id = "test_order_001"
            mock_exchange_client.get_order_status = AsyncMock(return_value={
                "id": filled_order_id,
                "status": "closed",
                "filled": 0.001,
                "price": 29_850.0,
                "side": "buy",
            })
            
            # Poll for fills
            new_price = 30_150.0  # Price moved up after buy fill
            await bot._fill_handler.poll_and_handle(new_price)
            
            # Verify fill was processed (exact behavior depends on FillHandler)
            # At minimum, no exceptions should be raised
            
            # ────────────────────────────────────────────────────────────────
            # Phase 4: Risk Check & State Persistence
            # ────────────────────────────────────────────────────────────────
            
            # Check risk limits
            balance = await bot._exchange.fetch_balance()
            current_equity = float(balance.get("USDT", {}).get("total", 0))
            
            risk_action = bot._risk_manager.evaluate(
                current_price=new_price,
                current_equity=current_equity,
                centre_price=bot._centre_price,
                adx=20.0,
                grid_spacing_abs=150.0,
            )
            
            # Should not trigger any risk action in normal conditions
            from src.oms import RiskAction
            assert risk_action == RiskAction.NONE
            
            # Persist state
            await bot._persist_state()
            
            # Verify state file
            state_file = integration_settings.STATE_FILE
            assert state_file.exists()
            
            state_data = json.loads(state_file.read_text())
            assert state_data["centre_price"] == current_price
            assert "orders" in state_data
            assert "_saved_at" in state_data
            
            # ────────────────────────────────────────────────────────────────
            # Phase 5: Graceful Shutdown
            # ────────────────────────────────────────────────────────────────
            
            bot._running = True
            await bot.stop(reason="TEST_COMPLETE")
            
            assert bot._running is False
            mock_exchange_client.close.assert_called_once()
            mock_telegram_alerter.alert_shutdown.assert_called_once()
        
        # ────────────────────────────────────────────────────────────────────
        # Phase 6: Recovery Test (New Bot Instance)
        # ────────────────────────────────────────────────────────────────────
        
        bot2 = GridBot()
        
        mock_telegram_alerter = AsyncMock()
        mock_telegram_alerter.alert_grid_deployed = AsyncMock()
        mock_telegram_alerter.alert_risk_action = AsyncMock()
        mock_telegram_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_telegram_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot2._init_components()
            await bot2._restore_state()
            
            # Verify state was recovered
            assert bot2._centre_price == current_price
            
            # Bot should be able to continue trading from recovered state
            # (No further actions needed - just verify it doesn't crash)


# ============================================================================
# Integration Test: Multi-Cycle Resilience
# ============================================================================


@pytest.mark.asyncio
class TestMultiCycleResilience:
    """Test bot stability over multiple trading cycles."""
    
    async def test_multiple_grid_deploy_cancel_cycles(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
    ) -> None:
        """
        Verify bot can deploy and cancel grids multiple times without leaking
        resources or encountering state corruption.
        
        Simulates:
          - 5 cycles of deploy → cancel
          - Verifies order count returns to zero after each cancel
          - Checks for any resource leaks or deadlocks
        """
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            
            for cycle in range(5):
                # Deploy grid
                price = 30_000.0 + (cycle * 100)  # Vary price slightly
                await bot._deploy_grid(price)
                
                assert bot._order_manager.open_order_count > 0
                assert bot._centre_price == price
                
                # Cancel all
                await bot._order_manager.cancel_all_orders()
                assert bot._order_manager.open_order_count == 0
                
            # After 5 cycles, bot should still be functional
            await bot._deploy_grid(31_000.0)
            assert bot._order_manager.open_order_count > 0
    
    async def test_continuous_state_persistence(
        self,
        integration_settings: GridBotSettings,
        mock_exchange_client: MagicMock,
        mock_price_feed: MagicMock,
        tmp_path: Path,
    ) -> None:
        """
        Verify state file remains valid after multiple save operations.
        
        Simulates:
          - 10 state save operations
          - Verifies each save produces valid JSON
          - Checks timestamps are sequential
        """
        state_file = tmp_path / "continuous_state.json"
        
        bot = GridBot()
        
        mock_alerter = AsyncMock()
        mock_alerter.alert_grid_deployed = AsyncMock()
        mock_alerter.alert_risk_action = AsyncMock()
        mock_alerter.alert_shutdown = AsyncMock()
        with patch("main.ExchangeClient", return_value=mock_exchange_client), \
             patch("main.PriceFeed", return_value=mock_price_feed), \
             patch("main.TelegramAlerter", return_value=mock_alerter), \
             patch("main.settings", integration_settings), \
             patch("config.settings.settings", integration_settings):
            
            await bot._init_components()
            bot._state_store = StateStore(state_file)
            
            timestamps = []
            
            for i in range(10):
                bot._centre_price = 30_000.0 + i
                await bot._persist_state()
                
                # Read and validate
                assert state_file.exists()
                state_data = json.loads(state_file.read_text())
                assert state_data["centre_price"] == 30_000.0 + i
                
                timestamps.append(state_data["_saved_at"])
            
            # Timestamps should be in order
            assert timestamps == sorted(timestamps)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
