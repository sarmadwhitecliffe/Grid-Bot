"""
src/persistence/validator.py
---------------------------
Data validation and recovery system for Grid Bot state.

This module provides:
- StateValidator: Validates data integrity across all state files
- Recovery strategies: auto_reconcile, manual_review_required, full_reset
- Validation reports: detailed reports for auditing

Detects and fixes inconsistencies between:
- Order states and fill logs
- Grid states and active orders
- Capital calculations and trade history
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class ValidationSeverity(str, Enum):
    """Severity levels for validation issues."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class RecoveryStrategy(str, Enum):
    """Recovery strategies available."""

    AUTO_RECONCILE = "AUTO_RECONCILE"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    FULL_RESET = "FULL_RESET"
    SKIP = "SKIP"


@dataclass
class ValidationIssue:
    """A single validation issue."""

    severity: ValidationSeverity
    category: str
    message: str
    file_path: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    recovery_suggestion: Optional[str] = None


@dataclass
class ValidationReport:
    """Complete validation report."""

    timestamp: str
    is_valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    recovery_action: Optional[RecoveryStrategy] = None
    details: Dict[str, Any] = field(default_factory=dict)


class FillEvent:
    """Represents a fill event from fill_log.jsonl."""

    def __init__(self, data: Dict[str, Any]):
        self.symbol = data.get("symbol", "")
        self.timestamp = data.get("timestamp", "")
        self.order_id = data.get("order_id", "")
        self.side = data.get("side", "")
        self.price = Decimal(str(data.get("price", 0)))
        self.amount = Decimal(str(data.get("amount", 0)))
        self.source = data.get("source", "")

    def __repr__(self):
        return f"FillEvent({self.symbol}, {self.side} {self.amount} @ {self.price})"


class StateValidator:
    """
    Validates state integrity across all persistence files.

    Checks:
    - Order states match fill logs
    - Fill counts are consistent
    - Grid states are valid
    - Capital calculations match trades
    """

    def __init__(self, data_dir: Path = Path("data_futures")):
        self.data_dir = Path(data_dir)
        self.fill_log_file = self.data_dir / "fill_log.jsonl"
        self.orders_state_file = self.data_dir / "orders_state.json"
        self.grid_states_file = self.data_dir / "grid_states.json"
        self.grid_exposure_file = self.data_dir / "grid_exposure.json"
        self.grid_trade_history_file = self.data_dir / "grid_trade_history.json"
        self.symbol_capitals_file = self.data_dir / "symbol_capitals.json"
        self.active_positions_file = self.data_dir / "active_positions.json"
        self.trade_history_file = self.data_dir / "trade_history.json"

    def validate_all(self) -> ValidationReport:
        """Run all validations and generate a report."""
        issues: List[ValidationIssue] = []
        stats: Dict[str, Any] = {}

        orders_issues, orders_stats = self.validate_order_states()
        issues.extend(orders_issues)
        stats["order_states"] = orders_stats

        fills_issues, fills_stats = self.validate_fill_consistency()
        issues.extend(fills_issues)
        stats["fills"] = fills_stats

        grid_issues, grid_stats = self.validate_grid_states()
        issues.extend(grid_issues)
        stats["grid_states"] = grid_stats

        capital_issues, capital_stats = self.validate_capitals()
        issues.extend(capital_issues)
        stats["capitals"] = capital_stats

        is_valid = not any(
            i.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
            for i in issues
        )

        recovery_action = self._determine_recovery_action(issues)

        return ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            is_valid=is_valid,
            issues=issues,
            stats=stats,
            recovery_action=recovery_action,
        )

    def validate_order_states(self) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
        """Validate order states against fill logs."""
        issues = []
        stats = {"total_orders": 0, "open_orders": 0, "filled_orders": 0}

        if not self.orders_state_file.exists():
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    category="order_states",
                    message="orders_state.json does not exist",
                    file_path=str(self.orders_state_file),
                )
            )
            return issues, stats

        try:
            with open(self.orders_state_file) as f:
                orders_data = json.load(f)
        except json.JSONDecodeError as e:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="order_states",
                    message=f"Failed to parse orders_state.json: {e}",
                    file_path=str(self.orders_state_file),
                )
            )
            return issues, stats

        orders = orders_data.get("orders", {})
        stats["total_orders"] = len(orders)

        filled_order_ids = set()
        if self.fill_log_file.exists():
            with open(self.fill_log_file) as f:
                for line in f:
                    try:
                        fill = json.loads(line.strip())
                        filled_order_ids.add(fill.get("order_id", ""))
                    except json.JSONDecodeError:
                        pass

        for order_id, order_data in orders.items():
            status = order_data.get("status", "UNKNOWN")
            exchange_id = order_data.get("exchange_order_id", "")

            if status == "OPEN":
                stats["open_orders"] += 1
                if exchange_id in filled_order_ids:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category="order_states",
                            message=f"Order {order_id} marked OPEN but has fills in fill_log",
                            details={
                                "order_id": order_id,
                                "exchange_order_id": exchange_id,
                            },
                            recovery_suggestion="Mark order as FILLED in orders_state.json",
                        )
                    )
            elif status == "FILLED":
                stats["filled_orders"] += 1

        return issues, stats

    def validate_fill_consistency(self) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
        """Validate fill counts match across files."""
        issues = []
        stats = {"total_fills": 0, "by_symbol": {}}

        fills_by_order: Dict[str, FillEvent] = {}

        if not self.fill_log_file.exists():
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="fills",
                    message="fill_log.jsonl does not exist",
                )
            )
            return issues, stats

        try:
            with open(self.fill_log_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        fill_data = json.loads(line)
                        fill = FillEvent(fill_data)
                        fills_by_order[fill.order_id] = fill
                        stats["total_fills"] += 1

                        symbol = fill.symbol
                        if symbol not in stats["by_symbol"]:
                            stats["by_symbol"][symbol] = {
                                "buy": 0,
                                "sell": 0,
                                "total": 0,
                            }
                        stats["by_symbol"][symbol][fill.side] += 1
                        stats["by_symbol"][symbol]["total"] += 1
                    except (json.JSONDecodeError, KeyError) as e:
                        issues.append(
                            ValidationIssue(
                                severity=ValidationSeverity.WARNING,
                                category="fills",
                                message=f"Failed to parse fill log line: {e}",
                            )
                        )
        except Exception as e:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="fills",
                    message=f"Failed to read fill_log.jsonl: {e}",
                )
            )

        buy_counts = sum(s.get("buy", 0) for s in stats["by_symbol"].values())
        sell_counts = sum(s.get("sell", 0) for s in stats["by_symbol"].values())

        if buy_counts != sell_counts:
            diff = buy_counts - sell_counts
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="fills",
                    message=f"Unbalanced fills: {buy_counts} buys vs {sell_counts} sells (diff: {diff})",
                    details={"buy_count": buy_counts, "sell_count": sell_counts},
                    recovery_suggestion="Some fills may be orphaned - review unmatched fills",
                )
            )

        return issues, stats

    def validate_grid_states(self) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
        """Validate grid states are consistent."""
        issues = []
        stats = {"total_sessions": 0, "active_sessions": 0, "symbols": []}

        if not self.grid_states_file.exists():
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    category="grid_states",
                    message="grid_states.json does not exist",
                )
            )
            return issues, stats

        try:
            with open(self.grid_states_file) as f:
                grid_data = json.load(f)
        except json.JSONDecodeError as e:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="grid_states",
                    message=f"Failed to parse grid_states.json: {e}",
                )
            )
            return issues, stats

        stats["total_sessions"] = len(grid_data)

        for symbol, state in grid_data.items():
            stats["symbols"].append(symbol)
            is_active = state.get("is_active", False)

            if is_active:
                stats["active_sessions"] += 1
                active_orders = state.get("active_orders", {})

                if not active_orders:
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            category="grid_states",
                            message=f"Grid for {symbol} marked active but has no orders",
                            details={"symbol": symbol},
                        )
                    )

                if not state.get("centre_price"):
                    issues.append(
                        ValidationIssue(
                            severity=ValidationSeverity.ERROR,
                            category="grid_states",
                            message=f"Grid for {symbol} missing centre_price",
                            details={"symbol": symbol},
                        )
                    )

        if self.grid_exposure_file.exists():
            try:
                with open(self.grid_exposure_file) as f:
                    exposure = json.load(f)

                for symbol in exposure:
                    if symbol not in stats["symbols"]:
                        issues.append(
                            ValidationIssue(
                                severity=ValidationSeverity.WARNING,
                                category="grid_states",
                                message=f"Symbol {symbol} in exposure but not in grid_states",
                            )
                        )
            except json.JSONDecodeError:
                pass

        return issues, stats

    def validate_capitals(self) -> Tuple[List[ValidationIssue], Dict[str, Any]]:
        """Validate capital consistency with trades."""
        issues = []
        stats = {"total_symbols": 0, "symbols": {}}

        if not self.symbol_capitals_file.exists():
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    category="capitals",
                    message="symbol_capitals.json does not exist",
                )
            )
            return issues, stats

        try:
            with open(self.symbol_capitals_file) as f:
                capitals_data = json.load(f)
        except json.JSONDecodeError as e:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    category="capitals",
                    message=f"Failed to parse symbol_capitals.json: {e}",
                )
            )
            return issues, stats

        stats["total_symbols"] = len(capitals_data)

        for symbol, capital_data in capitals_data.items():
            if isinstance(capital_data, dict):
                capital = Decimal(str(capital_data.get("capital", 0)))
                tier = capital_data.get("tier", "UNKNOWN")
            else:
                capital = Decimal(str(capital_data))
                tier = "UNKNOWN"

            stats["symbols"][symbol] = {
                "capital": str(capital),
                "tier": tier,
            }

            if capital <= 0:
                issues.append(
                    ValidationIssue(
                        severity=ValidationSeverity.ERROR,
                        category="capitals",
                        message=f"Symbol {symbol} has zero or negative capital",
                        details={"symbol": symbol, "capital": str(capital)},
                    )
                )

        return issues, stats

    def reconcile_fills(self) -> Dict[str, Any]:
        """
        Reconcile fills to identify orphaned entries and closed trades.

        Returns:
            Dictionary with reconciliation results
        """
        result = {
            "matched_closes": [],
            "orphaned_buys": [],
            "orphaned_sells": [],
            "total_pnl": Decimal("0"),
        }

        fills_by_symbol: Dict[str, Dict[str, List[FillEvent]]] = {}

        if not self.fill_log_file.exists():
            return result

        with open(self.fill_log_file) as f:
            for line in f:
                try:
                    fill_data = json.loads(line.strip())
                    fill = FillEvent(fill_data)

                    if fill.symbol not in fills_by_symbol:
                        fills_by_symbol[fill.symbol] = {"buy": [], "sell": []}

                    side_key = fill.side.lower()
                    if side_key in fills_by_symbol[fill.symbol]:
                        fills_by_symbol[fill.symbol][side_key].append(fill)
                except (json.JSONDecodeError, KeyError):
                    continue

        for symbol, fills in fills_by_symbol.items():
            buys = sorted(fills["buy"], key=lambda f: f.timestamp)
            sells = sorted(fills["sell"], key=lambda f: f.timestamp)

            buy_idx = 0
            sell_idx = 0

            while buy_idx < len(buys) and sell_idx < len(sells):
                buy = buys[buy_idx]
                sell = sells[sell_idx]

                matched_qty = min(buy.amount, sell.amount)

                if matched_qty > 0:
                    pnl = (sell.price - buy.price) * matched_qty
                    result["total_pnl"] += pnl
                    result["matched_closes"].append(
                        {
                            "symbol": symbol,
                            "buy_order_id": buy.order_id,
                            "sell_order_id": sell.order_id,
                            "quantity": str(matched_qty),
                            "pnl": str(pnl),
                        }
                    )

                    buy.amount -= matched_qty
                    sell.amount -= matched_qty

                if buy.amount <= 0:
                    buy_idx += 1
                if sell.amount <= 0:
                    sell_idx += 1

            while buy_idx < len(buys) and buys[buy_idx].amount > 0:
                result["orphaned_buys"].append(
                    {
                        "symbol": symbol,
                        "order_id": buys[buy_idx].order_id,
                        "amount": str(buys[buy_idx].amount),
                        "price": str(buys[buy_idx].price),
                    }
                )
                buy_idx += 1

            while sell_idx < len(sells) and sells[sell_idx].amount > 0:
                result["orphaned_sells"].append(
                    {
                        "symbol": symbol,
                        "order_id": sells[sell_idx].order_id,
                        "amount": str(sells[sell_idx].amount),
                        "price": str(sells[sell_idx].price),
                    }
                )
                sell_idx += 1

        return result

    def _determine_recovery_action(
        self, issues: List[ValidationIssue]
    ) -> Optional[RecoveryStrategy]:
        """Determine appropriate recovery strategy based on issues."""
        critical_count = sum(
            1 for i in issues if i.severity == ValidationSeverity.CRITICAL
        )
        error_count = sum(1 for i in issues if i.severity == ValidationSeverity.ERROR)
        warning_count = sum(
            1 for i in issues if i.severity == ValidationSeverity.WARNING
        )

        if critical_count > 0:
            return RecoveryStrategy.FULL_RESET

        if error_count > 2:
            return RecoveryStrategy.MANUAL_REVIEW_REQUIRED

        if error_count > 0:
            return RecoveryStrategy.AUTO_RECONCILE

        if warning_count > 5:
            return RecoveryStrategy.AUTO_RECONCILE

        return None

    def auto_reconcile(self, report: ValidationReport) -> Dict[str, Any]:
        """
        Attempt automatic reconciliation of issues.

        Returns:
            Dictionary with reconciliation results
        """
        results = {
            "fixed_issues": [],
            "failed_fixes": [],
            "backup_created": False,
        }

        if not report.is_valid:
            backup_path = (
                self.data_dir
                / f"pre_reconciliation_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            )
            try:
                import shutil

                shutil.copytree(
                    self.data_dir,
                    backup_path,
                    ignore=lambda x, y: ["wal", "checkpoints"],
                )
                results["backup_created"] = True
                results["backup_path"] = str(backup_path)
            except Exception as e:
                logger.error(f"Failed to create backup: {e}")
                results["failed_fixes"].append(f"Failed to create backup: {e}")

        for issue in report.issues:
            if issue.severity in (
                ValidationSeverity.ERROR,
                ValidationSeverity.CRITICAL,
            ):
                if (
                    issue.category == "order_states"
                    and "marked OPEN but has fills" in issue.message
                ):
                    results["fixed_issues"].append(
                        {
                            "issue": issue.message,
                            "action": "Manual review required - order state mismatch",
                        }
                    )

        return results

    def generate_report_summary(self, report: ValidationReport) -> str:
        """Generate human-readable summary of validation report."""
        lines = [
            f"=== Validation Report ===",
            f"Timestamp: {report.timestamp}",
            f"Valid: {report.is_valid}",
            f"",
            f"Issues: {len(report.issues)}",
        ]

        for severity in ValidationSeverity:
            count = sum(1 for i in report.issues if i.severity == severity)
            if count > 0:
                lines.append(f"  {severity.value}: {count}")

        lines.append("")

        if report.issues:
            lines.append("=== Details ===")
            for issue in report.issues:
                lines.append(
                    f"[{issue.severity.value}] {issue.category}: {issue.message}"
                )
                if issue.recovery_suggestion:
                    lines.append(f"  -> {issue.recovery_suggestion}")

        if report.recovery_action:
            lines.append(f"")
            lines.append(f"Recommended Action: {report.recovery_action.value}")

        return "\n".join(lines)


class DataRecoveryManager:
    """
    Manages data recovery operations.
    """

    def __init__(self, data_dir: Path = Path("data_futures")):
        self.data_dir = Path(data_dir)
        self.validator = StateValidator(data_dir)
        self.validation_reports_dir = self.data_dir / "validation_reports"
        self.validation_reports_dir.mkdir(parents=True, exist_ok=True)

    def validate_and_recover(self, auto_reconcile: bool = True) -> ValidationReport:
        """
        Run validation and optionally attempt recovery.

        Args:
            auto_reconcile: If True, attempt automatic reconciliation

        Returns:
            ValidationReport with results
        """
        logger.info("Starting data validation and recovery...")

        report = self.validator.validate_all()

        self._save_report(report)

        logger.info(
            f"Validation complete: valid={report.is_valid}, issues={len(report.issues)}"
        )

        if report.recovery_action and auto_reconcile:
            if report.recovery_action in (
                RecoveryStrategy.AUTO_RECONCILE,
                RecoveryStrategy.FULL_RESET,
            ):
                logger.info(
                    f"Attempting recovery with strategy: {report.recovery_action}"
                )
                recovery_result = self.validator.auto_reconcile(report)

                report.details["recovery_result"] = recovery_result

                self._save_report(report)

        return report

    def _save_report(self, report: ValidationReport) -> None:
        """Save validation report to disk."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        report_file = self.validation_reports_dir / f"validation_{timestamp}.json"

        data = {
            "timestamp": report.timestamp,
            "is_valid": report.is_valid,
            "recovery_action": report.recovery_action.value
            if report.recovery_action
            else None,
            "stats": report.stats,
            "issues": [
                {
                    "severity": i.severity.value,
                    "category": i.category,
                    "message": i.message,
                    "file_path": i.file_path,
                    "details": i.details,
                    "recovery_suggestion": i.recovery_suggestion,
                }
                for i in report.issues
            ],
        }

        with open(report_file, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Saved validation report to {report_file}")

    def get_latest_report(self) -> Optional[ValidationReport]:
        """Get the most recent validation report."""
        reports = sorted(self.validation_reports_dir.glob("validation_*.json"))

        if not reports:
            return None

        latest = reports[-1]

        with open(latest) as f:
            data = json.load(f)

        issues = [
            ValidationIssue(
                severity=ValidationSeverity(i["severity"]),
                category=i["category"],
                message=i["message"],
                file_path=i.get("file_path"),
                details=i.get("details", {}),
                recovery_suggestion=i.get("recovery_suggestion"),
            )
            for i in data.get("issues", [])
        ]

        return ValidationReport(
            timestamp=data["timestamp"],
            is_valid=data["is_valid"],
            issues=issues,
            stats=data.get("stats", {}),
            recovery_action=RecoveryStrategy(data["recovery_action"])
            if data.get("recovery_action")
            else None,
        )
