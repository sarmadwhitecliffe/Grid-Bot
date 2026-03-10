"""
Structured logging configuration.

Sets up JSON-formatted logging for production and human-readable
logging for development. Provides consistent logging across all modules.
"""

import logging
import os
import queue
import sys
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from typing import Optional

import pytz


class NYTimeFormatter(logging.Formatter):
    """Convert log timestamps to NY/EST time regardless of system timezone."""
    
    def formatTime(self, record, datefmt=None):
        # Convert UTC timestamp to NY/EST timezone
        utc_dt = datetime.fromtimestamp(record.created, tz=pytz.UTC)
        ny_tz = pytz.timezone('America/New_York')
        ny_dt = utc_dt.astimezone(ny_tz)
        return ny_dt.strftime('%Y-%m-%d %H:%M:%S')


def setup_logging(
    log_level: Optional[str] = None,
    log_dir: Optional[Path] = None,
    log_file: str = "bot_v2.log",
    json_format: Optional[bool] = None,
    verbose_startup: Optional[bool] = None,
    async_logging: bool = True,
) -> None:
    """
    Set up console and file logging in an idempotent manner.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
                  Defaults to LOG_LEVEL env var or INFO
        log_dir: Directory for log files. Defaults to bot_logs/
        log_file: Name of the log file
        json_format: Use JSON structured logging (for production). Defaults to LOG_STRUCTURED env var or True
        verbose_startup: Log detailed startup info. Defaults to LOG_VERBOSE_STARTUP env var or False
        async_logging: Use async queue-based logging to prevent blocking. Defaults to True
    """
    # Get log level from parameter or environment
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Get JSON format from parameter or environment (default to True for production)
    if json_format is None:
        json_format = os.getenv("LOG_STRUCTURED", "true").lower() == "true"

    # Get verbose startup from parameter or environment (default to False for cleaner logs)
    if verbose_startup is None:
        verbose_startup = os.getenv("LOG_VERBOSE_STARTUP", "false").lower() == "true"

    # Create log directory
    if log_dir is None:
        log_dir = Path("bot_logs")
    log_dir.mkdir(exist_ok=True)

    log_path = log_dir / log_file

    # Get async logging setting
    async_logging = os.getenv("LOG_ASYNC", "true").lower() == "true"

    # Get root logger
    root_logger = logging.getLogger()

    # Only set up if no handlers exist (idempotent)
    if root_logger.handlers:
        return

    # Set log level
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Create formatter
    if json_format:
        # JSON structured logging (for production parsing)
        formatter = NYTimeFormatter(
            '{"timestamp": "%(asctime)s", "level": "%(levelname)s", '
            '"logger": "%(name)s", "line": %(lineno)d, "message": "%(message)s"}'
        )
    else:
        # Human-readable logging (for development)
        formatter = NYTimeFormatter(
            "%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] - %(message)s"
        )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)

    # File handler
    try:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)

        if async_logging:
            # Use async queue-based logging to prevent blocking
            log_queue = queue.Queue()
            queue_handler = QueueHandler(log_queue)
            root_logger.addHandler(queue_handler)

            # Start background listener thread
            listener = QueueListener(
                log_queue, console_handler, file_handler, respect_handler_level=True
            )
            listener.start()

            # Store listener reference to prevent garbage collection
            root_logger._async_listener = listener

            root_logger.info(f"Async logging initialized. Log file: {log_path}")
        else:
            # Use synchronous logging
            root_logger.addHandler(console_handler)
            root_logger.addHandler(file_handler)
            root_logger.info(f"Logging initialized. Log file: {log_path}")

    except Exception as e:
        root_logger.error(f"Could not create log file handler: {e}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance

    Example:
        >>> from bot_v2.utils.logging_config import get_logger
        >>> logger = get_logger(__name__)
        >>> logger.info("Module initialized")
    """
    return logging.getLogger(name)
