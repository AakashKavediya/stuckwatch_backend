"""Unit tests for the transparent evidence estimator and baseline z-scores."""

from __future__ import annotations

import pytest

from stuckwatch.config import ModelConfig
from stuckwatch.model.baseline import BaselineProfile
from stuckwatch.model.estimator import StuckEstimator


def test_baseline_zscore_clamping_and_fallback():
    """Verify z-scores clamp to [-3.0, 3.0] and use population defaults when uncalibrated."""
    empty_profile = BaselineProfile()
    # Population default for blink_rate is mean=16.0, std=5.0
    z_normal = empty_profile.zscore("blink_rate", 16.0)
    assert pytest.approx(z_normal, abs=1e-2) == 0.0

    z_high = empty_profile.zscore("blink_rate", 36.0)  # +4 std
    assert z_high == 3.0  # Clamped to +3.0

    z_low = empty_profile.zscore("blink_rate", -10.0)  # -5 std
    assert z_low == -3.0  # Clamped to -3.0

    # Test personal profile overrides default
    custom_profile = BaselineProfile(
        features={"blink_rate": (25.0, 2.0, 1000.0)}
    )
    z_custom = custom_profile.zscore("blink_rate", 25.0)
    assert pytest.approx(z_custom, abs=1e-2) == 0.0
    z_custom_diff = custom_profile.zscore("blink_rate", 29.0)
    assert pytest.approx(z_custom_diff, abs=1e-2) == 2.0


def test_estimator_monotonicity_on_stagnation():
    """Higher idle in editor must produce a higher stuck_likelihood score."""
    cfg = ModelConfig()
    estimator = StuckEstimator(cfg)
    profile = BaselineProfile()

    # Low idle, active work
    score_active, conf_active, _ = estimator.estimate(
        idle_seconds=5.0,
        typing_rate=120.0,
        app_switches_per_min=1.0,
        app_category="editor",
        blink_rate=16.0,
        gaze_stability=0.55,
        head_motion=0.035,
        baseline=profile,
        mode="camera",
        face_detected=True,
    )

    # Reset EMA for independent comparison
    estimator.reset()

    # Long idle in editor, no typing (screen stagnation proxy)
    score_stagnant, conf_stagnant, reasons = estimator.estimate(
        idle_seconds=180.0,
        typing_rate=0.0,
        app_switches_per_min=0.0,
        app_category="editor",
        blink_rate=8.0,
        gaze_stability=0.92,
        head_motion=0.010,
        baseline=profile,
        mode="camera",
        face_detected=True,
    )

    assert score_stagnant > score_active
    assert any("editor" in r or "typing" in r for r in reasons)


def test_estimator_erratic_switching_evidence():
    """High switching with low typing should trigger erratic switching reason."""
    cfg = ModelConfig()
    estimator = StuckEstimator(cfg)
    profile = BaselineProfile()

    score, conf, reasons = estimator.estimate(
        idle_seconds=10.0,
        typing_rate=5.0,
        app_switches_per_min=7.0,
        app_category="browser",
        blink_rate=16.0,
        gaze_stability=0.5,
        head_motion=0.03,
        baseline=profile,
        mode="camera",
        face_detected=True,
    )

    assert any("switching between apps" in r for r in reasons)


def test_confidence_penalties():
    """Confidence must drop when face is missing or in screen_only mode."""
    cfg = ModelConfig()
    estimator = StuckEstimator(cfg)
    profile = BaselineProfile()

    # Full camera with face
    _, conf_camera, _ = estimator.estimate(
        idle_seconds=10.0,
        typing_rate=50.0,
        app_switches_per_min=1.0,
        app_category="editor",
        blink_rate=16.0,
        gaze_stability=0.5,
        head_motion=0.03,
        baseline=profile,
        mode="camera",
        face_detected=True,
    )

    # Camera with missing face
    estimator.reset()
    _, conf_no_face, _ = estimator.estimate(
        idle_seconds=10.0,
        typing_rate=50.0,
        app_switches_per_min=1.0,
        app_category="editor",
        blink_rate=None,
        gaze_stability=None,
        head_motion=None,
        baseline=profile,
        mode="camera",
        face_detected=False,
    )

    # Screen only mode
    estimator.reset()
    _, conf_screen_only, _ = estimator.estimate(
        idle_seconds=10.0,
        typing_rate=50.0,
        app_switches_per_min=1.0,
        app_category="editor",
        blink_rate=None,
        gaze_stability=None,
        head_motion=None,
        baseline=profile,
        mode="screen_only",
        face_detected=False,
    )

    assert conf_camera > conf_no_face
    assert conf_camera > conf_screen_only
