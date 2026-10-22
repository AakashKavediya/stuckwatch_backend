from __future__ import annotations

from stuckwatch.interventions import InterventionEngine


def test_sustained_editor_signal_emits_one_nudge():
    policy = InterventionEngine(sustained_seconds=120)
    assert policy.evaluate(0.9, "editor", False, False, now=0) is None
    assert policy.evaluate(0.9, "editor", False, False, now=119) is None
    nudge = policy.evaluate(0.9, "editor", False, False, now=120)
    assert nudge is not None
    assert nudge["silent"] is True
    assert nudge["options"] == ["tiny_step", "rest", "not_now"]


def test_video_typing_and_pause_are_suppressed():
    for category, fullscreen, paused in (("video", True, False), ("editor", False, True)):
        policy = InterventionEngine(sustained_seconds=0)
        assert policy.evaluate(0.99, category, fullscreen, paused, now=0) is None

    policy = InterventionEngine(sustained_seconds=0)
    assert policy.evaluate(0.1, "editor", False, False, now=0) is None


def test_not_now_cooldown_and_tiny_step():
    policy = InterventionEngine(sustained_seconds=0)
    nudge = policy.evaluate(0.9, "editor", False, False, now=0)
    assert nudge is not None
    assert policy.respond(nudge["id"], "not_now", now=0) is None
    assert policy.evaluate(0.9, "editor", False, False, now=2699) is None
    assert policy.evaluate(0.9, "editor", False, False, now=2701) is not None


def test_response_returns_category_step_and_smaller_change():
    policy = InterventionEngine(sustained_seconds=0)
    nudge = policy.evaluate(0.9, "browser", False, False, now=0)
    assert nudge is not None
    step = policy.respond(nudge["id"], "tiny_step", now=0)
    assert step["type"] == "tiny_step"
    assert "tab" in step["text"]


def test_activity_rates_decay_and_idle_only_changes_on_events():
    from stuckwatch.context.activity import ActivityTracker

    tracker = ActivityTracker(rolling_window_seconds=60)
    tracker._record_key_event(timestamp=0)
    tracker._record_key_event(timestamp=1)
    tracker._last_activity_time = 1
    import stuckwatch.context.activity as activity_module

    original_time = activity_module.time.time
    activity_module.time.time = lambda: 2
    try:
        idle, typing, _, _ = tracker.get_metrics()
        assert idle == 1
        assert typing == 2.0
        activity_module.time.time = lambda: 70
        idle, typing, _, _ = tracker.get_metrics()
        assert idle == 69
        assert typing == 0.0
    finally:
        activity_module.time.time = original_time


def test_simulation_scenarios_have_expected_intervention_outcomes():
    from stuckwatch.simulate import SignalSimulator

    cases = {
        "stuck_doc": True,
        "watching_video": False,
        "busy_typing": False,
    }
    for scenario, should_nudge in cases.items():
        simulator = SignalSimulator(start_time=0, scenario=scenario)
        policy = InterventionEngine(sustained_seconds=120)
        nudges = []
        for now in range(0, 181, 30):
            signal = simulator.get_signals(now)
            nudge = policy.evaluate(
                score=0.9 if scenario == "stuck_doc" else 0.1,
                app_category=signal.app_category,
                fullscreen=signal.is_fullscreen,
                paused=False,
                now=now,
            )
            if nudge:
                nudges.append(nudge)
        assert bool(nudges) is should_nudge
        assert len(nudges) == 1 if should_nudge else len(nudges) == 0
