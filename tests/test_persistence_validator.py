"""
tests/test_persistence_validator.py
--------------------------------
Unit tests for the data validation system.
"""

import json
import tempfile
import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.persistence.validator import (
    StateValidator,
    DataRecoveryManager,
    ValidationIssue,
    ValidationSeverity,
    RecoveryStrategy,
    ValidationReport,
    FillEvent,
)


class TestFillEvent:
    """Tests for FillEvent class."""

    def test_fill_event_creation(self):
        """Test creating a FillEvent from dict."""
        data = {
            "symbol": "BTC/USDT",
            "timestamp": "2026-03-12T10:00:00Z",
            "order_id": "order_123",
            "side": "buy",
            "price": 50000.0,
            "amount": 0.1,
            "source": "grid",
        }

        fill = FillEvent(data)

        assert fill.symbol == "BTC/USDT"
        assert fill.side == "buy"
        assert fill.price == Decimal("50000")
        assert fill.amount == Decimal("0.1")

    def test_fill_event_repr(self):
        """Test FillEvent string representation."""
        data = {
            "symbol": "ETH/USDT",
            "side": "sell",
            "price": 2000.0,
            "amount": 1.0,
            "timestamp": "",
            "order_id": "",
            "source": "",
        }

        fill = FillEvent(data)
        repr_str = repr(fill)

        assert "ETH/USDT" in repr_str
        assert "sell" in repr_str


class TestStateValidator:
    """Tests for StateValidator class."""

    @pytest.fixture
    def temp_data_dir(self):
        """Create a temporary data directory."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def validator(self, temp_data_dir):
        """Create a validator with temp directory."""
        return StateValidator(data_dir=temp_data_dir)

    def test_validator_creation(self, validator):
        """Test validator initialization."""
        assert validator.data_dir is not None

    def test_validate_missing_files(self, validator):
        """Test validation when files don't exist."""
        report = validator.validate_all()

        assert report is not None
        assert len(report.issues) > 0

    def test_validate_order_states_missing_file(self, validator):
        """Test order validation with missing file."""
        issues, stats = validator.validate_order_states()

        assert any(i.severity == ValidationSeverity.INFO for i in issues)

    def test_validate_capitals_missing_file(self, validator):
        """Test capital validation with missing file."""
        issues, stats = validator.validate_capitals()

        assert any(i.severity == ValidationSeverity.WARNING for i in issues)

    def test_validate_grid_states_missing_file(self, validator):
        """Test grid state validation with missing file."""
        issues, stats = validator.validate_grid_states()

        assert any(i.severity == ValidationSeverity.INFO for i in issues)

    def test_validate_fill_consistency_empty(self, validator):
        """Test fill validation with empty file."""
        issues, stats = validator.validate_fill_consistency()

        assert stats["total_fills"] == 0


class TestStateValidatorWithData:
    """Tests with actual data files."""

    @pytest.fixture
    def data_dir_with_orders(self):
        """Create temp directory with order data."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir)

        orders_file = data_dir / "orders_state.json"
        orders = {
            "orders": {
                "order_1": {"status": "OPEN", "exchange_order_id": "ex_1"},
                "order_2": {"status": "FILLED", "exchange_order_id": "ex_2"},
            }
        }
        with open(orders_file, "w") as f:
            json.dump(orders, f)

        yield data_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_validate_orders_with_data(self, data_dir_with_orders):
        """Test order validation with data."""
        validator = StateValidator(data_dir=data_dir_with_orders)

        issues, stats = validator.validate_order_states()

        assert stats["total_orders"] == 2
        assert stats["open_orders"] == 1
        assert stats["filled_orders"] == 1


class TestRecoveryStrategies:
    """Tests for recovery determination."""

    def test_determine_recovery_no_issues(self):
        """Test recovery with no issues."""
        validator = StateValidator()

        report = ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_valid=True,
            issues=[],
        )

        action = validator._determine_recovery_action([])

        assert action is None

    def test_determine_recovery_critical(self):
        """Test recovery with critical issues."""
        validator = StateValidator()

        issues = [
            ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                category="test",
                message="Critical error",
            )
        ]

        action = validator._determine_recovery_action(issues)

        assert action == RecoveryStrategy.FULL_RESET

    def test_determine_recovery_many_errors(self):
        """Test recovery with multiple errors."""
        validator = StateValidator()

        issues = [
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                category="test",
                message=f"Error {i}",
            )
            for i in range(5)
        ]

        action = validator._determine_recovery_action(issues)

        assert action == RecoveryStrategy.MANUAL_REVIEW_REQUIRED

    def test_determine_recovery_few_errors(self):
        """Test recovery with few errors."""
        validator = StateValidator()

        issues = [
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                category="test",
                message="Error 1",
            ),
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                category="test",
                message="Error 2",
            ),
        ]

        action = validator._determine_recovery_action(issues)

        assert action == RecoveryStrategy.AUTO_RECONCILE


class TestFillReconciliation:
    """Tests for fill reconciliation."""

    @pytest.fixture
    def data_dir_with_fills(self):
        """Create temp directory with fill data."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir)

        fill_log = data_dir / "fill_log.jsonl"
        with open(fill_log, "w") as f:
            f.write(
                json.dumps(
                    {
                        "symbol": "BTC/USDT",
                        "timestamp": "2026-03-12T10:00:00Z",
                        "order_id": "buy_1",
                        "side": "buy",
                        "price": 50000,
                        "amount": 0.1,
                        "source": "grid",
                    }
                )
                + "\n"
            )
            f.write(
                json.dumps(
                    {
                        "symbol": "BTC/USDT",
                        "timestamp": "2026-03-12T10:01:00Z",
                        "order_id": "sell_1",
                        "side": "sell",
                        "price": 51000,
                        "amount": 0.1,
                        "source": "grid",
                    }
                )
                + "\n"
            )

        yield data_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_reconcile_balanced_fills(self, data_dir_with_fills):
        """Test reconciliation with balanced buys/sells."""
        validator = StateValidator(data_dir=data_dir_with_fills)

        result = validator.reconcile_fills()

        assert len(result["matched_closes"]) == 1
        assert Decimal(result["total_pnl"]) > 0

    def test_validate_fill_consistency_balanced(self, data_dir_with_fills):
        """Test validation with balanced fills."""
        validator = StateValidator(data_dir=data_dir_with_fills)

        issues, stats = validator.validate_fill_consistency()

        buy_count = sum(s.get("buy", 0) for s in stats.get("by_symbol", {}).values())
        sell_count = sum(s.get("sell", 0) for s in stats.get("by_symbol", {}).values())

        assert buy_count == sell_count


class TestDataRecoveryManager:
    """Tests for DataRecoveryManager."""

    @pytest.fixture
    def recovery_manager(self):
        """Create a recovery manager with temp directory."""
        temp_dir = tempfile.mkdtemp()
        mgr = DataRecoveryManager(data_dir=Path(temp_dir))
        yield mgr
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_recovery_manager_creation(self, recovery_manager):
        """Test recovery manager initialization."""
        assert recovery_manager.validator is not None
        assert recovery_manager.validation_reports_dir.exists()

    def test_validate_and_recover(self, recovery_manager):
        """Test validation and recovery process."""
        report = recovery_manager.validate_and_recover(auto_reconcile=False)

        assert report is not None
        assert isinstance(report.is_valid, bool)

    def test_get_latest_report_none(self, recovery_manager):
        """Test getting latest report when none exist."""
        report = recovery_manager.get_latest_report()

        assert report is None


class TestValidationReport:
    """Tests for ValidationReport."""

    def test_validation_report_creation(self):
        """Test creating a validation report."""
        report = ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_valid=True,
            issues=[],
            stats={"test": 1},
        )

        assert report.is_valid is True
        assert report.stats["test"] == 1

    def test_validation_report_with_issues(self):
        """Test report with issues."""
        issues = [
            ValidationIssue(
                severity=ValidationSeverity.ERROR,
                category="test",
                message="Test error",
            )
        ]

        report = ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_valid=False,
            issues=issues,
        )

        assert report.is_valid is False
        assert len(report.issues) == 1


class TestValidatorIntegration:
    """Integration tests for validator."""

    @pytest.fixture
    def complete_data_dir(self):
        """Create directory with all data files."""
        temp_dir = tempfile.mkdtemp()
        data_dir = Path(temp_dir)

        orders = {"orders": {}}
        with open(data_dir / "orders_state.json", "w") as f:
            json.dump(orders, f)

        with open(data_dir / "fill_log.jsonl", "w") as f:
            pass

        grid_states = {}
        with open(data_dir / "grid_states.json", "w") as f:
            json.dump(grid_states, f)

        capitals = {}
        with open(data_dir / "symbol_capitals.json", "w") as f:
            json.dump(capitals, f)

        yield data_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_full_validation_cycle(self, complete_data_dir):
        """Test complete validation cycle."""
        validator = StateValidator(data_dir=complete_data_dir)

        report = validator.validate_all()

        assert report is not None
        assert isinstance(report.is_valid, bool)
        assert "order_states" in report.stats
        assert "fills" in report.stats
        assert "grid_states" in report.stats
        assert "capitals" in report.stats
