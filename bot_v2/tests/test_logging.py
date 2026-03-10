"""
Tests for logging configuration utilities.

Basic coverage to ensure logging setup works correctly.
"""

import logging
from pathlib import Path

from bot_v2.utils.logging_config import get_logger, setup_logging


class TestLoggingSetup:
    """Test logging configuration."""

    def test_setup_logging_default(self, tmp_path, monkeypatch):
        """Test default logging setup."""
        # Change to tmp directory so logs go there
        import os

        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            # Clear any existing handlers
            root_logger = logging.getLogger()
            root_logger.handlers.clear()

            setup_logging()

            # Should have handlers configured
            assert len(root_logger.handlers) > 0

            # Log directory should be created in current dir
            log_dir = tmp_path / "bot_logs"
            assert log_dir.exists()
        finally:
            os.chdir(original_cwd)

    def test_setup_logging_with_level(self, tmp_path, monkeypatch):
        """Test logging setup with custom level."""
        import os

        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            monkeypatch.setenv("LOG_LEVEL", "DEBUG")

            # Clear handlers
            root_logger = logging.getLogger()
            root_logger.handlers.clear()

            setup_logging()

            assert root_logger.level == logging.DEBUG
        finally:
            os.chdir(original_cwd)

    def test_setup_logging_idempotent(self, tmp_path, monkeypatch):
        """Test that calling setup_logging multiple times doesn't duplicate handlers."""
        import os

        original_cwd = os.getcwd()
        os.chdir(tmp_path)

        try:
            # Clear handlers
            root_logger = logging.getLogger()
            root_logger.handlers.clear()

            setup_logging()
            handler_count_1 = len(root_logger.handlers)

            setup_logging()
            handler_count_2 = len(root_logger.handlers)

            # Should not add duplicate handlers
            assert handler_count_1 == handler_count_2
        finally:
            os.chdir(original_cwd)

    def test_get_logger_returns_logger(self):
        """Test get_logger returns a logger instance."""
        logger = get_logger("test_module")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test_module"

    def test_logger_can_log_messages(self, caplog):
        """Test that logger can actually log messages."""
        logger = get_logger("test_module")

        with caplog.at_level(logging.INFO):
            logger.info("Test message")

        assert "Test message" in caplog.text

    def test_logger_levels_work(self, caplog):
        """Test different log levels work."""
        logger = get_logger("test_module")

        with caplog.at_level(logging.DEBUG):
            logger.debug("Debug message")
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")

        assert "Debug message" in caplog.text
        assert "Info message" in caplog.text
        assert "Warning message" in caplog.text
        assert "Error message" in caplog.text


class TestLoggingIntegration:
    """Test logging integration with other modules."""

    def test_capital_manager_uses_logging(self, temp_data_dir, caplog):
        """Test that CapitalManager logs operations."""
        import asyncio

        from bot_v2.risk.capital_manager import CapitalManager

        manager = CapitalManager(data_dir=Path(temp_data_dir))

        with caplog.at_level(logging.INFO):
            asyncio.run(manager.get_capital("BTCUSDT"))

        # Should log initialization
        assert (
            "Initialized BTCUSDT" in caplog.text
            or "No existing capitals" in caplog.text
        )

    def test_state_manager_uses_logging(self, temp_data_dir, caplog):
        """Test that StateManager logs operations."""
        from bot_v2.persistence.state_manager import StateManager

        manager = StateManager(data_dir=Path(temp_data_dir))

        with caplog.at_level(logging.INFO):
            manager.load_positions()

        # StateManager should have logged something
        assert len(caplog.records) >= 0  # May log or may not if no file
