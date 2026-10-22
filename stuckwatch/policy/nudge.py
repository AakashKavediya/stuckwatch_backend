"""Gentle, low-pressure nudge policy management.

Enforces:
  - Sustained elevated stuck_likelihood before triggering (no instantaneous triggers).
  - Exponentially increasing cooldown on dismissal (30m -> 1h -> 2h -> 3h).
  - Silent auto-expiration after 2 minutes if ignored (NO escalation, NO repeated chime).
  - Hard safety rate limits (max 3/hr, max 6/day).
  - Rotating low-guilt, non-clinical supportive messages.
  - Automatic quieter sensitivity adaptation after repeated dismissals.
"""

from __future__ import annotations

import collections
import time
import uuid
from typing import Callable, Deque, List, Optional, Tuple

from stuckwatch.config import NudgeConfig
from stuckwatch.logging_setup import get_logger

logger = get_logger("policy.nudge")

GENTLE_MESSAGES = [
    "Been on this a while. Want a hand, or are you good?",
    "Want to try one tiny step, or take a breather?",
    "No pressure. A small start or a short rest?",
    "Checking in gently. Take a micro-step, or pause for a bit?",
]

STANDARD_OPTIONS = ["tiny_start", "rest", "not_now"]


class NudgePolicy:
    """Orchestrates nudge triggering, cooldowns, auto-expiry, and feedback handling."""

    def __init__(
        self,
        config: NudgeConfig,
        on_sensitivity_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.config = config
        self.on_sensitivity_change = on_sensitivity_change

        self._current_cooldown_duration = self.config.default_cooldown_seconds
        self._cooldown_until: float = 0.0

        self._sustained_start_time: Optional[float] = None
        self._message_index: int = 0

        self._active_nudge_id: Optional[str] = None
        self._active_nudge_time: Optional[float] = None

        self._hourly_nudge_timestamps: Deque[float] = collections.deque()
        self._daily_nudge_timestamps: Deque[float] = collections.deque()

        self._consecutive_dismissals: int = 0

    @property
    def current_sensitivity(self) -> str:
        return self.config.current_sensitivity

    def set_sensitivity(self, level: str) -> None:
        if level in self.config.sensitivity_thresholds:
            self.config.current_sensitivity = level
            logger.info("Sensitivity set to: %s", level)

    def is_in_cooldown(self, now: Optional[float] = None) -> bool:
        current_time = time.time() if now is None else now
        return current_time < self._cooldown_until

    def is_rate_limited(self, now: Optional[float] = None) -> bool:
        current_time = time.time() if now is None else now
        # Prune older than 1 hour (3600s)
        cutoff_hour = current_time - 3600.0
        while self._hourly_nudge_timestamps and self._hourly_nudge_timestamps[0] < cutoff_hour:
            self._hourly_nudge_timestamps.popleft()

        # Prune older than 24 hours (86400s)
        cutoff_day = current_time - 86400.0
        while self._daily_nudge_timestamps and self._daily_nudge_timestamps[0] < cutoff_day:
            self._daily_nudge_timestamps.popleft()

        if len(self._hourly_nudge_timestamps) >= self.config.max_nudges_per_hour:
            return True
        if len(self._daily_nudge_timestamps) >= self.config.max_nudges_per_day:
            return True
        return False

    def check_auto_expire(self, now: Optional[float] = None) -> bool:
        """Check if active nudge timed out without user response."""
        current_time = time.time() if now is None else now
        if self._active_nudge_id is not None and self._active_nudge_time is not None:
            if (current_time - self._active_nudge_time) >= self.config.auto_expire_seconds:
                logger.info(
                    "Active nudge %s auto-expired silently after %.0fs without response.",
                    self._active_nudge_id,
                    self.config.auto_expire_seconds,
                )
                self._active_nudge_id = None
                self._active_nudge_time = None
                # Enter default cooldown without escalating
                self._cooldown_until = current_time + self._current_cooldown_duration
                return True
        return False

    def evaluate_trigger(
        self,
        smoothed_score: float,
        is_suppressed: bool,
        reasons: List[str],
        now: Optional[float] = None,
        accelerated: bool = False,
    ) -> Optional[dict]:
        """Evaluate whether a new nudge should be emitted.

        Returns:
            dict containing nudge event parameters, or None.
        """
        current_time = time.time() if now is None else now

        # Auto-expire pending unanswered nudge if overdue
        self.check_auto_expire(now=current_time)

        # If a nudge is already pending response, do not emit another
        if self._active_nudge_id is not None:
            return None

        # Suppression check
        if is_suppressed or self.is_in_cooldown(now=current_time) or self.is_rate_limited(now=current_time):
            self._sustained_start_time = None
            return None

        threshold = self.config.sensitivity_thresholds.get(
            self.config.current_sensitivity, 0.75
        )

        if smoothed_score < threshold:
            self._sustained_start_time = None
            return None

        # Score is above threshold
        if self._sustained_start_time is None:
            self._sustained_start_time = current_time

        # Check sustained duration
        effective_sustained = (
            15.0 if accelerated else self.config.sustained_seconds
        )
        if (current_time - self._sustained_start_time) < effective_sustained:
            return None

        # Sustained threshold met! Generate gentle nudge
        nudge_id = str(uuid.uuid4())
        message = GENTLE_MESSAGES[self._message_index % len(GENTLE_MESSAGES)]
        self._message_index += 1

        self._active_nudge_id = nudge_id
        self._active_nudge_time = current_time
        self._sustained_start_time = None

        # Record timestamps for rate limiting
        self._hourly_nudge_timestamps.append(current_time)
        self._daily_nudge_timestamps.append(current_time)

        # Enter baseline cooldown
        self._cooldown_until = current_time + self._current_cooldown_duration

        logger.info("Emitting nudge %s (reasons: %s)", nudge_id, reasons)
        return {
            "id": nudge_id,
            "message": message,
            "options": STANDARD_OPTIONS,
            "reasons": reasons,
        }

    def handle_feedback(
        self, nudge_id: str, choice: str, now: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """Process user response to a nudge.

        Returns:
            (acknowledged, new_sensitivity_if_changed)
        """
        current_time = time.time() if now is None else now

        if self._active_nudge_id == nudge_id:
            self._active_nudge_id = None
            self._active_nudge_time = None

        sensitivity_changed: Optional[str] = None

        if choice in ("not_now", "dismissed"):
            # Double cooldown on dismissal, capped at max_cooldown_seconds (3 hours)
            self._current_cooldown_duration = min(
                self.config.max_cooldown_seconds,
                self._current_cooldown_duration * 2.0,
            )
            self._cooldown_until = current_time + self._current_cooldown_duration
            self._consecutive_dismissals += 1
            logger.info(
                "Nudge dismissed. Cooldown doubled to %.0f minutes (consecutive: %d).",
                self._current_cooldown_duration / 60.0,
                self._consecutive_dismissals,
            )

            # Quiet adaptation: after repeated dismissals, lower sensitivity
            if self._consecutive_dismissals >= self.config.consecutive_dismissals_for_quieter:
                self._consecutive_dismissals = 0
                if self.config.current_sensitivity == "high":
                    self.set_sensitivity("medium")
                    sensitivity_changed = "medium"
                elif self.config.current_sensitivity == "medium":
                    self.set_sensitivity("low")
                    sensitivity_changed = "low"

                if sensitivity_changed and self.on_sensitivity_change:
                    self.on_sensitivity_change(sensitivity_changed)

        elif choice == "rest":
            # Cooldown for resting: 60 minutes
            self._current_cooldown_duration = self.config.rest_cooldown_seconds
            self._cooldown_until = current_time + self._current_cooldown_duration
            self._consecutive_dismissals = 0
            logger.info("User chose 'rest'. Cooldown set to 60 minutes.")

        elif choice in ("tiny_start", "change"):
            # Reset cooldown back to default
            self._current_cooldown_duration = self.config.default_cooldown_seconds
            self._cooldown_until = current_time + self._current_cooldown_duration
            self._consecutive_dismissals = 0
            logger.info("User engaged with nudge ('%s'). Cooldown reset to default.", choice)

        return True, sensitivity_changed
