"""
src/monitoring/health.py
--------------------
Health monitoring and metrics for Grid Bot persistence.

This module provides:
- HealthMonitor: Track health metrics for persistence layer
- Health check endpoint (JSON-based)
- Prometheus-compatible metrics export
- Alerting integration
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthMetrics:
    """Health metrics snapshot."""

    timestamp: str
    save_operations: int = 0
    failed_saves: int = 0
    total_save_duration_ms: float = 0.0
    corruption_events: int = 0
    recovery_operations: int = 0
    successful_recoveries: int = 0
    checkpoint_operations: int = 0
    wal_entries: int = 0


class HealthMonitor:
    """
    Monitors health of the persistence layer.

    Features:
    - Track save operations and latency
    - Count corruption events and recoveries
    - Track checkpoint operations
    - Export Prometheus metrics
    - Health check endpoint (JSON)
    """

    HEALTH_FILE = "health_status.json"
    METRICS_FILE = "health_metrics.json"

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        alert_on_corruption: bool = True,
    ):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.alert_on_corruption = alert_on_corruption

        self._lock = threading.RLock()
        self._metrics = HealthMetrics(timestamp=datetime.now(timezone.utc).isoformat())
        self._start_time = time.time()

        self.health_file = self.data_dir / self.HEALTH_FILE
        self.metrics_file = self.data_dir / self.METRICS_FILE

    def record_save(self, duration_ms: float, success: bool = True) -> None:
        """Record a save operation."""
        with self._lock:
            self._metrics.save_operations += 1
            self._metrics.total_save_duration_ms += duration_ms
            if not success:
                self._metrics.failed_saves += 1
            self._persist_metrics()

    def record_corruption(self) -> None:
        """Record a corruption event."""
        with self._lock:
            self._metrics.corruption_events += 1
            self._persist_metrics()

            if self.alert_on_corruption:
                self._trigger_alert("corruption", "Data corruption detected")

    def record_recovery(self, success: bool = True) -> None:
        """Record a recovery operation."""
        with self._lock:
            self._metrics.recovery_operations += 1
            if success:
                self._metrics.successful_recoveries += 1
            self._persist_metrics()

    def record_checkpoint(self) -> None:
        """Record a checkpoint operation."""
        with self._lock:
            self._metrics.checkpoint_operations += 1
            self._persist_metrics()

    def record_wal_entry(self) -> None:
        """Record a WAL entry."""
        with self._lock:
            self._metrics.wal_entries += 1

    def get_health_status(self) -> Dict[str, Any]:
        """Get current health status."""
        with self._lock:
            uptime_seconds = time.time() - self._start_time

            avg_save_duration = 0.0
            if self._metrics.save_operations > 0:
                avg_save_duration = (
                    self._metrics.total_save_duration_ms / self._metrics.save_operations
                )

            health = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": uptime_seconds,
                "status": self._calculate_status(),
                "metrics": {
                    "save_operations": self._metrics.save_operations,
                    "failed_saves": self._metrics.failed_saves,
                    "avg_save_duration_ms": avg_save_duration,
                    "corruption_events": self._metrics.corruption_events,
                    "recovery_operations": self._metrics.recovery_operations,
                    "successful_recoveries": self._metrics.successful_recoveries,
                    "checkpoint_operations": self._metrics.checkpoint_operations,
                    "wal_entries": self._metrics.wal_entries,
                },
                "integrity": self._check_integrity(),
            }

            self._persist_health(health)

            return health

    def _calculate_status(self) -> str:
        """Calculate overall health status."""
        if self._metrics.corruption_events > 0:
            return "DEGRADED"

        if self._metrics.failed_saves > 0:
            fail_rate = self._metrics.failed_saves / max(
                1, self._metrics.save_operations
            )
            if fail_rate > 0.1:
                return "DEGRADED"

        return "HEALTHY"

    def _check_integrity(self) -> Dict[str, Any]:
        """Check data integrity."""
        try:
            from src.persistence.integrity import IntegrityManager

            integrity = IntegrityManager(data_dir=self.data_dir)
            result = integrity.verify_all()
            return {
                "valid": result.get("valid", False),
                "files_checked": result.get("files_checked", 0),
                "files_failed": result.get("files_failed", 0),
            }
        except Exception as e:
            logger.warning(f"Integrity check failed: {e}")
            return {"valid": None, "error": str(e)}

    def _persist_health(self, health: Dict[str, Any]) -> None:
        """Persist health status to file."""
        try:
            with open(self.health_file, "w") as f:
                json.dump(health, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist health status: {e}")

    def _persist_metrics(self) -> None:
        """Persist metrics to file."""
        try:
            with self._lock:
                metrics = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "save_operations": self._metrics.save_operations,
                    "failed_saves": self._metrics.failed_saves,
                    "total_save_duration_ms": self._metrics.total_save_duration_ms,
                    "corruption_events": self._metrics.corruption_events,
                    "recovery_operations": self._metrics.recovery_operations,
                    "successful_recoveries": self._metrics.successful_recoveries,
                    "checkpoint_operations": self._metrics.checkpoint_operations,
                    "wal_entries": self._metrics.wal_entries,
                }

            with open(self.metrics_file, "w") as f:
                json.dump(metrics, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to persist metrics: {e}")

    def _trigger_alert(self, alert_type: str, message: str) -> None:
        """Trigger an alert."""
        logger.warning(f"ALERT [{alert_type}]: {message}")

        try:
            self._send_telegram_alert(alert_type, message)
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")

    def _send_telegram_alert(self, alert_type: str, message: str) -> None:
        """Send Telegram alert if available."""
        try:
            from src.monitoring.alerting import TelegramAlerter

            alerter = TelegramAlerter(token="", chat_id="")
            alerter.send_message(f"[{alert_type.upper()}] {message}")
        except ImportError:
            pass

    def export_prometheus_metrics(self) -> str:
        """Export metrics in Prometheus format."""
        with self._lock:
            avg_save_duration = 0.0
            if self._metrics.save_operations > 0:
                avg_save_duration = (
                    self._metrics.total_save_duration_ms / self._metrics.save_operations
                )

        lines = [
            "# HELP grid_bot_save_operations_total Total number of save operations",
            "# TYPE grid_bot_save_operations_total counter",
            f"grid_bot_save_operations_total {self._metrics.save_operations}",
            "",
            "# HELP grid_bot_failed_saves_total Total number of failed save operations",
            "# TYPE grid_bot_failed_saves_total counter",
            f"grid_bot_failed_saves_total {self._metrics.failed_saves}",
            "",
            "# HELP grid_bot_avg_save_duration_ms Average save operation duration in milliseconds",
            "# TYPE grid_bot_avg_save_duration_ms gauge",
            f"grid_bot_avg_save_duration_ms {avg_save_duration}",
            "",
            "# HELP grid_bot_corruption_events_total Total number of corruption events",
            "# TYPE grid_bot_corruption_events_total counter",
            f"grid_bot_corruption_events_total {self._metrics.corruption_events}",
            "",
            "# HELP grid_bot_recovery_operations_total Total number of recovery operations",
            "# TYPE grid_bot_recovery_operations_total counter",
            f"grid_bot_recovery_operations_total {self._metrics.recovery_operations}",
            "",
            "# HELP grid_bot_checkpoint_operations_total Total number of checkpoint operations",
            "# TYPE grid_bot_checkpoint_operations_total counter",
            f"grid_bot_checkpoint_operations_total {self._metrics.checkpoint_operations}",
            "",
            "# HELP grid_bot_wal_entries_total Total number of WAL entries written",
            "# TYPE grid_bot_wal_entries_total counter",
            f"grid_bot_wal_entries_total {self._metrics.wal_entries}",
            "",
        ]

        return "\n".join(lines)

    def reset_metrics(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._metrics = HealthMetrics(
                timestamp=datetime.now(timezone.utc).isoformat()
            )
            self._start_time = time.time()
            self._persist_metrics()


class HealthCheckEndpoint:
    """Simple HTTP endpoint for health checks (file-based)."""

    def __init__(self, health_monitor: HealthMonitor):
        self.health_monitor = health_monitor

    def get_health(self) -> Dict[str, Any]:
        """Get health check response."""
        return self.health_monitor.get_health_status()

    def get_ready(self) -> Dict[str, Any]:
        """Get readiness check response."""
        health = self.get_health()

        if health["status"] == "HEALTHY":
            return {"ready": True, "status": "HEALTHY"}

        return {"ready": False, "status": health["status"]}

    def get_live(self) -> Dict[str, Any]:
        """Get liveness check response."""
        return {"alive": True, "timestamp": datetime.now(timezone.utc).isoformat()}

    def get_metrics(self) -> str:
        """Get Prometheus metrics."""
        return self.health_monitor.export_prometheus_metrics()


_singleton_monitor: Optional[HealthMonitor] = None


def get_health_monitor(data_dir: Path = Path("data_futures")) -> HealthMonitor:
    """Get or create the singleton health monitor."""
    global _singleton_monitor

    if _singleton_monitor is None:
        _singleton_monitor = HealthMonitor(data_dir=data_dir)

    return _singleton_monitor
