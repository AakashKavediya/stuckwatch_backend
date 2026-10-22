from __future__ import annotations

from unittest.mock import Mock

from stuckwatch.config import ModelConfig, StuckwatchConfig
from stuckwatch.engine import Engine
from stuckwatch.ipc import IPC
from stuckwatch.model.baseline import BaselineProfile
from stuckwatch.model.estimator import StuckEstimator
from stuckwatch.policy.suppression import SuppressionEngine


def make_engine(tmp_path, mode="hybrid"):
    return Engine(StuckwatchConfig(mode=mode, data_dir=tmp_path))


def test_hybrid_without_consent_reports_no_consent_and_clears_vision(tmp_path):
    engine = make_engine(tmp_path)
    engine.state = "monitoring"
    engine._ensure_camera_state()

    assert engine.vision_status == "no_consent"
    assert engine.camera_manager.is_active is False
    assert engine._latest_blink_rate is None
    engine.shutdown()


def test_screen_only_never_opens_camera_with_consent(tmp_path):
    engine = make_engine(tmp_path, mode="screen_only")
    engine.config.consent_camera = True
    engine.camera_manager.open = Mock(return_value=True)
    engine.state = "monitoring"

    engine._ensure_camera_state()

    engine.camera_manager.open.assert_not_called()
    assert engine.vision_status == "screen_only_mode"
    engine.shutdown()


def test_revoking_consent_releases_camera_and_clears_fields(tmp_path):
    engine = make_engine(tmp_path)
    engine.camera_manager.release = Mock()
    engine.config.consent_camera = True
    engine._latest_blink_rate = 12.0
    engine._latest_gaze_stability = 0.9
    engine._latest_head_motion = 0.01
    engine._face_detected = True

    engine._handle_command({"cmd": "consent", "camera": False})

    engine.camera_manager.release.assert_called()
    assert engine.vision_status == "no_consent"
    assert engine._latest_blink_rate is None
    assert engine._latest_gaze_stability is None
    assert engine._latest_head_motion is None
    assert engine._face_detected is False
    engine.shutdown()


def test_signals_include_valid_vision_fields(monkeypatch):
    captured = []
    ipc = IPC()
    monkeypatch.setattr(ipc, "emit", captured.append)

    ipc.emit_signals(
        blink_rate_per_min=None,
        gaze_stability=None,
        head_motion=None,
        idle_seconds=1,
        app_switches_per_min=0,
        typing_rate=0,
        app_category="editor",
        stuck_likelihood=0.1,
        confidence=0.5,
        reasons=[],
        vision_available=False,
        vision_status="no_consent",
        face_detected=None,
        vision_warmup_seconds_left=None,
    )

    event = captured[0]
    assert event["vision_available"] is False
    assert event["vision_status"] == "no_consent"
    assert event["face_detected"] is None
    assert event["vision_warmup_seconds_left"] is None


def test_estimator_confidence_is_higher_with_active_vision():
    profile = BaselineProfile(
        features={"blink_rate": (16.0, 5.0, 1.0), "typing_rate": (100.0, 50.0, 1.0)}
    )
    kwargs = dict(
        idle_seconds=100.0,
        typing_rate=0.0,
        app_switches_per_min=0.0,
        app_category="editor",
        blink_rate=8.0,
        gaze_stability=0.95,
        head_motion=0.01,
        baseline=profile,
    )
    estimator = StuckEstimator(ModelConfig())
    _, vision_confidence, _ = estimator.estimate(
        mode="hybrid", face_detected=True, **kwargs
    )
    estimator.reset()
    _, screen_confidence, _ = estimator.estimate(
        mode="hybrid",
        face_detected=False,
        blink_rate=None,
        gaze_stability=None,
        head_motion=None,
        **{key: value for key, value in kwargs.items()
           if key not in ("blink_rate", "gaze_stability", "head_motion")},
    )

    assert vision_confidence > screen_confidence


def test_suppression_still_wins_over_active_vision():
    suppressed, reason = SuppressionEngine().is_suppressed(
        app_category="video",
        is_fullscreen=False,
        paused_until=None,
        mode="hybrid",
        consent_camera=True,
        idle_seconds=120.0,
        face_detected=True,
        away_threshold_seconds=900.0,
        in_cooldown=False,
        confidence=0.99,
    )

    assert suppressed is True
    assert reason == "active_category_video"
