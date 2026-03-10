import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Import TradingBot - assuming it can be imported without side effects
# We might need to mock setup_logging or other module level calls if they run on import
from bot_v2.bot import TradingBot


class TestSecondTradeQualification:

    @pytest.fixture
    def mock_bot(self):
        with patch("bot_v2.bot.StateManager") as MockState, patch(
            "bot_v2.bot.CapitalManager"
        ) as MockCap, patch("bot_v2.bot.LiveExchange") as MockEx, patch(
            "bot_v2.bot.Notifier"
        ) as MockNot, patch(
            "bot_v2.bot.setup_logging"
        ):  # Prevent logging setup

            # We need to bypass __init__ because it does a lot of setup
            # Or we can just instantiate it and mock everything attached to self
            bot = TradingBot.__new__(TradingBot)
            bot.state_manager = MockState.return_value
            bot.capital_manager = MockCap.return_value
            bot.exchange = MockEx.return_value
            bot.notifier = MockNot.return_value
            bot.trade_history = []
            return bot

    def test_qualification_success_global(self, mock_bot):
        # Mock the inline import and file reading
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True), patch.object(
            mock_bot, "_count_daily_overrides", return_value=0
        ):

            # Setup position
            pos = MagicMock()
            pos.symbol_id = "BTCUSDT"
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

            # Setup history entry
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            # Setup state manager
            mock_bot.state_manager.make_day_key.return_value = "2023-10-27"
            mock_bot.state_manager.get_second_trade_override.return_value = None

            # Call method
            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            # Verify override set
            mock_bot.state_manager.set_second_trade_override.assert_called_once()
            call_args = mock_bot.state_manager.set_second_trade_override.call_args
            assert call_args[0][0] == "2023-10-27"  # day_key
            assert call_args[0][1] == "GLOBAL_1"  # scope_key with sequence number (first override)
            assert call_args[0][2]["reason"] == "TrailExit"
            assert call_args[0][2]["scope"] == "global"

    def test_qualification_fail_wrong_reason(self, mock_bot):
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            pos.entry_time = datetime.now(timezone.utc)
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "ManualExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_fail_negative_pnl(self, mock_bot):
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            pos.entry_time = datetime.now(timezone.utc)
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("-5.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_fail_too_long(self, mock_bot):
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            # 31 minutes ago
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=31)
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_fail_not_first_trade(self, mock_bot):
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            pos.entry_time = datetime.now(timezone.utc)
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            # Add a prior exit for today
            mock_bot.trade_history = [
                {"timestamp": datetime.now(timezone.utc), "type": "exit"},
                history_entry,  # The current one is appended before calling this method usually?
                # Wait, _add_trade_to_history appends it.
                # The method checks self.trade_history[:-1]
            ]
            # If _add_trade_to_history calls this, it has already appended the current trade.
            # So trade_history has [prior, current].
            # trade_history[:-1] is [prior].

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_success_per_symbol(self, mock_bot):
        # Test that per_symbol scope allows qualification even if other symbols traded
        # Also verify that slashed symbols (ETH/USDT) are normalized to ETHUSDT in scope_key
        config_data = {
            "enabled": True,
            "scope": "per_symbol",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True), patch.object(
            mock_bot, "_count_daily_overrides", return_value=0
        ):

            pos = MagicMock()
            pos.symbol_id = "ETH/USDT"  # Use slashed symbol
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

            history_entry = {"timestamp": datetime.now(timezone.utc)}

            # Add a prior exit for a DIFFERENT symbol
            mock_bot.trade_history = [
                {
                    "timestamp": datetime.now(timezone.utc),
                    "type": "exit",
                    "symbol": "BTC/USDT",
                },
                history_entry,
            ]

            mock_bot.state_manager.make_day_key.return_value = "2023-10-27"
            mock_bot.state_manager.get_second_trade_override.return_value = None

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            # Should qualify because the prior trade was BTC, and we are ETH
            mock_bot.state_manager.set_second_trade_override.assert_called_once()
            call_args = mock_bot.state_manager.set_second_trade_override.call_args
            assert (
                call_args[0][1] == "ETHUSDT_1"
            )  # scope_key should be normalized (no slash) with sequence number (first override)
            assert call_args[0][2]["scope"] == "per_symbol"

    def test_qualification_fail_per_symbol_same_symbol(self, mock_bot):
        # Test that per_symbol scope blocks if SAME symbol traded
        config_data = {
            "enabled": True,
            "scope": "per_symbol",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            pos.symbol_id = "ETHUSDT"
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

            history_entry = {"timestamp": datetime.now(timezone.utc)}

            # Add a prior exit for SAME symbol
            mock_bot.trade_history = [
                {
                    "timestamp": datetime.now(timezone.utc),
                    "type": "exit",
                    "symbol": "ETHUSDT",
                },
                history_entry,
            ]

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_with_string_timestamps(self, mock_bot):
        # Test that string timestamps (from JSON load) are handled correctly
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True):

            pos = MagicMock()
            pos.entry_time = datetime.now(timezone.utc)
            history_entry = {"timestamp": datetime.now(timezone.utc)}

            # Add a prior exit with STRING timestamp (simulating loaded from JSON)
            # This should be detected as a prior trade, so qualification should FAIL
            prior_ts_str = datetime.now(timezone.utc).isoformat()
            mock_bot.trade_history = [
                {"timestamp": prior_ts_str, "type": "exit"},
                history_entry,
            ]

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("10.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_fail_low_r_multiple(self, mock_bot):
        # Test R-multiple gate: trade doesn't meet min_r threshold
        config_data = {
            "enabled": True,
            "scope": "global",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
            "require_min_pnl_r_multiple": 0.75,  # Requires at least 0.75R
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True), patch.object(
            mock_bot, "_count_daily_overrides", return_value=0
        ):

            pos = MagicMock()
            pos.symbol_id = "BTCUSDT"
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=15)

            # History entry with LOW realized_r_multiple (0.5R < 0.75R threshold)
            history_entry = {
                "timestamp": datetime.now(timezone.utc),
                "realized_r_multiple": Decimal("0.5"),  # Below threshold
            }

            # Does NOT qualify because 0.5R < 0.75R
            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("5.0"), "TrailExit"
            )

            mock_bot.state_manager.set_second_trade_override.assert_not_called()

    def test_qualification_success_high_r_multiple(self, mock_bot):
        # Test R-multiple gate: trade meets min_r threshold
        config_data = {
            "enabled": True,
            "scope": "per_symbol",
            "allowed_reasons": ["TrailExit"],
            "max_time_minutes": 30,
            "require_min_pnl_r_multiple": 0.75,  # Requires at least 0.75R
        }

        with patch(
            "builtins.open", mock_open(read_data=json.dumps(config_data))
        ), patch("pathlib.Path.exists", return_value=True), patch.object(
            mock_bot, "_count_daily_overrides", return_value=0
        ):

            pos = MagicMock()
            pos.symbol_id = "ETHUSDT"
            pos.entry_time = datetime.now(timezone.utc) - timedelta(minutes=5)

            # History entry with GOOD realized_r_multiple (1.5R > 0.75R threshold)
            history_entry = {
                "timestamp": datetime.now(timezone.utc),
                "realized_r_multiple": Decimal("1.5"),  # Above threshold
            }

            # Qualifies because 1.5R >= 0.75R
            mock_bot.state_manager.make_day_key.return_value = "2023-10-27"
            mock_bot.state_manager.get_second_trade_override.return_value = None

            mock_bot._evaluate_second_trade_leverage_qualification(
                pos, history_entry, Decimal("15.0"), "TrailExit"
            )

            # Should pass R-multiple check and qualify
            mock_bot.state_manager.set_second_trade_override.assert_called_once()
            call_args = mock_bot.state_manager.set_second_trade_override.call_args
            assert call_args[0][0] == "2023-10-27"  # day_key
            assert call_args[0][1] == "ETHUSDT_1"  # scope_key with sequence
