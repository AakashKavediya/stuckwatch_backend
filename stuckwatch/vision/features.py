"""Privacy-preserving derived feature extraction from facial landmarks.

All functions here are pure or maintain small, rolling in-memory windows.
No camera, frames, or biometric identifiers are stored or required.

ETHICAL NOTE:
Each computed metric is a weak, noisy proxy indicator that varies significantly
between individuals and task contexts. They are never diagnostic or clinical.
"""

from __future__ import annotations

import collections
import time
from typing import Deque, Optional, Tuple

import numpy as np

# Landmark index definitions for Face Mesh with iris refinement
LEFT_EYE_INDICES = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_INDICES = [33, 160, 158, 133, 153, 144]

# Iris centers
LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473

# Reference landmarks for normalization
NOSE_TIP = 1
LEFT_EAR_CORNER = 362
RIGHT_EAR_CORNER = 33


def compute_eye_aspect_ratio(eye_pts: np.ndarray) -> float:
    """Compute Eye Aspect Ratio (EAR) for 6 eye landmark points.

    eye_pts must contain 6 coordinates corresponding to:
      p1: outer corner, p2: upper outer, p3: upper inner,
      p4: inner corner, p5: lower inner, p6: lower outer.

    Formula: EAR = (||p2 - p6|| + ||p3 - p5||) / (2.0 * ||p1 - p4||)
    """
    if len(eye_pts) != 6:
        return 0.0

    p1, p2, p3, p4, p5, p6 = eye_pts[:6]
    v1 = np.linalg.norm(p2 - p6)
    v2 = np.linalg.norm(p3 - p5)
    horiz = np.linalg.norm(p1 - p4)

    if horiz < 1e-6:
        return 0.0

    return float((v1 + v2) / (2.0 * horiz))


def calculate_ear_from_landmarks(points: np.ndarray) -> float:
    """Calculate mean EAR across both eyes from FaceMesh landmarks."""
    if points is None or len(points) < 468:
        return 0.0

    left_pts = points[LEFT_EYE_INDICES]
    right_pts = points[RIGHT_EYE_INDICES]

    left_ear = compute_eye_aspect_ratio(left_pts)
    right_ear = compute_eye_aspect_ratio(right_pts)

    return float((left_ear + right_ear) / 2.0)


class BlinkDetector:
    """Stateful blink detector tracking EAR over time with adaptive thresholding.

    Proxy Indicator Note:
      Blink rate varies wildly across humans (typically 10-25 blinks/min) and
      tends to drop during intense visual reading or screen absorption. It is
      a noisy hint, not a measurement of focus.
    """

    def __init__(
        self,
        ear_threshold: float = 0.21,
        min_consecutive_frames: int = 2,
        window_seconds: float = 60.0,
        min_valid_data_seconds: float = 20.0,
    ) -> None:
        self.ear_threshold = ear_threshold
        self.min_consecutive_frames = min_consecutive_frames
        self.window_seconds = window_seconds
        self.min_valid_data_seconds = min_valid_data_seconds

        self._consecutive_below = 0
        self._blink_timestamps: Deque[float] = collections.deque()
        self._start_time: Optional[float] = None
        self._last_update_time: float = 0.0

    def update(self, ear: Optional[float], timestamp: Optional[float] = None) -> Optional[float]:
        """Update tracker with latest EAR value.

        Returns:
            blink_rate_per_min: rolling blinks per minute, or None if insufficient history.
        """
        now = time.time() if timestamp is None else timestamp
        if self._start_time is None and ear is not None:
            self._start_time = now

        self._last_update_time = now

        # Prune old blinks outside rolling window
        cutoff = now - self.window_seconds
        while self._blink_timestamps and self._blink_timestamps[0] < cutoff:
            self._blink_timestamps.popleft()

        if ear is None:
            # Face missing - pause consecutive count
            self._consecutive_below = 0
        else:
            if ear < self.ear_threshold:
                self._consecutive_below += 1
            else:
                if self._consecutive_below >= self.min_consecutive_frames:
                    # Valid blink completed
                    self._blink_timestamps.append(now)
                self._consecutive_below = 0

        # Require minimum observation duration before emitting rate
        if self._start_time is None or (now - self._start_time) < self.min_valid_data_seconds:
            return None

        # Effective duration for rate scaling (up to window_seconds)
        observed_sec = min(self.window_seconds, max(1.0, now - self._start_time))
        rate_per_min = (len(self._blink_timestamps) / observed_sec) * 60.0
        return float(rate_per_min)

    def reset(self) -> None:
        self._consecutive_below = 0
        self._blink_timestamps.clear()
        self._start_time = None


class GazeStabilityTracker:
    """Tracks iris movement relative to eye corners to measure gaze dispersion.

    Proxy Indicator Note:
      A fixed, unchanging gaze (high stability) accompanied by zero keyboard input
      can indicate staring blankly at a screen. Gaze stability is normalized to
      eye width to tolerate minor head movements.
    """

    def __init__(self, window_seconds: float = 15.0) -> None:
        self.window_seconds = window_seconds
        # Stores tuples of (timestamp, norm_x, norm_y)
        self._history: Deque[Tuple[float, float, float]] = collections.deque()

    def update(self, points: Optional[np.ndarray], timestamp: Optional[float] = None) -> Optional[float]:
        """Update tracker with normalized iris center coordinates.

        Returns:
            gaze_stability: float in [0.0, 1.0], where 1.0 is extremely fixed gaze,
            or None if insufficient samples or no face detected.
        """
        now = time.time() if timestamp is None else timestamp

        # Prune expired samples
        cutoff = now - self.window_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        if points is None or len(points) < 478:
            if len(self._history) < 5:
                return None
            return self._compute_stability()

        try:
            # Left eye normalization
            left_corner1 = points[362][:2]
            left_corner2 = points[263][:2]
            left_iris = points[LEFT_IRIS_CENTER][:2]
            eye_w_left = np.linalg.norm(left_corner1 - left_corner2)

            # Right eye normalization
            right_corner1 = points[33][:2]
            right_corner2 = points[133][:2]
            right_iris = points[RIGHT_IRIS_CENTER][:2]
            eye_w_right = np.linalg.norm(right_corner1 - right_corner2)

            if eye_w_left < 1e-5 or eye_w_right < 1e-5:
                return None

            # Normalized position of iris relative to outer corner, divided by eye width
            pos_left = (left_iris - left_corner1) / eye_w_left
            pos_right = (right_iris - right_corner1) / eye_w_right
            avg_norm = (pos_left + pos_right) / 2.0

            self._history.append((now, float(avg_norm[0]), float(avg_norm[1])))
        except Exception:
            return None

        return self._compute_stability()

    def _compute_stability(self) -> Optional[float]:
        if len(self._history) < 5:
            return None

        xs = [pt[1] for pt in self._history]
        ys = [pt[2] for pt in self._history]

        std_x = float(np.std(xs))
        std_y = float(np.std(ys))
        dispersion = float(np.sqrt(std_x**2 + std_y**2))

        # Typical dispersion range in normalized eye units:
        # 0.01 = laser-focused staring, > 0.08 = looking around actively
        max_dispersion = 0.08
        stability = max(0.0, min(1.0, 1.0 - (dispersion / max_dispersion)))
        return float(stability)

    def reset(self) -> None:
        self._history.clear()


class HeadMotionTracker:
    """Tracks variance in normalized nose-tip position over a short rolling window.

    Proxy Indicator Note:
      Low head motion (frozen posture) can be a proxy for physical stagnation
      or immobility, but can also occur during engaged reading.
    """

    def __init__(self, window_seconds: float = 15.0) -> None:
        self.window_seconds = window_seconds
        # Stores tuples of (timestamp, norm_x, norm_y)
        self._history: Deque[Tuple[float, float, float]] = collections.deque()

    def update(self, points: Optional[np.ndarray], timestamp: Optional[float] = None) -> Optional[float]:
        """Update tracker with nose tip position normalized by face scale.

        Returns:
            head_motion: variance measure (higher means more movement), or None.
        """
        now = time.time() if timestamp is None else timestamp

        cutoff = now - self.window_seconds
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

        if points is None or len(points) < 468:
            if len(self._history) < 5:
                return None
            return self._compute_motion()

        try:
            # Face scale estimation using outer eye corners distance
            scale = np.linalg.norm(points[362][:2] - points[33][:2])
            if scale < 1e-5:
                return None

            nose = points[NOSE_TIP][:2] / scale
            self._history.append((now, float(nose[0]), float(nose[1])))
        except Exception:
            return None

        return self._compute_motion()

    def _compute_motion(self) -> Optional[float]:
        if len(self._history) < 5:
            return None

        xs = [pt[1] for pt in self._history]
        ys = [pt[2] for pt in self._history]

        var_x = float(np.var(xs))
        var_y = float(np.var(ys))
        motion = float(np.sqrt(var_x + var_y))
        return motion

    def reset(self) -> None:
        self._history.clear()
