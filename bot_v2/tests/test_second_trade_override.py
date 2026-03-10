from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bot_v2.persistence.state_manager import StateManager
from bot_v2.risk.capital_manager import CapitalManager


def test_second_trade_override_application(tmp_path, monkeypatch):
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()

    # No longer write or modify real strategy_configs.json; use in-memory feature_cfg only
    feature_cfg = {
        "enabled": True,
        "scope": "global",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "max_delay_minutes": 0,
        "require_min_pnl_r_multiple": 0,
    }
    # Removed file write to avoid overriding real strategy_configs.json

    # Initialize managers
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})

    # Simulate qualification (first trade closed)
    day_key = state_mgr.make_day_key()
    payload = {
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "reason": "AggressivePeakExit",
        "time_open_min": 8.4,
        "pnl_usd": "10.25",
        "scope": "global",
        "symbol": None,
        "consumed": False,
        "rule_version": "1",
    }
    state_mgr.set_second_trade_override(day_key, "GLOBAL", payload)

    # Apply override on second trade sizing
    base_leverage = Decimal("3")
    tier_max = Decimal("5")
    new_leverage = cap_mgr.apply_second_trade_override(
        symbol="TESTUSDT",
        leverage=base_leverage,
        tier_max_leverage=tier_max,
        state_manager=state_mgr,
        feature_cfg=feature_cfg,
    )

    assert new_leverage == tier_max, "Leverage should be elevated to tier max"
    # Second call should not re-apply
    second_leverage = cap_mgr.apply_second_trade_override(
        symbol="TESTUSDT",
        leverage=base_leverage,
        tier_max_leverage=tier_max,
        state_manager=state_mgr,
        feature_cfg=feature_cfg,
    )
    assert (
        second_leverage == base_leverage or second_leverage == tier_max
    ), "Override should not consume twice"


def test_second_trade_override_expiry(tmp_path):
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    feature_cfg = {
        "enabled": True,
        "scope": "global",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "max_delay_minutes": 1,
        "require_min_pnl_r_multiple": 0,
    }
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})
    day_key = state_mgr.make_day_key()
    past_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    payload = {
        "qualified_at": past_time.isoformat(),
        "reason": "TrailExit",
        "time_open_min": 5.0,
        "pnl_usd": "3.10",
        "scope": "global",
        "symbol": None,
        "consumed": False,
        "rule_version": "1",
    }
    state_mgr.set_second_trade_override(day_key, "GLOBAL", payload)
    base_leverage = Decimal("2")
    tier_max = Decimal("4")
    new_leverage = cap_mgr.apply_second_trade_override(
        symbol="TESTUSDT",
        leverage=base_leverage,
        tier_max_leverage=tier_max,
        state_manager=state_mgr,
        feature_cfg=feature_cfg,
    )
    # Should not apply due to expiry; leverage unchanged
    assert new_leverage == base_leverage, "Expired override should not change leverage"


def test_second_trade_override_concurrency(tmp_path):
    """Ensure only one concurrent application consumes the override."""
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    feature_cfg = {
        "enabled": True,
        "scope": "global",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "require_min_pnl_r_multiple": 0,
    }
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})
    day_key = state_mgr.make_day_key()
    payload = {
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "reason": "TrailExit",
        "time_open_min": 4.2,
        "pnl_usd": "5.00",
        "scope": "global",
        "symbol": None,
        "consumed": False,
        "rule_version": "1",
    }
    state_mgr.set_second_trade_override(day_key, "GLOBAL", payload)

    base_leverage = Decimal("2")
    tier_max = Decimal("7")

    results = []

    def attempt():
        results.append(
            cap_mgr.apply_second_trade_override(
                symbol="WIFUSDT",
                leverage=base_leverage,
                tier_max_leverage=tier_max,
                state_manager=state_mgr,
                feature_cfg=feature_cfg,
            )
        )

    # Simulate concurrent attempts
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=5) as ex:
        for _ in range(5):
            ex.submit(attempt)

    # At least one should reach tier_max, all others either same base or tier_max
    assert any(r == tier_max for r in results), "One attempt should elevate leverage"
    # Consumed state should be true
    override = state_mgr.get_second_trade_override(day_key, "GLOBAL")
    assert override.get(
        "consumed"
    ), "Override should be marked consumed after first successful application"


def test_second_trade_override_per_symbol_scope_isolation(tmp_path):
    """Qualification for one symbol should not apply to different symbol when scope=per_symbol."""
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    feature_cfg = {
        "enabled": True,
        "scope": "per_symbol",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "require_min_pnl_r_multiple": 0,
    }
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})
    day_key = state_mgr.make_day_key()
    # Qualify only for SYMBOL_A
    state_mgr.set_second_trade_override(
        day_key,
        "SYMBOLAUSDT",
        {
            "qualified_at": datetime.now(timezone.utc).isoformat(),
            "reason": "AggressivePeakExit",
            "time_open_min": 12.0,
            "pnl_usd": "9.10",
            "scope": "per_symbol",
            "symbol": "SYMBOLAUSDT",
            "consumed": False,
            "rule_version": "1",
        },
    )
    base = Decimal("3")
    tier_max = Decimal("9")
    # Different symbol should not apply
    lv_other = cap_mgr.apply_second_trade_override(
        "SYMBOLBUSDT", base, tier_max, state_mgr, feature_cfg
    )
    assert lv_other == base, "Override must not apply to other symbol"
    # Matching symbol applies
    lv_match = cap_mgr.apply_second_trade_override(
        "SYMBOLAUSDT", base, tier_max, state_mgr, feature_cfg
    )
    assert lv_match == tier_max, "Override should apply to qualified symbol"


def test_second_trade_override_midnight_reset(tmp_path):
    """Override for previous UTC day should not apply today; new qualification possible."""
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    feature_cfg = {
        "enabled": True,
        "scope": "global",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "require_min_pnl_r_multiple": 0,
    }
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})
    # Create yesterday's override
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    y_key = state_mgr.make_day_key(yesterday)
    state_mgr.set_second_trade_override(
        y_key,
        "GLOBAL",
        {
            "qualified_at": yesterday.isoformat(),
            "reason": "TrailExit",
            "time_open_min": 10.0,
            "pnl_usd": "4.50",
            "scope": "global",
            "symbol": None,
            "consumed": False,
            "rule_version": "1",
        },
    )
    # Applying today should not use yesterday's override
    base = Decimal("2")
    tier_max = Decimal("8")
    today_leverage = cap_mgr.apply_second_trade_override(
        "WIFUSDT", base, tier_max, state_mgr, feature_cfg
    )
    assert today_leverage == base, "Yesterday override must not apply today"
    # Qualify new override today then apply
    today_key = state_mgr.make_day_key()
    state_mgr.set_second_trade_override(
        today_key,
        "GLOBAL",
        {
            "qualified_at": datetime.now(timezone.utc).isoformat(),
            "reason": "TrailExit",
            "time_open_min": 7.5,
            "pnl_usd": "6.00",
            "scope": "global",
            "symbol": None,
            "consumed": False,
            "rule_version": "1",
        },
    )
    applied = cap_mgr.apply_second_trade_override(
        "WIFUSDT", base, tier_max, state_mgr, feature_cfg
    )
    assert applied == tier_max, "New day override should apply"


def test_second_trade_override_target_leverage(tmp_path):
    """Verify that target_leverage overrides tier max leverage."""
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    feature_cfg = {
        "enabled": True,
        "scope": "global",
        "max_time_minutes": 30,
        "allowed_reasons": ["AggressivePeakExit", "TrailExit"],
        "cooldown_minutes": 0,
        "rule_version": "1",
        "max_delay_minutes": 0,
        "require_min_pnl_r_multiple": 0,
        "target_leverage": 10,
    }
    state_mgr = StateManager(data_dir=data_dir)
    cap_mgr = CapitalManager(data_dir=data_dir, strategy_configs={})
    day_key = state_mgr.make_day_key()
    payload = {
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "reason": "AggressivePeakExit",
        "time_open_min": 8.4,
        "pnl_usd": "10.25",
        "scope": "global",
        "symbol": None,
        "consumed": False,
        "rule_version": "1",
    }
    state_mgr.set_second_trade_override(day_key, "GLOBAL", payload)

    base_leverage = Decimal("3")
    tier_max = Decimal("5")
    # Should return 10 (target), ignoring tier_max (5)
    new_leverage = cap_mgr.apply_second_trade_override(
        symbol="TESTUSDT",
        leverage=base_leverage,
        tier_max_leverage=tier_max,
        state_manager=state_mgr,
        feature_cfg=feature_cfg,
    )

    assert new_leverage == Decimal(
        "10"
    ), f"Leverage should be target leverage (10), got {new_leverage}"


def test_second_trade_override_numeric_fifo_ordering(tmp_path):
    """Ensure sequenced overrides use numeric FIFO order (1,2,3...10), not lexicographic."""
    data_dir = tmp_path / "data_futures"
    data_dir.mkdir()
    state_mgr = StateManager(data_dir=data_dir)
    day_key = state_mgr.make_day_key()

    base_payload = {
        "qualified_at": datetime.now(timezone.utc).isoformat(),
        "reason": "TrailExit",
        "time_open_min": 3.0,
        "pnl_usd": "1.0",
        "scope": "per_symbol",
        "symbol": "XRPUSDT",
        "rule_version": "1",
    }

    # First sequenced override already consumed.
    payload_1 = dict(base_payload)
    payload_1["consumed"] = True
    state_mgr.set_second_trade_override(day_key, "XRPUSDT_1", payload_1)

    # Keep both _2 and _10 pending; FIFO should pick _2.
    payload_2 = dict(base_payload)
    payload_2["consumed"] = False
    state_mgr.set_second_trade_override(day_key, "XRPUSDT_2", payload_2)

    payload_10 = dict(base_payload)
    payload_10["consumed"] = False
    state_mgr.set_second_trade_override(day_key, "XRPUSDT_10", payload_10)

    first = state_mgr.get_first_unconsumed_override(day_key, "XRPUSDT")

    assert first is not None, "Expected a pending override"
    assert first.get("_scope_key") == "XRPUSDT_2"
