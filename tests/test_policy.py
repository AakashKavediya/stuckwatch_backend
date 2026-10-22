"""Unit tests for suppression rules and gentle nudge policy."""

from __future__ import annotations

import time
import pytest

from stuckwatch.config import NudgeConfig
from stuckwatch.policy.nudge import NudgePolicy
from stuckwatch.policy.suppression import SuppressionEngine


def test_suppression_engine_rules():
    """Verify each suppression condition silences nudges."""
    engine = SuppressionEngine()

    # 1. Protected categories: video, call, game
    for cat in ["video", "call", "game"]:
        supp, reason = engine.is_suppressed(
            app_category=cat,
            is_fullscreen=False,
            paused_until=None,
            mode="camera",
            consent_camera=True,
            idle_seconds=100.0,
            face_detected=True,
            away_threshold_seconds=900.0,
            in_cooldown=False,
            confidence=0.85,
        )
        assert supp is True
        assert reason == f"active_category_{cat}"

    # 2. Fullscreen application
    supp, reason = engine.is_suppressed(
        app_category="editor",
        is_fullscreen=True,
        paused_until=None,
        mode="camera",
        consent_camera=True,
        idle_seconds=100.0,
        face_detected=True,
        away_threshold_seconds=900.0,
        in_cooldown=False,
        confidence=0.85,
    )
    assert supp is True
    assert reason == "fullscreen_active"

    # 3. User paused
    supp, reason = engine.is_suppressed(
        app_category="editor",
        is_fullscreen=False,
        paused_until=time.time() + 300.0,
        mode="camera",
        consent_camera=True,
        idle_seconds=100.0,
        face_detected=True,
        away_threshold_seconds=900.0,
        in_cooldown=False,
        confidence=0.85,
    )
    assert supp is True
    assert reason == "user_paused"

    # 4. Away from desk (idle > 900s and no face)
    supp, reason = engine.is_suppressed(
        app_category="editor",
        is_fullscreen=False,
        paused_until=None,
        mode="camera",
        consent_camera=True,
        idle_seconds=1000.0,
        face_detected=False,
        away_threshold_seconds=900.0,
        in_cooldown=False,
        confidence=0.85,
    )
    assert supp is True
    assert reason == "away_from_desk"

    # 5. Permitted condition (editor, active face, unpaused)
    supp, reason = engine.is_suppressed(
        app_category="editor",
        is_fullscreen=False,
        paused_until=None,
        mode="camera",
        consent_camera=True,
        idle_seconds=100.0,
        face_detected=True,
        away_threshold_seconds=900.0,
        in_cooldown=False,
        confidence=0.85,
    )
    assert supp is False
    assert reason is None


def test_cooldown_doubling_on_dismissal():
    """Dismissing a nudge must double the cooldown duration."""
    cfg = NudgeConfig(
        default_cooldown_seconds=1800.0,  # 30m
        max_cooldown_seconds=10800.0,     # 3h
    )
    policy = NudgePolicy(cfg)

    t = 1000.0
    # First dismissal: doubles to 3600s
    policy.handle_feedback("nudge-1", "not_now", now=t)
    assert policy.is_in_cooldown(now=t + 100.0) is True
    assert policy.is_in_cooldown(now=t + 3599.0) is True
    assert policy.is_in_cooldown(now=t + 3601.0) is False

    # Second dismissal: doubles to 7200s
    t = 5000.0
    policy.handle_feedback("nudge-2", "dismissed", now=t)
    assert policy.is_in_cooldown(now=t + 7199.0) is True
    assert policy.is_in_cooldown(now=t + 7201.0) is False


def test_auto_expiry_silently_without_escalation():
    """Unanswered nudges auto-expire after 2 minutes and enter standard cooldown."""
    cfg = NudgeConfig(
        sustained_seconds=10.0,
        auto_expire_seconds=120.0,
        default_cooldown_seconds=1800.0,
    )
    policy = NudgePolicy(cfg)

    t = 1000.0
    # Trigger a nudge
    policy.evaluate_trigger(smoothed_score=0.90, is_suppressed=False, reasons=["test"], now=t)
    # Sustain
    nudge = policy.evaluate_trigger(
        smoothed_score=0.90, is_suppressed=False, reasons=["test"], now=t + 11.0
    )
    assert nudge is not None
    nudge_id = nudge["id"]
    assert policy._active_nudge_id == nudge_id

    # Check 1 minute later: still pending
    assert policy.check_auto_expire(now=t + 70.0) is False
    assert policy._active_nudge_id == nudge_id

    # Check after 2 minutes (125s elapsed): silently expired
    assert policy.check_auto_expire(now=t + 11.0 + 125.0) is True
    assert policy._active_nudge_id is None
    # Cooldown is active
    assert policy.is_in_cooldown(now=t + 150.0) is True


def test_adaptive_sensitivity_lowering():
    """Repeated dismissals should automatically decrease sensitivity to be quieter."""
    cfg = NudgeConfig(
        current_sensitivity="high",
        consecutive_dismissals_for_quieter=3,
    )
    changed_level = []
    policy = NudgePolicy(cfg, on_sensitivity_change=lambda lvl: changed_level.append(lvl))

    policy.handle_feedback("1", "not_now")
    assert policy.current_sensitivity == "high"

    policy.handle_feedback("2", "dismissed")
    assert policy.current_sensitivity == "high"

    # Third dismissal triggers adaptation
    policy.handle_feedback("3", "not_now")
    assert policy.current_sensitivity == "medium"
    assert "medium" in changed_level
