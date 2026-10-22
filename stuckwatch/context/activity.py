"""Privacy-first user activity tracking.

CRITICAL PRIVACY GUARANTEE:
  - NO key characters, key codes, or typed text are ever inspected or recorded.
  - NO mouse coordinates, paths, trails, or cursor positions are ever recorded.
  - Listeners record ONLY anonymous event timestamps and event counts.
"""

from __future__ import annotations

import collections
import sys
import threading
import time
from typing import Callable, Deque, Optional

from stuckwatch.logging_setup import get_logger

logger = get_logger("context.activity")

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = None  # type: ignore
    mouse = None  # type: ignore


class ActivityTracker:
    """Tracks idle duration, typing rate, and pointer activity counts."""

    def __init__(
        self,
        rolling_window_seconds: float = 60.0,
        on_error: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.rolling_window_seconds = rolling_window_seconds
        self.on_error = on_error

        self._lock = threading.Lock()
        self._last_activity_time: float = time.time()
        self._key_timestamps: Deque[float] = collections.deque()
        self._mouse_timestamps: Deque[float] = collections.deque()
        self._app_switch_timestamps: Deque[float] = collections.deque()

        self._kb_listener = None
        self._mouse_listener = None
        self._running = False
        self._pynput_available = keyboard is not None and mouse is not None

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start anonymous keyboard and mouse event listeners."""
        if self._running:
            return

        if not self._pynput_available:
            msg = "pynput is not installed or available on this system."
            logger.warning(msg)
            if self.on_error:
                self.on_error("INPUT_MONITORING_UNAVAILABLE", msg)
            self._running = True
            return

        try:
            # Strictly anonymous listener: parameters are ignored, only time recorded
            def on_press(_key) -> None:
                self._record_key_event()

            def on_move(_x, _y) -> None:
                self._record_mouse_event()

            def on_click(_x, _y, _button, _pressed) -> None:
                self._record_mouse_event()

            def on_scroll(_x, _y, _dx, _dy) -> None:
                self._record_mouse_event()

            self._kb_listener = keyboard.Listener(on_press=on_press)
            self._kb_listener.daemon = True
            self._kb_listener.start()

            self._mouse_listener = mouse.Listener(
                on_move=on_move, on_click=on_click, on_scroll=on_scroll
            )
            self._mouse_listener.daemon = True
            self._mouse_listener.start()

            self._running = True
            logger.info("Activity input monitoring started (counts and timestamps only).")

        except Exception as exc:
            err_msg = (
                f"Failed to start input listeners ({exc}). On macOS, ensure Accessibility and "
                "Input Monitoring permissions are granted in System Settings. On Linux Wayland, "
                "input listening may require elevated display server access."
            )
            logger.warning(err_msg)
            if self.on_error:
                self.on_error("INPUT_MONITORING_PERMISSION", err_msg)
            self._running = True  # Keep running in degraded/fallback mode

    def stop(self) -> None:
        """Stop input listeners and clean up threads."""
        self._running = False
        if self._kb_listener is not None:
            try:
                self._kb_listener.stop()
            except Exception:
                pass
            self._kb_listener = None

        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None

        logger.debug("Activity input monitoring stopped.")

    def _record_key_event(self, timestamp: Optional[float] = None) -> None:
        now = time.time() if timestamp is None else timestamp
        with self._lock:
            self._last_activity_time = now
            self._key_timestamps.append(now)

    def _record_mouse_event(self, timestamp: Optional[float] = None) -> None:
        now = time.time() if timestamp is None else timestamp
        with self._lock:
            self._last_activity_time = now
            self._mouse_timestamps.append(now)

    def record_app_switch(self) -> None:
        """Record an app category switch event."""
        now = time.time()
        with self._lock:
            self._app_switch_timestamps.append(now)

    def get_metrics(self) -> tuple[float, float, float, float]:
        """Compute current activity metrics.

        Returns:
            (idle_seconds, typing_rate_per_min, pointer_activity_per_min, app_switches_per_min)
        """
        now = time.time()
        cutoff = now - self.rolling_window_seconds

        with self._lock:
            # Prune old timestamps
            while self._key_timestamps and self._key_timestamps[0] < cutoff:
                self._key_timestamps.popleft()
            while self._mouse_timestamps and self._mouse_timestamps[0] < cutoff:
                self._mouse_timestamps.popleft()
            while self._app_switch_timestamps and self._app_switch_timestamps[0] < cutoff:
                self._app_switch_timestamps.popleft()

            # Calculate rates per minute scaled to the window
            key_count = len(self._key_timestamps)
            mouse_count = len(self._mouse_timestamps)
            switch_count = len(self._app_switch_timestamps)
            window_factor = 60.0 / max(1.0, self.rolling_window_seconds)

            typing_rate = key_count * window_factor
            pointer_activity = mouse_count * window_factor
            app_switches = switch_count * window_factor

            idle_sec = max(0.0, now - self._last_activity_time)

            return (float(idle_sec), float(typing_rate), float(pointer_activity), float(app_switches))

    def _get_windows_idle_seconds(self) -> Optional[float]:
        """Query GetLastInputInfo on Windows as a safe OS-level fallback."""
        try:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return max(0.0, float(millis) / 1000.0)
        except Exception:
            pass
        return None
