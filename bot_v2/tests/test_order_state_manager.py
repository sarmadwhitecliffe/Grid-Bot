"""Regression tests for OrderStateManager persistence."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bot_v2.execution.order_state_manager import OrderRecord, OrderStateManager


@pytest.mark.asyncio
async def test_add_order_uses_stable_snapshot_during_concurrent_saves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent writes should serialize a stable snapshot without runtime errors."""
    manager = OrderStateManager(tmp_path)
    original_save_sync = manager._save_sync
    save_started = asyncio.Event()

    def slow_save_sync(snapshot):
        save_started.set()
        import time

        time.sleep(0.02)
        original_save_sync(snapshot)

    monkeypatch.setattr(manager, "_save_sync", slow_save_sync)

    first_order = OrderRecord(
        local_id="order-1",
        exchange_order_id="exchange-1",
        symbol="LINK/USDT",
        side="BUY",
        quantity="1",
        avg_price="10",
        status="NEW",
        mode="local_sim",
    )
    second_order = OrderRecord(
        local_id="order-2",
        exchange_order_id="exchange-2",
        symbol="LINK/USDT",
        side="SELL",
        quantity="1",
        avg_price="11",
        status="NEW",
        mode="local_sim",
    )

    first_task = asyncio.create_task(manager.add_order(first_order))
    await asyncio.wait_for(save_started.wait(), timeout=1)
    await manager.add_order(second_order)
    await first_task

    with open(tmp_path / "orders_state.json", "r") as handle:
        persisted = json.load(handle)

    assert set(persisted["orders"]) == {"order-1", "order-2"}
    assert manager.get_order("order-1") is not None
    assert manager.get_order("order-2") is not None


@pytest.mark.asyncio
async def test_concurrent_add_order_persists_all_without_tempfile_collision(
    tmp_path: Path,
) -> None:
    """High-concurrency writes should not lose data or raise temp-file races."""
    manager = OrderStateManager(tmp_path)

    tasks = []
    total_orders = 60
    for i in range(total_orders):
        order = OrderRecord(
            local_id=f"order-{i}",
            exchange_order_id=f"exchange-{i}",
            symbol="BTC/USDT",
            side="BUY" if i % 2 == 0 else "SELL",
            quantity="1",
            avg_price="100",
            status="NEW",
            mode="local_sim",
        )
        tasks.append(asyncio.create_task(manager.add_order(order)))

    await asyncio.gather(*tasks)

    with open(tmp_path / "orders_state.json", "r", encoding="utf-8") as handle:
        persisted = json.load(handle)

    assert len(persisted["orders"]) == total_orders
    assert len(manager.get_all_orders()) == total_orders


@pytest.mark.asyncio
async def test_prune_archive_moves_filled_orders_older_than_retention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filled orders older than retention should move to archive and leave open orders untouched."""
    monkeypatch.setenv("BOTV2_ORDER_STATE_RETENTION_HOURS", "1")
    manager = OrderStateManager(tmp_path)
    now = datetime.now(timezone.utc)

    old_filled = OrderRecord(
        local_id="filled-old",
        exchange_order_id="exchange-filled-old",
        symbol="ADA/USDT",
        side="SELL",
        quantity="10",
        avg_price="1.1",
        status="FILLED",
        created_at=(now - timedelta(hours=3)).isoformat(),
        mode="local_sim",
        raw_response={"debug": "payload"},
    )
    fresh_open = OrderRecord(
        local_id="open-fresh",
        exchange_order_id="exchange-open-fresh",
        symbol="ADA/USDT",
        side="BUY",
        quantity="10",
        avg_price="1.0",
        status="OPEN",
        created_at=now.isoformat(),
        mode="local_sim",
    )

    await manager.add_order(old_filled)
    await manager.add_order(fresh_open)

    await manager.prune_archive()

    with open(tmp_path / "orders_state.json", "r", encoding="utf-8") as handle:
        state = json.load(handle)
    with open(tmp_path / "orders_archive.json", "r", encoding="utf-8") as handle:
        archive = json.load(handle)

    assert "filled-old" not in state["orders"]
    assert "open-fresh" in state["orders"]
    assert "filled-old" in archive["orders"]
    assert "raw_response" not in archive["orders"]["filled-old"]


@pytest.mark.asyncio
async def test_prune_archive_keeps_order_with_invalid_created_at(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid timestamps should be retained to avoid accidental data loss."""
    monkeypatch.setenv("BOTV2_ORDER_STATE_RETENTION_HOURS", "1")
    manager = OrderStateManager(tmp_path)

    order = OrderRecord(
        local_id="filled-invalid-ts",
        exchange_order_id="exchange-filled-invalid-ts",
        symbol="BTC/USDT",
        side="SELL",
        quantity="1",
        avg_price="100",
        status="FILLED",
        created_at="not-a-timestamp",
        mode="local_sim",
    )

    await manager.add_order(order)
    await manager.prune_archive()

    with open(tmp_path / "orders_state.json", "r", encoding="utf-8") as handle:
        state = json.load(handle)

    assert "filled-invalid-ts" in state["orders"]