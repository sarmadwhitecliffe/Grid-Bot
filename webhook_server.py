"""
Webhook server for bot_v2 - receives trading signals and commands.

This server integrates with the modular bot_v2 architecture and provides:
- Health check endpoint
- Status reporting endpoint
- Webhook signal processing
- Start/stop trading control
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Add parent directory to Python path for bot_v2 imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# --- Logger Setup ---
from bot_v2.utils.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("WebhookServer")

# --- Bot Module Import ---
try:
    from bot_v2.bot import TradingBot
    from bot_v2.models.strategy_config import StrategyConfig

    BOT_IMPORTED_SUCCESSFULLY = True
    logger.debug("Successfully imported bot_v2 TradingBot and StrategyConfig.")
except ImportError as e:
    BOT_IMPORTED_SUCCESSFULLY = False
    logger.critical(f"FATAL: Could not import from 'bot_v2'. Error: {e}")

    # Minimal dummy replacements
    class StrategyConfig:
        pass

    class TradingBot:
        is_running = False

        async def run(self):
            pass

        async def handle_webhook_signal(self, signal):
            pass

        async def get_status_message(self):
            return "Bot not available"


# --- Pydantic Models ---
class WebhookPayload(BaseModel):
    """Pydantic model for webhook JSON payloads.

    Fields:
    - action: string specifying the command or trade action (e.g. 'start', 'buy').
    - symbol: optional trading symbol required for trade actions.
    - metadata: optional additional data about the signal.
    """

    action: str
    symbol: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class HealthStatus(BaseModel):
    """Model returned from the health endpoint describing server state.

    Fields:
    - status: a simple status string (usually 'ok').
    - bot_module_loaded: whether `bot_v2` was imported successfully.
    - bot_is_running: whether the instantiated bot reports it's running.
    - trading_is_enabled: whether incoming trade signals are currently accepted.
    - timestamp: UTC timestamp when the check was generated.
    """

    status: str
    bot_module_loaded: bool
    bot_is_running: bool
    trading_is_enabled: bool
    timestamp: datetime


# --- Periodic Summary Logging ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager that starts/stops the TradingBot.

    Responsibilities:
    - Initialize `app.state.trading_enabled` to allow toggling signal processing.
    - If available, instantiate `TradingBot` and start its background task.
    - Attempt graceful shutdown of the bot on application exit.
    """
    # --- Server Startup ---
    app.state.trading_enabled = True
    logger.info("Trading is ENABLED by default on server startup.")

    if not BOT_IMPORTED_SUCCESSFULLY:
        logger.error("Bot module not loaded. The bot will not be started.")
        yield
        return

    # Try to create and start the bot in a background asyncio task.
    logger.debug("Server starting up... Initializing bot_v2 TradingBot.")
    bot_instance = None
    try:
        # Load configuration from strategy_configs.json
        import json
        from pathlib import Path

        config_path = Path(
            os.getenv("STRATEGY_CONFIG_PATH", "config/strategy_configs.json")
        )

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r") as f:
            all_configs = json.load(f)

        # Log second_trade_override feature configuration status at startup (visibility for ops)
        try:
            feature_path = Path("config") / "second_trade_override.feature.json"
            if feature_path.exists():
                with open(feature_path, "r") as ff:
                    feature_cfg = json.load(ff)
                logger.info(
                    "second_trade_override startup: path=%s enabled=%s scope=%s max_time_min=%s",
                    str(feature_path),
                    feature_cfg.get("enabled"),
                    feature_cfg.get("scope"),
                    feature_cfg.get("max_time_minutes"),
                )
            else:
                logger.info(
                    "second_trade_override startup: feature file missing at %s",
                    str(feature_path),
                )
        except Exception as fe:
            logger.warning(
                "second_trade_override startup: failed to read feature file: %s", fe
            )

        # Get the target symbol(s) from environment
        target_symbol = os.getenv("BOT_SYMBOL", None)
        multi_symbol_mode = os.getenv("BOT_MULTI_SYMBOL", "true").lower() == "true"

        if target_symbol and not multi_symbol_mode:
            # Single-symbol mode (legacy)
            if target_symbol not in all_configs:
                raise ValueError(f"Symbol {target_symbol} not found in config file")
            config_data = all_configs[target_symbol]
            symbol_id = target_symbol
            config = StrategyConfig.from_dict(symbol_id, config_data)
            logger.info(f"Initializing bot in single-symbol mode for {symbol_id}")
            bot_instance = TradingBot(config)
        else:
            # Multi-symbol mode (default) - load all enabled symbols
            enabled_symbols = {
                k: v for k, v in all_configs.items() if v.get("enabled", True)
            }
            if not enabled_symbols:
                raise ValueError("No enabled symbols found in config file")

            # Build multi-symbol config dict
            multi_config = {}
            for symbol_id, config_data in enabled_symbols.items():
                multi_config[symbol_id] = StrategyConfig.from_dict(
                    symbol_id, config_data
                )

            logger.info(
                f"Initializing bot in multi-symbol mode with {len(multi_config)} symbols: {', '.join(multi_config.keys())}"
            )
            bot_instance = TradingBot(multi_config)
        app.state.bot = bot_instance

        # Start bot in background
        bot_task = asyncio.create_task(bot_instance.run())
        app.state.bot_task = bot_task

        # Wait for the bot to indicate readiness
        for _ in range(60):
            if bot_instance.is_running:
                logger.info(
                    "✅ bot_v2 TradingBot is initialized and running. Server is fully operational."
                )
                break
            await asyncio.sleep(1)
        else:
            raise asyncio.TimeoutError(
                "Bot did not become ready within the timeout period."
            )

        # Start periodic summary logging task
        summary_task = asyncio.create_task(log_periodic_summaries(bot_instance))
        app.state.summary_task = summary_task

    except Exception as e:
        logger.critical(
            f"🚨 FAILED to start TradingBot during server startup: {e}", exc_info=True
        )
        app.state.bot = None
        if bot_instance and hasattr(app.state, "bot_task"):
            app.state.bot_task.cancel()

    # Yield control back to FastAPI — app is now serving requests.
    yield

    # --- Server Shutdown ---
    active_bot = getattr(app.state, "bot", None)
    if not active_bot:
        logger.info("No active bot instance to shut down.")
        return

    logger.info("Server shutting down. Initiating graceful bot shutdown...")
    bot_task: asyncio.Task = app.state.bot_task
    summary_task: asyncio.Task = getattr(app.state, "summary_task", None)

    if not bot_task.done():
        # Ask the bot to perform its own asynchronous shutdown steps (close exchanges, cleanup)
        try:
            # Support both async and sync shutdown implementations on the bot.
            shutdown_result = active_bot.shutdown()
            # If the result is awaitable (coroutine/future), await it; otherwise assume sync and proceed.
            if asyncio.iscoroutine(shutdown_result) or hasattr(
                shutdown_result, "__await__"
            ):
                await shutdown_result
        except Exception as e:
            logger.warning(f"Error during bot.shutdown(): {e}")

        # Ensure the bot task exits in a timely manner
        try:
            # Prefer awaiting tasks/coroutines/futures directly
            if (
                asyncio.isfuture(bot_task)
                or asyncio.iscoroutine(bot_task)
                or isinstance(bot_task, asyncio.Task)
                or hasattr(bot_task, "__await__")
            ):
                await asyncio.wait_for(bot_task, timeout=20.0)
                logger.info("✅ Bot background task shut down gracefully.")
            else:
                # Fallback for test doubles (AsyncMock) or non-awaitable objects: poll .done()
                logger.debug(
                    "Bot task is not awaitable - polling .done() until completion or timeout"
                )
                timeout_seconds = 20.0
                poll_interval = 0.1
                elapsed = 0.0
                while elapsed < timeout_seconds:
                    try:
                        if bot_task.done():
                            logger.info("✅ Bot background task completed (polled).")
                            break
                    except Exception:
                        # If .done() is not callable or raises, break and attempt cancel
                        break
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval
                else:
                    logger.warning(
                        "⚠️ Bot task did not shut down within timeout (polled). Forcibly cancelling."
                    )
                    try:
                        cancel_result = bot_task.cancel()
                        if asyncio.iscoroutine(cancel_result) or hasattr(
                            cancel_result, "__await__"
                        ):
                            try:
                                await cancel_result
                            except Exception as e:
                                logger.debug(
                                    f"Error while awaiting cancel_result for bot_task: {e}"
                                )
                    except Exception as e:
                        logger.debug(f"Error while attempting to cancel bot_task: {e}")
        except asyncio.TimeoutError:
            logger.warning(
                "⚠️ Bot task did not shut down within timeout. Forcibly cancelling."
            )
            try:
                cancel_result = bot_task.cancel()
                if asyncio.iscoroutine(cancel_result) or hasattr(
                    cancel_result, "__await__"
                ):
                    try:
                        await cancel_result
                    except Exception as e:
                        logger.debug(
                            f"Error while awaiting cancel_result during TimeoutError handling: {e}"
                        )
            except Exception as e:
                logger.debug(f"Error during TimeoutError cancel handling: {e}")
        except asyncio.CancelledError:
            logger.info("Bot task was cancelled during shutdown.")
        except Exception as e:
            logger.error(f"An error occurred during bot shutdown: {e}", exc_info=True)

    # Mark bot as not running now that shutdown procedures were invoked
    try:
        active_bot.is_running = False
    except Exception as e:
        logger.debug(f"Could not set active_bot.is_running flag: {e}")

    # Cancel summary task
    if summary_task and not summary_task.done():
        summary_task.cancel()
        logger.debug("Summary task cancelled.")


# --- FastAPI App ---
app = FastAPI(
    title="bot_v2 Trading Webhook Server",
    description="Receives and processes trading signals and commands for bot_v2.",
    version="2.0.0",
    lifespan=lifespan,
)


# --- API Endpoints ---
@app.get("/health", response_model=HealthStatus)
def health_check(request: Request):
    """Health endpoint returning server and bot status information.

    This is useful for external monitoring/uptime checks and returns the
    `HealthStatus` model which contains: status, whether the bot module
    was loaded, whether the bot is running, whether trading is enabled, and
    the current UTC timestamp.
    """
    bot_instance = getattr(request.app.state, "bot", None)
    trading_enabled = getattr(request.app.state, "trading_enabled", False)

    return HealthStatus(
        status="ok",
        bot_module_loaded=BOT_IMPORTED_SUCCESSFULLY,
        bot_is_running=bot_instance.is_running if bot_instance else False,
        trading_is_enabled=trading_enabled,
        timestamp=datetime.now(timezone.utc),
    )


@app.get("/status")
async def get_bot_status(request: Request):
    """Get formatted status message including active positions and PnL.

    This endpoint triggers the bot's heartbeat message generation and returns
    it as a formatted text response suitable for Telegram or other messaging platforms.
    """
    bot_instance = getattr(request.app.state, "bot", None)

    if not bot_instance or not bot_instance.is_running:
        return {"status": "error", "message": "Trading bot is not running."}

    try:
        status_message = await bot_instance.get_status_message()
        return {"status": "ok", "message": status_message}
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        return {"status": "error", "message": f"Failed to get status: {str(e)}"}


@app.get("/summary")
async def get_bot_summary(request: Request, hours: int = 24):
    """Get performance summary for the last N hours.

    Query parameters:
        hours: Number of hours to look back (default: 24, common: 24, 168 for 7 days)

    This endpoint returns a detailed performance summary including win rate,
    profit factor, exit analysis, and trade statistics.
    """
    bot_instance = getattr(request.app.state, "bot", None)

    if not bot_instance or not bot_instance.is_running:
        return {"status": "error", "message": "Trading bot is not running."}

    # Validate hours parameter
    if hours < 1 or hours > 720:  # Max 30 days
        return {
            "status": "error",
            "message": "Hours must be between 1 and 720 (30 days)",
        }

    try:
        summary_message = await bot_instance.get_summary_message(hours=hours)
        return {"status": "ok", "message": summary_message}
    except Exception as e:
        logger.error(f"Error getting bot summary: {e}")
        return {"status": "error", "message": f"Failed to get summary: {str(e)}"}


@app.post("/webhook", status_code=202)
async def process_webhook_signal(payload: WebhookPayload, request: Request):
    """Main webhook receiver.

    Supported behaviors:
    - `start` / `stop` actions toggle whether trade signals are processed.
    - `buy`, `sell`, `exit` are treated as trade signals and forwarded to the bot.

    The endpoint uses 202 Accepted since signal processing is asynchronous.
    """
    logger.info(
        f"Webhook received: Action='{payload.action}', Symbol='{payload.symbol or 'N/A'}'"
    )
    if payload.metadata:
        summary = (
            payload.metadata.get("gate_message")
            or payload.metadata.get("failing_gates")
            or "metadata payload received"
        )
        logger.debug(f"Webhook metadata summary: {summary}")

    bot: TradingBot = getattr(request.app.state, "bot", None)
    command = payload.action.strip().lower()

    # Immediate commands that affect runtime behavior
    if command == "start":
        request.app.state.trading_enabled = True
        logger.info("Signal processing has been STARTED.")
        return {"status": "Trading enabled."}

    if command == "stop":
        request.app.state.trading_enabled = False
        logger.info("Signal processing has been STOPPED.")
        return {"status": "Trading disabled."}

    # --- From here, we handle trade signals ---
    if not bot or not bot.is_running:
        raise HTTPException(status_code=503, detail="The trading bot is not running.")

    if not request.app.state.trading_enabled:
        logger.warning(
            f"Signal ignored because trading is stopped: Action='{payload.action}'"
        )
        return {"status": "Signal ignored, trading is currently stopped."}

    if not payload.symbol:
        raise HTTPException(
            status_code=422,
            detail=f"A 'symbol' is required for the action '{payload.action}'",
        )

    # Grid Control Commands
    if command == "grid_start":
        if not hasattr(bot, "grid_orchestrators") or payload.symbol not in bot.grid_orchestrators:
            raise HTTPException(status_code=400, detail=f"Grid not configured for {payload.symbol}")
        await bot.grid_orchestrators[payload.symbol].start()
        return {"status": f"Grid started for {payload.symbol}"}

    if command == "grid_stop":
        if not hasattr(bot, "grid_orchestrators") or payload.symbol not in bot.grid_orchestrators:
            raise HTTPException(status_code=400, detail=f"Grid not active for {payload.symbol}")
        await bot.grid_orchestrators[payload.symbol].stop()
        return {"status": f"Grid stopped for {payload.symbol}"}

    valid_trade_actions = ["buy", "sell", "exit"]
    if command not in valid_trade_actions:
        raise HTTPException(
            status_code=422, detail=f"Unrecognized trade action: '{payload.action}'"
        )

    final_signal = {
        "action": command,
        "symbol": payload.symbol,
        "metadata": payload.metadata,
    }

    try:
        await bot.handle_webhook_signal(final_signal)
        logger.info(
            f"Signal for {final_signal['symbol']} ({final_signal['action']}) was successfully queued."
        )
        return {"status": "Signal accepted and queued."}
    except ValueError as e:
        logger.warning(f"Signal rejected (validation error): {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(
            f"An unexpected error occurred while queueing signal: {e}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Internal server error.")


# --- Main Execution ---
async def log_periodic_summaries(bot_instance):
    """Log periodic summary of bot activity every hour."""
    while bot_instance.is_running:
        try:
            await asyncio.sleep(3600)  # Wait 1 hour
            trade_count = (
                len(bot_instance.trade_history)
                if hasattr(bot_instance, "trade_history")
                else 0
            )
            position_count = (
                len(bot_instance.positions) if hasattr(bot_instance, "positions") else 0
            )
            logger.info(
                f"📊 Periodic Summary: Trades={trade_count}, Active Positions={position_count}"
            )
        except asyncio.CancelledError:
            logger.debug("Periodic summary task cancelled.")
            break
        except Exception as e:
            logger.error(f"Error in periodic summary logging: {e}", exc_info=True)


if __name__ == "__main__":
    port = int(
        os.getenv("WEBHOOK_PORT_FUTURES", "5000")
    )  # Different port from original
    reload_status = os.getenv("DEV_RELOAD", "false").lower() == "true"

    logger.info("--- Starting bot_v2 Webhook Server (v2.0) ---")
    logger.info(f"Host: http://0.0.0.0:{port}")
    logger.info(f"Uvicorn reload enabled: {reload_status}")

    # Run the app object directly to avoid re-importing the module as "__main__" and
    # potential duplicate execution or blocking behavior. Passing the app object is
    # a safer pattern when starting Uvicorn from inside the module.
    uvicorn.run(app, host="0.0.0.0", port=port, reload=reload_status)
