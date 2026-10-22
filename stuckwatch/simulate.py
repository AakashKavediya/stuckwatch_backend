"""Synthetic signal generator for demo and frontend development without hardware cameras.

Generates realistic proxy signals along a scripted timeline:
  0s - 25s:   Normal productive work (active typing, low idle, normal blinks)
  25s - 85s:  Task paralysis proxy phase (idle editor, zero typing, fixed gaze, low blinks)
              -> Triggers a realistic nudge event within ~60 seconds of starting
  85s - 130s: Resting/video phase (category 'video', suppressed)
  130s+:      Cycle loops smoothly
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class SyntheticSignals:
    idle_seconds: float
    typing_rate: float
    app_switches_per_min: float
    app_category: str
    blink_rate: Optional[float]
    gaze_stability: Optional[float]
    head_motion: Optional[float]
    face_detected: bool
    is_fullscreen: bool


class SignalSimulator:
    """Produces scripted synthetic feature streams for testing and demonstration."""

    def __init__(self, start_time: Optional[float] = None, scenario: str = "cycle") -> None:
        self._start_time = time.time() if start_time is None else start_time
        self.scenario = scenario

    def reset(self) -> None:
        self._start_time = time.time()

    def get_signals(self, now: Optional[float] = None) -> SyntheticSignals:
        current_time = time.time() if now is None else now
        elapsed = max(0.0, current_time - self._start_time)
        if self.scenario == "stuck_doc":
            return SyntheticSignals(
                idle_seconds=min(180.0, elapsed),
                typing_rate=0.0,
                app_switches_per_min=0.0,
                app_category="editor",
                blink_rate=8.0,
                gaze_stability=0.94,
                head_motion=0.01,
                face_detected=True,
                is_fullscreen=False,
            )
        if self.scenario == "watching_video":
            return SyntheticSignals(
                idle_seconds=min(180.0, elapsed),
                typing_rate=0.0,
                app_switches_per_min=0.0,
                app_category="video",
                blink_rate=14.0,
                gaze_stability=0.7,
                head_motion=0.025,
                face_detected=True,
                is_fullscreen=True,
            )
        if self.scenario == "busy_typing":
            return SyntheticSignals(
                idle_seconds=1.0,
                typing_rate=100.0,
                app_switches_per_min=1.0,
                app_category="editor",
                blink_rate=16.0,
                gaze_stability=0.55,
                head_motion=0.035,
                face_detected=True,
                is_fullscreen=False,
            )
        cycle_sec = elapsed % 140.0

        if cycle_sec < 25.0:
            # Phase 1: Active, productive work in editor
            typing_rate = 110.0 + 15.0 * math.sin(cycle_sec)
            idle_seconds = max(0.5, 3.0 + 2.0 * math.cos(cycle_sec))
            app_category = "editor"
            blink_rate = 16.0 + 2.0 * math.sin(cycle_sec * 0.5)
            gaze_stability = 0.55 + 0.10 * math.sin(cycle_sec * 0.3)
            head_motion = 0.035 + 0.010 * math.cos(cycle_sec * 0.2)
            face_detected = True
            is_fullscreen = False

        elif cycle_sec < 85.0:
            # Phase 2: Task paralysis / screen stagnation (stuck phase)
            # Begun at 25s; by 40-50s idle is high and fixed gaze is pronounced
            stuck_elapsed = cycle_sec - 25.0
            idle_seconds = 45.0 + (stuck_elapsed * 2.0)
            typing_rate = 0.0
            app_category = "editor"
            blink_rate = max(6.0, 10.0 - (stuck_elapsed * 0.08))
            gaze_stability = min(0.96, 0.82 + (stuck_elapsed * 0.003))
            head_motion = max(0.008, 0.018 - (stuck_elapsed * 0.0002))
            face_detected = True
            is_fullscreen = False

        elif cycle_sec < 115.0:
            # Phase 3: Resting or watching a tutorial/video (suppressed by policy)
            rest_elapsed = cycle_sec - 85.0
            idle_seconds = 10.0 + rest_elapsed
            typing_rate = 0.0
            app_category = "video"
            blink_rate = 14.0
            gaze_stability = 0.70
            head_motion = 0.025
            face_detected = True
            is_fullscreen = True

        else:
            # Phase 4: Gentle resumption of work
            idle_seconds = 2.0
            typing_rate = 80.0
            app_category = "editor"
            blink_rate = 15.0
            gaze_stability = 0.60
            head_motion = 0.030
            face_detected = True
            is_fullscreen = False

        app_switches = 0.0 if cycle_sec >= 25.0 and cycle_sec < 85.0 else 2.0

        return SyntheticSignals(
            idle_seconds=float(idle_seconds),
            typing_rate=float(typing_rate),
            app_switches_per_min=float(app_switches),
            app_category=app_category,
            blink_rate=float(blink_rate),
            gaze_stability=float(gaze_stability),
            head_motion=float(head_motion),
            face_detected=face_detected,
            is_fullscreen=is_fullscreen,
        )
