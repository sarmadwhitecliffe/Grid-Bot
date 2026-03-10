"""
Performance Profiler - CPU Profiling Tool

Profiles signal processing to identify CPU-bound bottlenecks.
Part of Phase 1: Measurement & Baseline for performance optimization.
"""

import cProfile
import io
import logging
import pstats
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PerformanceProfiler:
    """
    Context manager for profiling code execution.

    Usage:
        with profile_signal_processing("BTC/USDT_buy"):
            # ... code to profile ...
            pass
    """

    def __init__(
        self, profile_id: str, enabled: bool = True, save_dir: Optional[Path] = None
    ):
        """
        Initialize performance profiler.

        Args:
            profile_id: Identifier for this profiling session
            enabled: If False, profiling is skipped (no overhead)
            save_dir: Directory to save profile data (default: profiles/)
        """
        self.profile_id = profile_id
        self.enabled = enabled
        self.save_dir = save_dir or Path("profiles")
        self.profiler: Optional[cProfile.Profile] = None
        self.start_time: Optional[float] = None

    def __enter__(self):
        """Start profiling."""
        if not self.enabled:
            return self

        self.start_time = time.perf_counter()
        self.profiler = cProfile.Profile()
        self.profiler.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop profiling and save results."""
        if not self.enabled or self.profiler is None:
            return

        self.profiler.disable()
        elapsed = time.perf_counter() - self.start_time if self.start_time else 0

        # Log summary
        logger.info(f"Profile [{self.profile_id}] completed in {elapsed*1000:.1f}ms")

        # Save detailed profile data
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)

            # Save binary profile data
            profile_file = self.save_dir / f"{self.profile_id}_{int(time.time())}.prof"
            self.profiler.dump_stats(str(profile_file))
            logger.debug(f"Profile data saved to {profile_file}")

            # Save human-readable stats
            stats_file = self.save_dir / f"{self.profile_id}_{int(time.time())}.txt"
            with open(stats_file, "w") as f:
                stats = pstats.Stats(self.profiler, stream=f)
                stats.strip_dirs()
                stats.sort_stats("cumulative")
                stats.print_stats(50)  # Top 50 functions

            logger.debug(f"Profile stats saved to {stats_file}")

        except Exception as e:
            logger.error(f"Failed to save profile data: {e}", exc_info=True)

    def get_stats_string(self, sort_by: str = "cumulative", limit: int = 20) -> str:
        """
        Get formatted stats string.

        Args:
            sort_by: Sort order ('cumulative', 'time', 'calls')
            limit: Number of functions to show

        Returns:
            Formatted stats string
        """
        if not self.profiler:
            return "Profiler not run"

        stream = io.StringIO()
        stats = pstats.Stats(self.profiler, stream=stream)
        stats.strip_dirs()
        stats.sort_stats(sort_by)
        stats.print_stats(limit)
        return stream.getvalue()


@contextmanager
def profile_signal_processing(signal_id: str, enabled: bool = True):
    """
    Convenience context manager for profiling signal processing.

    Args:
        signal_id: Identifier for this signal (e.g., "BTC/USDT_buy")
        enabled: If False, profiling is skipped

    Usage:
        with profile_signal_processing("BTC/USDT_buy"):
            # ... signal processing code ...
            pass
    """
    profiler = PerformanceProfiler(signal_id, enabled=enabled)
    with profiler:
        yield profiler
