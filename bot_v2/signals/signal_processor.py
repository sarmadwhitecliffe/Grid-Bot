"""
Signal Processor Module for TradingBot

Handles signal queue processing, normalization, validation, and routing.

- Extracted from bot_v2/bot.py for modularity and testability.
- Core trading logic remains unchanged; only code movement and references updated.

Three-pass review: Completed.
"""

import asyncio
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SignalProcessor:
    def __init__(
        self,
        signal_queue: asyncio.Queue,
        logger,
        positions,
        notifier,
        strategy_configs,
        volatility_filter,
        cost_filter,
        capital_manager,
        risk_manager,
        get_order_manager_for_symbol,
        normalize_symbol,
        handle_entry_signal_callback,
        handle_exit_signal_callback,
    ):
        """
        Initialize SignalProcessor with required dependencies.
        Args:
            signal_queue: Asyncio queue for incoming signals
            logger: Logger instance
            positions: Active positions dict
            notifier: Notification system (e.g., Telegram)
            strategy_configs: Strategy configuration(s)
            volatility_filter: Volatility filter instance
            cost_filter: Cost filter instance
            capital_manager: Capital manager instance
            risk_manager: Risk manager instance
            get_order_manager_for_symbol: Callable to get order manager per symbol
            normalize_symbol: Symbol normalization function
            handle_entry_signal_callback: Callback for entry signal
            handle_exit_signal_callback: Callback for exit signal
        """
        self.signal_queue = signal_queue
        self.logger = logger
        self.positions = positions
        self.notifier = notifier
        self.strategy_configs = strategy_configs
        self.volatility_filter = volatility_filter
        self.cost_filter = cost_filter
        self.capital_manager = capital_manager
        self.risk_manager = risk_manager
        self.get_order_manager_for_symbol = get_order_manager_for_symbol
        self.normalize_symbol = normalize_symbol
        self.handle_entry_signal_callback = handle_entry_signal_callback
        self.handle_exit_signal_callback = handle_exit_signal_callback

    async def handle_webhook_signal(self, signal: Dict[str, Any]) -> None:
        """
        Handle incoming webhook signal.
        Args:
            signal: Signal dict with 'action', 'symbol', and optional 'metadata'
        """
        action = signal.get("action", "").lower()
        symbol = signal.get("symbol")
        self.logger.info(f"📥 Received signal: {action.upper()} {symbol}")
        await self.signal_queue.put(signal)

    async def process_signals(self) -> None:
        """
        Process all pending signals in the queue.
        """
        while not self.signal_queue.empty():
            try:
                signal = await asyncio.wait_for(self.signal_queue.get(), timeout=0.1)
                await self.process_single_signal(signal)
            except asyncio.TimeoutError:
                break
            except Exception as e:
                symbol = (
                    signal.get("symbol", "unknown")
                    if "signal" in locals()
                    else "unknown"
                )
                self.logger.error(
                    f"Error processing signal for {symbol}: {e}", exc_info=True
                )

    async def process_single_signal(self, signal: Dict[str, Any]) -> None:
        """
        Process a single trading signal.
        Args:
            signal: Signal dict with 'action', 'symbol', and optional 'metadata'
        """
        action = signal.get("action", "").lower()
        symbol = signal.get("symbol")
        metadata = signal.get("metadata")
        symbol = self.normalize_symbol(symbol)
        self.logger.info(f"Processing signal: {action} for {symbol}")
        if action == "buy":
            await self.handle_entry_signal_callback(symbol, "LONG", metadata)
        elif action == "sell":
            await self.handle_entry_signal_callback(symbol, "SHORT", metadata)
        elif action == "exit":
            await self.handle_exit_signal_callback(symbol)
        else:
            self.logger.warning(f"Unknown action: {action}")


# Usage in bot.py:
# from bot_v2.signals.signal_processor import SignalProcessor
# self.signal_processor = SignalProcessor(...)
# await self.signal_processor.handle_webhook_signal(signal)
# await self.signal_processor.process_signals()
# await self.signal_processor.process_single_signal(signal)
