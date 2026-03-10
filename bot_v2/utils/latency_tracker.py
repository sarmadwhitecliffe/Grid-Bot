"""
Latency Tracker - Performance Measurement Tool

Tracks checkpoint times during signal processing to identify bottlenecks.
Part of Phase 1: Measurement & Baseline for performance optimization.
"""

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class LatencyTracker:
    """
    Tracks latency between checkpoints in signal processing flow.

    Usage:
        tracker = LatencyTracker()
        tracker.checkpoint("start")
        # ... do work ...
        tracker.checkpoint("after_fetch")
        # ... more work ...
        tracker.checkpoint("end")

        logger.info(tracker.report())
    """

    def __init__(self, signal_id: Optional[str] = None):
        """
        Initialize latency tracker.

        Args:
            signal_id: Optional identifier for this signal (e.g., "BTC/USDT_buy")
        """
        self.signal_id = signal_id
        self.checkpoints: Dict[str, float] = {}
        self.start_time: Optional[float] = None

    def checkpoint(self, name: str) -> None:
        """
        Record a checkpoint timestamp.

        Args:
            name: Name of the checkpoint (e.g., "after_ohlcv_fetch")
        """
        timestamp = time.perf_counter()

        if self.start_time is None:
            self.start_time = timestamp

        self.checkpoints[name] = timestamp

    def get_delta(self, from_checkpoint: str, to_checkpoint: str) -> Optional[float]:
        """
        Get time delta between two checkpoints in milliseconds.

        Args:
            from_checkpoint: Starting checkpoint name
            to_checkpoint: Ending checkpoint name

        Returns:
            Delta in milliseconds or None if checkpoints not found
        """
        if (
            from_checkpoint not in self.checkpoints
            or to_checkpoint not in self.checkpoints
        ):
            return None

        delta = self.checkpoints[to_checkpoint] - self.checkpoints[from_checkpoint]
        return delta * 1000  # Convert to milliseconds

    def get_total_elapsed(self) -> Optional[float]:
        """
        Get total elapsed time from first to last checkpoint in milliseconds.

        Returns:
            Total elapsed time in milliseconds or None if no checkpoints
        """
        if not self.checkpoints or self.start_time is None:
            return None

        last_checkpoint_time = max(self.checkpoints.values())
        return (last_checkpoint_time - self.start_time) * 1000

    def report(self, detailed: bool = True) -> str:
        """
        Generate a human-readable latency report.

        Args:
            detailed: If True, show all checkpoint deltas; if False, only total

        Returns:
            Formatted string report of latencies
        """
        if not self.checkpoints:
            return "No checkpoints recorded"

        sorted_cps = sorted(self.checkpoints.items(), key=lambda x: x[1])

        report_lines = []

        # Add signal ID if provided
        if self.signal_id:
            report_lines.append(f"Signal: {self.signal_id}")

        # Add total time
        total = self.get_total_elapsed()
        if total is not None:
            report_lines.append(f"Total: {total:.1f}ms")

        # Add detailed breakdown if requested
        if detailed and len(sorted_cps) > 1:
            report_lines.append("Breakdown:")
            for i in range(len(sorted_cps) - 1):
                name1, t1 = sorted_cps[i]
                name2, t2 = sorted_cps[i + 1]
                delta_ms = (t2 - t1) * 1000
                report_lines.append(f"  {name1} → {name2}: {delta_ms:.1f}ms")

        return "\n".join(report_lines)

    def get_metrics(self) -> Dict[str, float]:
        """
        Get metrics suitable for monitoring/logging.

        Returns:
            Dict with total_ms and checkpoint deltas
        """
        metrics = {}

        total = self.get_total_elapsed()
        if total is not None:
            metrics["total_ms"] = total

        sorted_cps = sorted(self.checkpoints.items(), key=lambda x: x[1])

        for i in range(len(sorted_cps) - 1):
            name1, _ = sorted_cps[i]
            name2, _ = sorted_cps[i + 1]
            delta = self.get_delta(name1, name2)
            if delta is not None:
                metrics[f"{name1}_to_{name2}_ms"] = delta

        return metrics
