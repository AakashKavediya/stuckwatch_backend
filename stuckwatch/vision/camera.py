"""Camera capture management for stuckwatch.

Enforces privacy rules:
  - Camera is OFF by default.
  - Opens ONLY when consent is granted, mode is 'camera', and system is not paused.
  - Releases immediately when consent is revoked, paused, stopped, or on error.
  - Frames exist in memory only and are never saved or written to disk.
  - Enforces FPS throttling (10-15 FPS) and 640x480 resolution.
"""

from __future__ import annotations

import time
import sys
from typing import Callable, Optional

from stuckwatch.config import VisionConfig
from stuckwatch.logging_setup import get_logger

logger = get_logger("vision.camera")

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore


class CameraManager:
    """Manages lifecycle of cv2.VideoCapture with safety and consent enforcement."""

    def __init__(
        self,
        config: VisionConfig,
        on_error: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.config = config
        self.on_error = on_error
        self._cap: Optional[cv2.VideoCapture] = None  # type: ignore
        self._is_open = False
        self._last_frame_time = 0.0
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._last_frame_received = 0.0
        self._last_retry_time = 0.0

    @property
    def is_active(self) -> bool:
        return self._is_open and self._cap is not None and self._cap.isOpened()

    def open(self) -> bool:
        """Open the webcam stream. Returns True if successfully opened."""
        if cv2 is None:
            err = "OpenCV (cv2) is not available in this environment."
            logger.error(err)
            if self.on_error:
                self.on_error("CV2_UNAVAILABLE", err)
            return False

        if self.is_active:
            return True

        try:
            indexes = list(dict.fromkeys([self.config.camera_index, 0, 1, 2]))
            opened_index = None
            for index in indexes:
                backends = [cv2.CAP_DSHOW, None] if sys.platform == "win32" else [None]
                for backend in backends:
                    cap = (
                        cv2.VideoCapture(index, backend)
                        if backend is not None
                        else cv2.VideoCapture(index)
                    )
                    if cap.isOpened():
                        self._cap = cap
                        opened_index = index
                        break
                    cap.release()
                if opened_index is not None:
                    break

            if opened_index is None:
                err = f"Could not open webcam index {self.config.camera_index}."
                logger.warning(err)
                self.release()
                self._last_retry_time = time.time()
                if self.on_error:
                    self.on_error("camera_busy" if "busy" in err.lower() else "no_camera", err)
                return False

            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.frame_width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.frame_height)
            self._cap.set(cv2.CAP_PROP_FPS, 15.0)
            for _ in range(5):
                self._cap.grab()
            self._is_open = True
            self._consecutive_failures = 0
            self._last_frame_time = time.time()
            self._last_frame_received = self._last_frame_time
            logger.info("Webcam opened successfully on index %d.", opened_index)
            return True

        except Exception as exc:
            err = f"Unexpected exception while opening webcam: {exc}"
            logger.error(err)
            self.release()
            if self.on_error:
                self.on_error("error", err)
            return False

    def read_frame(self):
        """Read a single frame while enforcing target FPS throttle.

        Returns:
            numpy.ndarray | None: Frame in BGR format, or None on failure / rate throttle.
        """
        if not self.is_active or self._cap is None:
            return None

        # Enforce FPS cap
        now = time.time()
        min_interval = 1.0 / max(1.0, self.config.target_fps)
        elapsed = now - self._last_frame_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        elif elapsed > (2.0 * min_interval):
            # Discard one stale buffered frame when processing falls behind.
            self._cap.grab()

        ret, frame = self._cap.read()
        now = time.time()
        self._last_frame_time = now

        if not ret or frame is None:
            self._consecutive_failures += 1
            logger.debug(
                "Camera frame read failed (consecutive: %d)", self._consecutive_failures
            )
            if now - self._last_frame_received >= 5.0:
                err = "Webcam frame acquisition stopped repeatedly."
                logger.warning(err)
                self._last_retry_time = now
                self.release()
                if self.on_error:
                    self.on_error("camera_lost", err)
            return None

        self._consecutive_failures = 0
        self._last_frame_received = now
        return frame

    def release(self) -> None:
        """Immediately release hardware camera and clear memory references."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as exc:
                logger.debug("Error releasing camera: %s", exc)
            self._cap = None
        self._is_open = False
        self._consecutive_failures = 0
        logger.debug("Camera hardware released.")

    @property
    def retry_due(self) -> bool:
        return time.time() - self._last_retry_time >= 30.0
