"""
Unit tests for SignalProcessor (bot_v2/signals/signal_processor.py)

Covers:
- Signal queueing
- Signal normalization and routing
- Entry/exit callback invocation

Three-pass review: Completed.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot_v2.signals.signal_processor import SignalProcessor


@pytest.mark.asyncio
async def test_handle_webhook_signal_queues_signal():
    queue = asyncio.Queue()
    processor = SignalProcessor(
        signal_queue=queue,
        logger=MagicMock(),
        positions={},
        notifier=MagicMock(),
        strategy_configs={"TEST/USDT": MagicMock()},
        volatility_filter=MagicMock(),
        cost_filter=MagicMock(),
        capital_manager=MagicMock(),
        risk_manager=MagicMock(),
        get_order_manager_for_symbol=MagicMock(),
        normalize_symbol=lambda s: s,
        handle_entry_signal_callback=AsyncMock(),
        handle_exit_signal_callback=AsyncMock(),
    )
    signal = {"action": "buy", "symbol": "TEST/USDT"}
    await processor.handle_webhook_signal(signal)
    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_process_signals_invokes_entry_callback():
    queue = asyncio.Queue()
    entry_cb = AsyncMock()
    exit_cb = AsyncMock()
    processor = SignalProcessor(
        signal_queue=queue,
        logger=MagicMock(),
        positions={},
        notifier=MagicMock(),
        strategy_configs={"TEST/USDT": MagicMock()},
        volatility_filter=MagicMock(),
        cost_filter=MagicMock(),
        capital_manager=MagicMock(),
        risk_manager=MagicMock(),
        get_order_manager_for_symbol=MagicMock(),
        normalize_symbol=lambda s: s,
        handle_entry_signal_callback=entry_cb,
        handle_exit_signal_callback=exit_cb,
    )
    signal = {"action": "buy", "symbol": "TEST/USDT"}
    await processor.handle_webhook_signal(signal)
    await processor.process_signals()
    entry_cb.assert_awaited_with("TEST/USDT", "LONG", None)
    exit_cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_signals_invokes_exit_callback():
    queue = asyncio.Queue()
    entry_cb = AsyncMock()
    exit_cb = AsyncMock()
    processor = SignalProcessor(
        signal_queue=queue,
        logger=MagicMock(),
        positions={},
        notifier=MagicMock(),
        strategy_configs={"TEST/USDT": MagicMock()},
        volatility_filter=MagicMock(),
        cost_filter=MagicMock(),
        capital_manager=MagicMock(),
        risk_manager=MagicMock(),
        get_order_manager_for_symbol=MagicMock(),
        normalize_symbol=lambda s: s,
        handle_entry_signal_callback=entry_cb,
        handle_exit_signal_callback=exit_cb,
    )
    signal = {"action": "exit", "symbol": "TEST/USDT"}
    await processor.handle_webhook_signal(signal)
    await processor.process_signals()
    exit_cb.assert_awaited_with("TEST/USDT")
    entry_cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_signals_handles_unknown_action():
    queue = asyncio.Queue()
    entry_cb = AsyncMock()
    exit_cb = AsyncMock()
    logger = MagicMock()
    processor = SignalProcessor(
        signal_queue=queue,
        logger=logger,
        positions={},
        notifier=MagicMock(),
        strategy_configs={"TEST/USDT": MagicMock()},
        volatility_filter=MagicMock(),
        cost_filter=MagicMock(),
        capital_manager=MagicMock(),
        risk_manager=MagicMock(),
        get_order_manager_for_symbol=MagicMock(),
        normalize_symbol=lambda s: s,
        handle_entry_signal_callback=entry_cb,
        handle_exit_signal_callback=exit_cb,
    )
    signal = {"action": "foobar", "symbol": "TEST/USDT"}
    await processor.handle_webhook_signal(signal)
    await processor.process_signals()
    logger.warning.assert_called_with("Unknown action: foobar")
    entry_cb.assert_not_awaited()
    exit_cb.assert_not_awaited()
