"""Suppression rules to ensure nudges never interrupt critical or resting contexts.

A nudge MUST BE SUPPRESSED if ANY of the following hold:
  1. Active foreground category is 'video', 'call', or 'game'.
  2. Foreground window is fullscreen.
  3. User has paused or snoozed monitoring.
  4. Camera consent is missing while in camera mode.
  5. User is away from desk (idle > away threshold and no face detected).
  6. Within active cooldown period.
  7. Confidence is below minimum threshold.
  8. Hourly or daily nudge frequency limits are exceeded.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

from stuckwatch.logging_setup import get_logger

logger = get_logger("policy.suppression")


class SuppressionEngine:
    """Evaluates contextual and temporal suppression conditions."""

    SUPPRESSED_CATEGORIES = {"video", "call", "game"}

    def is_suppressed(
        self,
        app_category: str,
        is_fullscreen: bool,
        paused_until: Optional[float],
        mode: str,
        consent_camera: bool,
        idle_seconds: float,
        face_detected: bool,
        away_threshold_seconds: float,
        in_cooldown: bool,
        confidence: float,
        min_confidence: float = 0.30,
        rate_limited: bool = False,
        now: Optional[float] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Evaluate all suppression rules.

        Returns:
            (is_suppressed, suppression_reason)
        """
        current_time = time.time() if now is None else now

        # Rule 1: Protected app categories (video watching, calls, gaming)
        if app_category in self.SUPPRESSED_CATEGORIES:
            return True, f"active_category_{app_category}"

        # Rule 2: Fullscreen applications (presentations, full-screen playback/games)
        if is_fullscreen:
            return True, "fullscreen_active"

        # Rule 3: Explicit user pause / snooze
        if paused_until is not None and current_time < paused_until:
            return True, "user_paused"

        # Rule 4: Consent check for camera mode
        if mode == "camera" and not consent_camera:
            return True, "camera_consent_revoked"

        # Rule 5: Away from desk (idle for long duration and no face detected)
        if idle_seconds >= away_threshold_seconds and not face_detected:
            return True, "away_from_desk"

        # Rule 6: Within cooldown from previous nudge or dismissal
        if in_cooldown:
            return True, "cooldown_active"

        # Rule 7: Model confidence below threshold
        if confidence < min_confidence:
            return True, "low_confidence"

        # Rule 8: Rate limit safety caps (max per hour / max per day)
        if rate_limited:
            return True, "rate_limit_reached"

        return False, None
