"""Unit tests for derived facial and gaze features (EAR, blink detector, gaze stability, head motion).

All tests run purely in memory without camera hardware or images.
"""

from __future__ import annotations

import numpy as np
import pytest

from stuckwatch.vision.features import (
    BlinkDetector,
    GazeStabilityTracker,
    HeadMotionTracker,
    calculate_ear_from_landmarks,
    compute_eye_aspect_ratio,
)


def test_ear_calculation_with_known_points():
    """Verify EAR calculation on known open eye geometry."""
    # Construct synthetic 6 points for an eye:
    # p1: (0, 0), p4: (10, 0) -> width = 10
    # p2: (3, 3), p6: (3, -3) -> vertical 1 = 6
    # p3: (7, 3), p5: (7, -3) -> vertical 2 = 6
    # EAR = (6 + 6) / (2 * 10) = 12 / 20 = 0.60
    eye_open = np.array([
        [0.0, 0.0],
        [3.0, 3.0],
        [7.0, 3.0],
        [10.0, 0.0],
        [7.0, -3.0],
        [3.0, -3.0],
    ], dtype=np.float32)

    ear = compute_eye_aspect_ratio(eye_open)
    assert pytest.approx(ear, rel=1e-3) == 0.60

    # Closed eye: vertical distances are ~0
    eye_closed = np.array([
        [0.0, 0.0],
        [3.0, 0.1],
        [7.0, 0.1],
        [10.0, 0.0],
        [7.0, -0.1],
        [3.0, -0.1],
    ], dtype=np.float32)
    ear_closed = compute_eye_aspect_ratio(eye_closed)
    assert ear_closed < 0.10


def test_blink_detector_synthetic_sequence():
    """Verify blink detection on an EAR sequence with a 2-frame dip followed by reopen."""
    detector = BlinkDetector(
        ear_threshold=0.21,
        min_consecutive_frames=2,
        window_seconds=60.0,
        min_valid_data_seconds=10.0,
    )

    t = 1000.0
    # Baseline open eye: EAR = 0.30 for 10 seconds (10 frames at 1 fps)
    for _ in range(10):
        detector.update(0.30, timestamp=t)
        t += 1.0

    # Blink: 2 frames below threshold (0.15), then reopen (0.30)
    detector.update(0.15, timestamp=t)
    t += 0.1
    detector.update(0.15, timestamp=t)
    t += 0.1
    rate = detector.update(0.30, timestamp=t)
    t += 1.0

    # With 1 blink in ~11 seconds, rate per minute should be positive
    assert rate is not None
    assert rate > 0.0


def test_blink_detector_ignores_single_frame_flutter():
    """A single frame drop below threshold must NOT count as a blink."""
    detector = BlinkDetector(
        ear_threshold=0.21,
        min_consecutive_frames=2,
        window_seconds=60.0,
        min_valid_data_seconds=5.0,
    )

    t = 1000.0
    for _ in range(8):
        detector.update(0.30, timestamp=t)
        t += 1.0

    # Single flutter frame below threshold
    detector.update(0.15, timestamp=t)
    t += 0.1
    rate = detector.update(0.30, timestamp=t)

    # Rate should be 0 because 1 frame < min_consecutive_frames (2)
    assert rate == 0.0


def test_gaze_stability_fixed_vs_wandering():
    """Fixed iris coordinates should produce high stability; moving ones lower stability."""
    tracker_fixed = GazeStabilityTracker(window_seconds=15.0)
    tracker_moving = GazeStabilityTracker(window_seconds=15.0)

    # 478 mock landmarks
    landmarks = np.zeros((478, 3), dtype=np.float32)
    # Left eye outer (362) and inner (263)
    landmarks[362] = [0.2, 0.5, 0.0]
    landmarks[263] = [0.3, 0.5, 0.0]
    # Right eye outer (33) and inner (133)
    landmarks[33] = [0.7, 0.5, 0.0]
    landmarks[133] = [0.8, 0.5, 0.0]

    # Feed fixed gaze points
    t = 1000.0
    for _ in range(10):
        landmarks[468] = [0.25, 0.5, 0.0]  # fixed left iris
        landmarks[473] = [0.75, 0.5, 0.0]  # fixed right iris
        tracker_fixed.update(landmarks, timestamp=t)
        t += 1.0

    stab_fixed = tracker_fixed.update(landmarks, timestamp=t)
    assert stab_fixed is not None
    assert stab_fixed > 0.85  # Very high stability for fixed gaze

    # Feed moving gaze points
    t = 1000.0
    for i in range(10):
        offset = (i % 3) * 0.03
        landmarks[468] = [0.25 + offset, 0.5 + offset, 0.0]
        landmarks[473] = [0.75 + offset, 0.5 + offset, 0.0]
        tracker_moving.update(landmarks, timestamp=t)
        t += 1.0

    stab_moving = tracker_moving.update(landmarks, timestamp=t)
    assert stab_moving is not None
    assert stab_moving < stab_fixed


def test_missing_face_returns_none_or_degraded():
    """When no face is detected, features return None without crashing."""
    detector = BlinkDetector()
    tracker = GazeStabilityTracker()
    head = HeadMotionTracker()

    assert detector.update(None, timestamp=100.0) is None
    assert tracker.update(None, timestamp=100.0) is None
    assert head.update(None, timestamp=100.0) is None
