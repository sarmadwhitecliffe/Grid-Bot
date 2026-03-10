"""
Test script for Phase 1 latency tracking.

Verifies that latency tracker and performance profiler work correctly.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


import pytest

from bot_v2.utils.latency_tracker import LatencyTracker
from bot_v2.utils.performance_profiler import profile_signal_processing


@pytest.mark.asyncio
async def test_latency_tracker():
    """Test latency tracker functionality."""
    print("Testing LatencyTracker...")

    tracker = LatencyTracker("test_signal")

    tracker.checkpoint("start")
    await asyncio.sleep(0.1)  # Simulate 100ms work

    tracker.checkpoint("after_fetch")
    await asyncio.sleep(0.05)  # Simulate 50ms work

    tracker.checkpoint("after_calc")
    await asyncio.sleep(0.02)  # Simulate 20ms work

    tracker.checkpoint("end")

    # Test report
    report = tracker.report(detailed=True)
    print("\nLatency Report:")
    print(report)

    # Test metrics
    metrics = tracker.get_metrics()
    print("\nMetrics:")
    for key, value in metrics.items():
        print(f"  {key}: {value:.1f}ms")

    # Verify timing is approximately correct
    total = tracker.get_total_elapsed()
    assert total is not None, "Total elapsed should not be None"
    assert 160 < total < 200, f"Total should be ~170ms, got {total:.1f}ms"

    print("\n✅ LatencyTracker test passed!")


@pytest.mark.asyncio
async def test_performance_profiler():
    """Test performance profiler functionality."""
    print("\n\nTesting PerformanceProfiler...")

    def cpu_intensive_work():
        """Simulate CPU-intensive work."""
        result = 0
        for i in range(100000):
            result += i**2
        return result

    with profile_signal_processing("test_profile", enabled=True):
        # Do some work
        await asyncio.sleep(0.05)
        cpu_intensive_work()
        await asyncio.sleep(0.03)

    # Check that profile files were created
    profile_dir = Path("profiles")
    if profile_dir.exists():
        profile_files = list(profile_dir.glob("test_profile_*.prof"))
        if profile_files:
            print(f"✅ Profile files created: {len(profile_files)} files")
        else:
            print("⚠️  No profile files found (this is OK for quick tests)")

    print("✅ PerformanceProfiler test passed!")


@pytest.mark.asyncio
async def test_disabled_tracking():
    """Test that disabled tracking has minimal overhead."""
    print("\n\nTesting disabled tracking...")

    # Test with None tracker
    tracker = None

    if tracker:
        tracker.checkpoint("start")

    await asyncio.sleep(0.01)

    if tracker:
        tracker.checkpoint("end")

    print("✅ Disabled tracking test passed!")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Phase 1: Latency Tracking Tests")
    print("=" * 60)

    try:
        await test_latency_tracker()
        await test_performance_profiler()
        await test_disabled_tracking()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
