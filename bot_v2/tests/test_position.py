"""
Tests for models.position module.
"""

from datetime import datetime, timezone
from decimal import Decimal

from bot_v2.models.enums import PositionSide, PositionStatus
from bot_v2.models.position import Position


class TestPositionCreation:
    """Test Position creation and initialization."""

    def test_create_position_with_required_fields(self):
        """Test creating position with only required fields."""
        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        assert pos.symbol_id == "BTCUSDT"
        assert pos.side == PositionSide.LONG
        assert pos.entry_price == Decimal("50000")
        assert pos.status == PositionStatus.OPEN  # Default
        assert pos.current_amount == Decimal("0.01")  # Auto-set to initial_amount
        assert pos.peak_price_since_entry == Decimal("50000")  # Auto-set to entry_price

    def test_create_position_with_optional_fields(self):
        """Test creating position with optional fields."""
        pos = Position(
            symbol_id="ETHUSDT",
            side=PositionSide.SHORT,
            entry_price=Decimal("3000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("1.0"),
            entry_atr=Decimal("50"),
            initial_risk_atr=Decimal("25"),
            soft_sl_price=Decimal("3050"),
            hard_sl_price=Decimal("3100"),
            tp1_price=Decimal("2900"),
            total_entry_fee=Decimal("3.0"),
            tp1a_price=Decimal("2950"),
            mfe=Decimal("100"),
            mae=Decimal("50"),
            is_trailing_active=True,
        )

        assert pos.tp1a_price == Decimal("2950")
        assert pos.mfe == Decimal("100")
        assert pos.mae == Decimal("50")
        assert pos.is_trailing_active is True
        assert pos.tp1a_hit is False  # Default


class TestPositionSerialization:
    """Test Position to_dict and from_dict methods."""

    def test_to_dict(self):
        """Test converting position to dictionary."""
        now = datetime.now(timezone.utc)
        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=now,
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
            mfe=Decimal("500"),
            mae=Decimal("100"),
        )

        data = pos.to_dict()

        assert data["symbol_id"] == "BTCUSDT"
        assert data["side"] == "long"
        assert data["entry_price"] == "50000.00000000"
        assert data["entry_time"] == now.isoformat()
        assert data["mfe"] == "500.00000000"
        assert data["status"] == "open"

    def test_from_dict(self):
        """Test creating position from dictionary."""
        now = datetime.now(timezone.utc)
        data = {
            "symbol_id": "BTCUSDT",
            "side": "long",
            "entry_price": "50000.00",
            "entry_time": now.isoformat(),
            "initial_amount": "0.01",
            "entry_atr": "500",
            "initial_risk_atr": "250",
            "soft_sl_price": "49500",
            "hard_sl_price": "49000",
            "tp1_price": "51000",
            "total_entry_fee": "0.5",
            "mfe": "500",
            "mae": "100",
            "status": "open",
        }

        pos = Position.from_dict(data)

        assert pos.symbol_id == "BTCUSDT"
        assert pos.side == PositionSide.LONG
        assert pos.entry_price == Decimal("50000.00")
        assert pos.mfe == Decimal("500")
        assert pos.mae == Decimal("100")
        assert pos.status == PositionStatus.OPEN

    def test_roundtrip_serialization(self):
        """Test that to_dict -> from_dict preserves data."""
        now = datetime.now(timezone.utc)
        original = Position(
            symbol_id="ETHUSDT",
            side=PositionSide.SHORT,
            entry_price=Decimal("3000"),
            entry_time=now,
            initial_amount=Decimal("1.0"),
            entry_atr=Decimal("50"),
            initial_risk_atr=Decimal("25"),
            soft_sl_price=Decimal("3050"),
            hard_sl_price=Decimal("3100"),
            tp1_price=Decimal("2900"),
            total_entry_fee=Decimal("3.0"),
            mfe=Decimal("150"),
            peak_favorable_r=Decimal("2.5"),
            is_trailing_active=True,
            exit_conditions_met=["trailing_stop", "time_limit"],
        )

        data = original.to_dict()
        restored = Position.from_dict(data)

        assert restored.symbol_id == original.symbol_id
        assert restored.side == original.side
        assert restored.entry_price == original.entry_price
        assert restored.mfe == original.mfe
        assert restored.peak_favorable_r == original.peak_favorable_r
        assert restored.is_trailing_active == original.is_trailing_active
        assert restored.exit_conditions_met == original.exit_conditions_met

    def test_adverse_scaleout_timestamp_roundtrip(self):
        """Ensure adverse_scaleout_timestamp is preserved as datetime."""
        now = datetime.now(timezone.utc)
        scaleout_time = datetime.now(timezone.utc)

        original = Position(
            symbol_id="SOLUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("150"),
            entry_time=now,
            initial_amount=Decimal("2.0"),
            entry_atr=Decimal("5"),
            initial_risk_atr=Decimal("2.5"),
            soft_sl_price=Decimal("147"),
            hard_sl_price=Decimal("140"),
            tp1_price=Decimal("155"),
            total_entry_fee=Decimal("0.2"),
            scaled_out_on_adverse=True,
            adverse_scaleout_timestamp=scaleout_time,
        )

        data = original.to_dict()
        restored = Position.from_dict(data)

        assert restored.scaled_out_on_adverse is True
        assert restored.adverse_scaleout_timestamp == scaleout_time
        assert isinstance(restored.adverse_scaleout_timestamp, datetime)


class TestPositionCopy:
    """Test Position copy method."""

    def test_copy_without_changes(self):
        """Test copying position without modifications."""
        original = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        copied = original.copy()

        assert copied.symbol_id == original.symbol_id
        assert copied.entry_price == original.entry_price
        assert copied is not original  # Different object

    def test_copy_with_changes(self):
        """Test copying position with field updates."""
        original = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
            current_amount=Decimal("0.01"),
        )

        # Simulate partial close
        updated = original.copy(
            current_amount=Decimal("0.005"),
            status=PositionStatus.PARTIALLY_CLOSED,
            realized_profit=Decimal("250"),
        )

        # Original unchanged
        assert original.current_amount == Decimal("0.01")
        assert original.status == PositionStatus.OPEN
        assert original.realized_profit == Decimal("0")

        # Updated has changes
        assert updated.current_amount == Decimal("0.005")
        assert updated.status == PositionStatus.PARTIALLY_CLOSED
        assert updated.realized_profit == Decimal("250")

        # Other fields preserved
        assert updated.symbol_id == original.symbol_id
        assert updated.entry_price == original.entry_price


class TestPositionDefaults:
    """Test Position default value initialization."""

    def test_default_status_is_open(self):
        """Test default status is OPEN."""
        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        assert pos.status == PositionStatus.OPEN

    def test_default_performance_metrics_zero(self):
        """Test performance metrics default to zero."""
        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        assert pos.mfe == Decimal("0")
        assert pos.mae == Decimal("0")
        assert pos.realized_profit == Decimal("0.0")
        assert pos.peak_favorable_r == Decimal("0")
        assert pos.current_r == Decimal("0")

    def test_default_flags_false(self):
        """Test boolean flags default to False."""
        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        assert pos.tp1a_hit is False
        assert pos.is_trailing_active is False
        assert pos.moved_to_breakeven is False
        assert pos.scaled_out_on_adverse is False

    def test_creation_timestamp_auto_generated(self):
        """Test creation_timestamp is auto-generated if not provided."""
        import time

        before = int(time.time() * 1000)

        pos = Position(
            symbol_id="BTCUSDT",
            side=PositionSide.LONG,
            entry_price=Decimal("50000"),
            entry_time=datetime.now(timezone.utc),
            initial_amount=Decimal("0.01"),
            entry_atr=Decimal("500"),
            initial_risk_atr=Decimal("250"),
            soft_sl_price=Decimal("49500"),
            hard_sl_price=Decimal("49000"),
            tp1_price=Decimal("51000"),
            total_entry_fee=Decimal("0.5"),
        )

        after = int(time.time() * 1000)

        assert pos.creation_timestamp is not None
        assert before <= pos.creation_timestamp <= after
