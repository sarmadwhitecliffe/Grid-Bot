import json
from decimal import Decimal
from pathlib import Path

from bot_v2.exit_engine.engine import ExitConditionEngine
from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position
from bot_v2.models.strategy_config import StrategyConfig
from bot_v2.position.tracker import PositionTracker
from bot_v2.position.trailing_stop import TrailingStopCalculator, TrailingStopConfig

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "strategy_configs.json"


def load_configs() -> dict[str, StrategyConfig]:
    with open(CONFIG_PATH, "r") as f:
        raw = json.load(f)
    return {
        symbol_id: StrategyConfig.from_dict(symbol_id, cfg)
        for symbol_id, cfg in raw.items()
    }


def make_base_position(symbol_id: str, side: PositionSide) -> Position:
    """Create a minimal position at entry; then we walk price like live."""
    return Position(
        symbol_id=symbol_id,
        side=side,
        entry_price=Decimal("100.0"),
        initial_amount=Decimal("1.0"),
        current_amount=Decimal("1.0"),
        entry_atr=Decimal("2.0"),
        initial_risk_atr=Decimal("2.0"),
        total_entry_fee=Decimal("0.0"),
        soft_sl_price=Decimal("95.0"),
        hard_sl_price=Decimal("90.0"),
        tp1_price=Decimal("110.0"),
        entry_time=None,
        status=PositionStatus.OPEN,
        current_r=Decimal("0.0"),
        peak_price_since_entry=Decimal("100.0"),
        mfe=Decimal("0.0"),
        mae=Decimal("0.0"),
    )


def step_position_like_live(
    pos: Position, strategy: StrategyConfig, prices: list[Decimal]
) -> list[str | None]:
    """Walk price candle-by-candle and record exit reasons.

    This simulates: open -> price moves -> TP1 hit -> trailing active -> fluctuations.
    """
    reasons: list[str | None] = []

    # Build trailing config from strategy (mirror live wiring in simplified form)
    trail_cfg = TrailingStopConfig(
        trail_sl_atr_mult=strategy.trail_sl_atr_mult,
        trailing_start_r=strategy.trailing_start_r,
        trailing_buffer_pct=Decimal("0.5"),
        min_trailing_r_floor_low=strategy.min_trailing_r_floor_low,
        min_trailing_r_floor_high=strategy.min_trailing_r_floor_high,
    )

    for price in prices:
        # Use real tracker to update MFE/MAE and R metrics
        pos = PositionTracker.update_mfe_mae(pos, price)
        pos = PositionTracker.update_r_multiples(pos, price)

        # Mark TP1a when price crosses tp1 level for the first time
        if not getattr(pos, "tp1a_hit", False):
            if (pos.side == PositionSide.LONG and price >= pos.tp1_price) or (
                pos.side == PositionSide.SHORT and price <= pos.tp1_price
            ):
                pos = pos.copy(tp1a_hit=True, is_trailing_active=True, tp1a_price=price)

        # Update trailing stop level using real calculator
        new_trail = TrailingStopCalculator.calculate_trailing_stop(
            pos, trail_cfg, current_atr=pos.entry_atr, current_price=price
        )
        if new_trail is not None and new_trail.stop_price is not None:
            pos = pos.copy(trailing_sl_price=new_trail.stop_price)

        engine = ExitConditionEngine(
            pos, strategy, current_price=price, current_atr=pos.entry_atr
        )
        exit_cond = engine._check_trailing_stop()
        reasons.append(exit_cond.reason if exit_cond else None)

        if exit_cond is not None:
            # In live trading we would close; break to mimic that.
            break

    return reasons


def test_live_like_walk_per_symbol_long_and_short():
    """Simulate live price walks per symbol for long and short.

    Pattern per side:
    - Start near entry.
    - Increase price gradually to hit TP1 (tp1a).
    - Continue trend to build a strong peak.
    - Pull back enough to either trigger APE or just trailing.
    """
    cfgs = load_configs()
    symbols = ["WIF/USDT", "UNI/USDT", "IMX/USDT", "SYRUP/USDT", "HYPE/USDT"]

    for symbol_id in symbols:
        strategy = cfgs[symbol_id]

        # Long walk: use a stronger move for stricter configs like WIF
        pos_long = make_base_position(symbol_id, PositionSide.LONG)
        prices_long = [
            Decimal("100.0"),  # entry
            Decimal("108.0"),  # drift up
            Decimal("110.0"),  # cross TP1 -> tp1a_hit, trailing starts
            Decimal("135.0"),  # strong trend, build high peak
            Decimal("120.0"),  # sharp pullback: may trigger APE or just trail
        ]
        reasons_long = step_position_like_live(pos_long, strategy, prices_long)

        # We expect that by the end of the walk, if an exit happened,
        # it is either a trailing or aggressive peak exit.
        last_long = next((r for r in reversed(reasons_long) if r is not None), None)
        assert last_long in {
            None,
            "AggressivePeakExit",
            "TrailExit",
        }, f"{symbol_id} long walk unexpected exit: {reasons_long}"

        # Short walk (mirror: price falls instead of rises)
        pos_short = make_base_position(symbol_id, PositionSide.SHORT)
        prices_short = [
            Decimal("100.0"),  # entry
            Decimal("92.0"),  # drift down
            Decimal("90.0"),  # cross TP1 -> tp1a_hit, trailing starts
            Decimal("65.0"),  # strong trend
            Decimal("80.0"),  # sharp pullback
        ]
        reasons_short = step_position_like_live(pos_short, strategy, prices_short)

        last_short = next((r for r in reversed(reasons_short) if r is not None), None)
        assert last_short in {
            None,
            "AggressivePeakExit",
            "TrailExit",
        }, f"{symbol_id} short walk unexpected exit: {reasons_short}"
