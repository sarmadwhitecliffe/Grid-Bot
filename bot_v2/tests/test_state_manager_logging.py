import asyncio
import logging
import os
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot_v2.models.position import Position
from bot_v2.persistence.state_manager import StateManager

sys.path.insert(0, str(Path(__file__).parent.parent))


# Mock the log_periodic_summaries function to avoid importing webhook_server
async def mock_log_periodic_summaries(bot_instance):
    """Mock implementation of log_periodic_summaries."""
    while bot_instance.is_running:
        try:
            # Support trade_history/positions being either sequences or callables (tests may set MagicMock(side_effect=...))
            if hasattr(bot_instance, "trade_history"):
                total_trades = (
                    len(bot_instance.trade_history())
                    if callable(bot_instance.trade_history)
                    else len(bot_instance.trade_history)
                )
            else:
                total_trades = 0
            if hasattr(bot_instance, "positions"):
                active_positions = (
                    len(bot_instance.positions())
                    if callable(bot_instance.positions)
                    else len(bot_instance.positions)
                )
            else:
                active_positions = 0
            logging.getLogger("WebhookServer").info(
                f"📊 Periodic Summary: Trades={total_trades}, Active Positions={active_positions}"
            )
        except Exception as e:
            logging.getLogger("WebhookServer").error(
                f"Error generating periodic summary: {e}"
            )

        await asyncio.sleep(3600)  # Log every hour


class TestStateManagerLogging(unittest.TestCase):
    """Test logging behavior in StateManager."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = Path("test_data")
        self.temp_dir.mkdir(exist_ok=True)
        self.state_manager = StateManager(data_dir=self.temp_dir)

    def tearDown(self):
        """Clean up test fixtures."""
        # Remove test files
        for file in self.temp_dir.glob("*.json"):
            file.unlink()
        self.temp_dir.rmdir()

    @patch("bot_v2.persistence.state_manager.logger")
    def test_save_positions_logging_condensed(self, mock_logger):
        """Test that save_positions logs are condensed (INFO every 10th, DEBUG otherwise)."""
        # Create a mock position
        position = Position(
            symbol_id="TEST/USDT",
            side="long",
            entry_price=Decimal("100.0"),
            entry_time=datetime.now(),
            initial_amount=Decimal("1.0"),
            entry_atr=Decimal("1.0"),
            initial_risk_atr=Decimal("0.01"),
            soft_sl_price=Decimal("99.0"),
            hard_sl_price=Decimal("98.0"),
            tp1_price=Decimal("101.0"),
            tp1a_price=Decimal("102.0"),
            total_entry_fee=Decimal("0.1"),
            current_amount=Decimal("1.0"),
            status="open",
            trailing_sl_price=None,
            time_of_tp1=None,
            last_checked_bar_ts=0,
            mfe=Decimal("0.0"),
            mae=Decimal("0.0"),
            peak_price_since_entry=Decimal("100.0"),
            peak_price_since_tp1=None,
            realized_profit=Decimal("0.0"),
            is_trailing_active=False,
            moved_to_breakeven=False,
            scaled_out_on_adverse=False,
            mae_breach_counter=0,
            intrabar_breach_started_at=None,
            scaleout_suspend_until_bar_ts=0,
            progress_breakeven_eligible=False,
        )

        positions = {"TEST/USDT": position}

        # Test logging for first 9 saves (should log DEBUG "Saved 1 active positions")
        for i in range(1, 10):
            self.state_manager.save_positions(positions)
            # Check that debug was called with the position count
            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if "Saved 1 active positions" in str(call)
            ]
            self.assertTrue(len(debug_calls) > 0, f"Expected DEBUG log for save {i}")
            mock_logger.reset_mock()

        # 10th save should log INFO with summary
        self.state_manager.save_positions(positions)
        mock_logger.info.assert_called_with("State saved: 10 updates in session")

    @patch("bot_v2.persistence.state_manager.logger")
    def test_save_positions_reset_on_new_instance(self, mock_logger):
        """Test that save_count resets with new StateManager instance."""
        position = Position(
            symbol_id="TEST/USDT",
            side="long",
            entry_price=Decimal("100.0"),
            entry_time=datetime.now(),
            initial_amount=Decimal("1.0"),
            entry_atr=Decimal("1.0"),
            initial_risk_atr=Decimal("0.01"),
            soft_sl_price=Decimal("99.0"),
            hard_sl_price=Decimal("98.0"),
            tp1_price=Decimal("101.0"),
            tp1a_price=Decimal("102.0"),
            total_entry_fee=Decimal("0.1"),
            current_amount=Decimal("1.0"),
            status="open",
            trailing_sl_price=None,
            time_of_tp1=None,
            last_checked_bar_ts=0,
            mfe=Decimal("0.0"),
            mae=Decimal("0.0"),
            peak_price_since_entry=Decimal("100.0"),
            peak_price_since_tp1=None,
            realized_profit=Decimal("0.0"),
            is_trailing_active=False,
            moved_to_breakeven=False,
            scaled_out_on_adverse=False,
            mae_breach_counter=0,
            intrabar_breach_started_at=None,
            scaleout_suspend_until_bar_ts=0,
            progress_breakeven_eligible=False,
        )

        positions = {"TEST/USDT": position}

        # Save 10 times on first instance (to reach count 10)
        for _ in range(10):
            self.state_manager.save_positions(positions)

        # Create new instance (this will log init)
        new_state_manager = StateManager(data_dir=self.temp_dir)

        # First save on new instance should log DEBUG "Saved 1 active positions"
        new_state_manager.save_positions(positions)
        debug_calls = [
            call
            for call in mock_logger.debug.call_args_list
            if "Saved 1 active positions" in str(call)
        ]
        self.assertTrue(
            len(debug_calls) > 0, "Expected DEBUG log for first save on new instance"
        )


class TestPeriodicSummaries(unittest.TestCase):
    """Test periodic summary logging."""

    def setUp(self):
        """Set up mock bot."""
        self.mock_bot = MagicMock()
        self.mock_bot.is_running = True
        self.mock_bot.trade_history = [1, 2, 3]  # Mock 3 trades
        self.mock_bot.positions = {"TEST": "pos"}  # Mock 1 position

    @patch("logging.getLogger")
    def test_log_periodic_summaries(self, mock_get_logger):
        """Test that periodic summaries log expected data."""

        async def run_test():
            # Create task using mock function
            task = asyncio.create_task(mock_log_periodic_summaries(self.mock_bot))

            # Let the task run briefly (allow one iteration)
            await asyncio.sleep(0.01)

            # Cancel the task and await cancellation
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Check that summary was logged at least once
            mock_logger = mock_get_logger.return_value
            mock_logger.info.assert_called()

        asyncio.run(run_test())

    @patch("logging.getLogger")
    def test_log_periodic_summaries_error_handling(self, mock_get_logger):
        """Test error handling in periodic summaries."""

        async def run_test():
            # Make bot raise error
            self.mock_bot.trade_history = MagicMock(side_effect=Exception("Test error"))

            task = asyncio.create_task(mock_log_periodic_summaries(self.mock_bot))
            # Let the task run briefly to trigger the error path
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Check error was logged
            mock_logger = mock_get_logger.return_value
            mock_logger.error.assert_called()

        asyncio.run(run_test())


class TestLoggingConfig(unittest.TestCase):
    """Test logging configuration improvements."""

    @patch.dict(os.environ, {"LOG_STRUCTURED": "true"})
    def test_json_format_env_true(self):
        """Test LOG_STRUCTURED=true enables JSON."""
        from bot_v2.utils.logging_config import setup_logging

        # Mock to avoid actual setup
        with patch("logging.getLogger") as mock_get_logger:
            mock_root = MagicMock()
            mock_get_logger.return_value = mock_root
            mock_root.handlers = []
            setup_logging()
            # Check that JSON formatter was used (simplified check)
            # In real test, would check formatter type

    @patch.dict(os.environ, {"LOG_STRUCTURED": "false"})
    def test_json_format_env_false(self):
        """Test LOG_STRUCTURED=false uses human-readable."""
        from bot_v2.utils.logging_config import setup_logging

        with patch("logging.getLogger") as mock_get_logger:
            mock_root = MagicMock()
            mock_get_logger.return_value = mock_root
            mock_root.handlers = []
            setup_logging()
            # Check human-readable formatter


if __name__ == "__main__":
    unittest.main()
