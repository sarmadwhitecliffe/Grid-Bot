"""
src/persistence/shutdown.py
-------------------------
Graceful shutdown handling for Grid Bot.

This module provides:
- GracefulShutdownHandler: Coordinated shutdown with in-flight operation completion
- Shutdown markers for crash detection
- Integration with main.py for proper cleanup
"""

import asyncio
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ShutdownState(str, Enum):
    """States of graceful shutdown."""

    IDLE = "IDLE"
    PENDING_SHUTDOWN = "PENDING_SHUTDOWN"
    DRAINING_OPERATIONS = "DRAINING_OPERATIONS"
    PERSISTING_STATE = "PERSISTING_STATE"
    COMPLETE = "COMPLETE"
    FORCED = "FORCED"


@dataclass
class ShutdownComponent:
    """A component registered for graceful shutdown."""

    name: str
    shutdown_fn: Callable
    timeout: float = 30.0
    completed: bool = False
    error: Optional[str] = None


@dataclass
class ShutdownResult:
    """Result of shutdown operation."""

    success: bool
    state: ShutdownState
    duration_ms: float
    components_shutdown: int
    components_failed: int
    errors: List[str] = field(default_factory=list)


class GracefulShutdownHandler:
    """
    Handles graceful shutdown of the Grid Bot.

    Features:
    - Register components with shutdown functions
    - Wait for in-flight operations to complete
    - Persist state before exit
    - Create shutdown markers for crash detection
    - Force stop after timeout
    """

    SHUTDOWN_MARKER = ".shutdown_complete"

    def __init__(
        self,
        data_dir: Path = Path("data_futures"),
        default_timeout: float = 30.0,
    ):
        self.data_dir = data_dir
        self.default_timeout = default_timeout
        self.state = ShutdownState.IDLE
        self.components: Dict[str, ShutdownComponent] = {}
        self._lock = threading.RLock()
        self._shutdown_started: Optional[datetime] = None
        self._shutdown_reason: str = ""

    def register_component(
        self,
        name: str,
        shutdown_fn: Callable,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Register a component for graceful shutdown.

        Args:
            name: Component name
            shutdown_fn: Async function to call on shutdown
            timeout: Timeout for this component (default: 30s)
        """
        with self._lock:
            self.components[name] = ShutdownComponent(
                name=name,
                shutdown_fn=shutdown_fn,
                timeout=timeout or self.default_timeout,
            )
            logger.debug(f"Registered component for shutdown: {name}")

    def unregister_component(self, name: str) -> None:
        """Unregister a component."""
        with self._lock:
            self.components.pop(name, None)

    async def begin_shutdown(self, reason: str = "Manual") -> ShutdownResult:
        """
        Initiate graceful shutdown.

        Args:
            reason: Reason for shutdown

        Returns:
            ShutdownResult with details
        """
        start_time = time.time()
        self._shutdown_reason = reason
        self._shutdown_started = datetime.now(timezone.utc)

        logger.info(f"Starting graceful shutdown: {reason}")

        with self._lock:
            self.state = ShutdownState.PENDING_SHUTDOWN

            for comp in self.components.values():
                comp.completed = False
                comp.error = None

        self.state = ShutdownState.DRAINING_OPERATIONS
        logger.info("Draining in-flight operations...")

        await asyncio.sleep(0.5)

        self.state = ShutdownState.PERSISTING_STATE
        logger.info("Persisting final state...")

        success, errors = await self._execute_shutdowns()

        self.create_shutdown_marker(
            success=success,
            reason=reason,
            error_count=len(errors),
        )

        if not success and self._shutdown_started:
            elapsed = (
                datetime.now(timezone.utc) - self._shutdown_started
            ).total_seconds()
            if elapsed >= self.default_timeout:
                logger.warning("Shutdown timeout reached, forcing stop")
                self.state = ShutdownState.FORCED

        self.state = ShutdownState.COMPLETE

        duration_ms = (time.time() - start_time) * 1000

        result = ShutdownResult(
            success=success,
            state=self.state,
            duration_ms=duration_ms,
            components_shutdown=sum(1 for c in self.components.values() if c.completed),
            components_failed=len(errors),
            errors=errors,
        )

        logger.info(
            f"Shutdown complete: success={success}, "
            f"duration={duration_ms:.0f}ms, "
            f"components={result.components_shutdown}/{len(self.components)}"
        )

        return result

    async def _execute_shutdowns(self) -> tuple[bool, List[str]]:
        """Execute shutdown for all registered components."""
        errors = []

        async def run_shutdown(name: str, comp: ShutdownComponent):
            try:
                logger.debug(f"Shutting down component: {name}")
                if asyncio.iscoroutinefunction(comp.shutdown_fn):
                    await asyncio.wait_for(
                        comp.shutdown_fn(),
                        timeout=comp.timeout,
                    )
                else:
                    await asyncio.wait_for(
                        asyncio.to_thread(comp.shutdown_fn),
                        timeout=comp.timeout,
                    )
                comp.completed = True
                logger.debug(f"Component shutdown complete: {name}")
            except asyncio.TimeoutError:
                error = f"Component {name} timed out after {comp.timeout}s"
                logger.error(error)
                errors.append(error)
                comp.error = error
            except Exception as e:
                error = f"Component {name} failed: {e}"
                logger.error(error)
                errors.append(error)
                comp.error = str(e)

        tasks = [run_shutdown(name, comp) for name, comp in self.components.items()]

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        return len(errors) == 0, errors

    def create_shutdown_marker(
        self,
        success: bool,
        reason: str,
        error_count: int = 0,
    ) -> None:
        """
        Create shutdown marker file.

        Args:
            success: Whether shutdown was successful
            reason: Reason for shutdown
            error_count: Number of errors during shutdown
        """
        marker_path = self.data_dir / self.SHUTDOWN_MARKER

        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": success,
            "reason": reason,
            "error_count": error_count,
            "state": self.state.value,
        }

        import json

        with open(marker_path, "w") as f:
            json.dump(data, f, indent=2)

        logger.debug(f"Created shutdown marker: {marker_path}")

    def check_shutdown_marker(self) -> Optional[Dict[str, Any]]:
        """
        Check if previous shutdown was clean.

        Returns:
            Dict with shutdown details if marker exists, None otherwise
        """
        marker_path = self.data_dir / self.SHUTDOWN_MARKER

        if not marker_path.exists():
            return None

        import json

        try:
            with open(marker_path) as f:
                data = json.load(f)
            return data
        except Exception as e:
            logger.warning(f"Failed to read shutdown marker: {e}")
            return None

    def clear_shutdown_marker(self) -> None:
        """Clear shutdown marker on clean startup."""
        marker_path = self.data_dir / self.SHUTDOWN_MARKER
        if marker_path.exists():
            try:
                marker_path.unlink()
                logger.debug("Cleared shutdown marker")
            except Exception as e:
                logger.warning(f"Failed to clear shutdown marker: {e}")

    def was_crash(self) -> bool:
        """
        Check if the previous exit was a crash (no clean shutdown).

        Returns:
            True if previous exit was a crash
        """
        marker = self.check_shutdown_marker()
        if marker is None:
            return True

        return not marker.get("success", False)

    def get_state(self) -> ShutdownState:
        """Get current shutdown state."""
        return self.state


class ShutdownManager:
    """
    Singleton manager for graceful shutdown integration.
    """

    _instance: Optional[GracefulShutdownHandler] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(
        cls, data_dir: Path = Path("data_futures")
    ) -> GracefulShutdownHandler:
        """Get or create the shutdown handler instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = GracefulShutdownHandler(data_dir=data_dir)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (for testing)."""
        with cls._lock:
            cls._instance = None


def setup_signal_handlers(
    handler: GracefulShutdownHandler,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> None:
    """
    Setup signal handlers for SIGINT and SIGTERM.

    Args:
        handler: GracefulShutdownHandler instance
        loop: asyncio event loop
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    def signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating graceful shutdown...")

        async def shutdown():
            await handler.begin_shutdown(reason=f"Signal: {sig_name}")
            loop.stop()

        asyncio.ensure_future(shutdown(), loop=loop)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Signal handlers registered for SIGINT/SIGTERM")
