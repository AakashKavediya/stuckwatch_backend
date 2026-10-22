"""Local, low-friction intervention policy for possible task-paralysis signals."""

from __future__ import annotations

import collections
import time
import uuid
from typing import Callable, Deque, Optional

THRESHOLDS = {"low": 0.85, "medium": 0.75, "high": 0.65}
MESSAGE = "Been on this a while. Want a hand, or are you good?"
OPTIONS = ["tiny_step", "rest", "not_now"]
PROTECTED = {"video", "call", "game"}

STEPS = {
    "editor": "Open the file and write one line, even a bad one.",
    "browser": "Open one tab you need and read just the first paragraph.",
    "unknown": "Pick the smallest piece and spend two minutes on it.",
}
SMALLER_STEPS = {
    "editor": "Open the file and place the cursor where the next line goes.",
    "browser": "Open the one tab you need.",
    "unknown": "Name one tiny next action.",
}


class InterventionEngine:
    """Stateful intervention policy; all stored history is aggregate metadata."""

    def __init__(
        self,
        sensitivity: str = "medium",
        sustained_seconds: float = 120.0,
        max_nudges_per_day: int = 3,
        on_adaptation: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.sensitivity = sensitivity
        self.sustained_seconds = sustained_seconds
        self.max_nudges_per_day = max_nudges_per_day
        self.on_adaptation = on_adaptation
        self._above_since: Optional[float] = None
        self._cooldown_until = 0.0
        self._rest_until = 0.0
        self._active_id: Optional[str] = None
        self._active_time: Optional[float] = None
        self._nudge_times: Deque[float] = collections.deque()
        self._last_message = ""
        self._last_nudge_time = -float("inf")
        self._consecutive_quiet = 0
        self._adapted = False
        self._active_category = "unknown"
        self.demo_mode = False

    def set_demo_mode(self, enabled: bool) -> None:
        self.demo_mode = enabled
        self.sustained_seconds = 10.0 if enabled else 120.0
        self._cooldown_until = 0.0
        if enabled:
            self._last_nudge_time = -float("inf")

    def reset(self) -> None:
        self._above_since = None
        self._cooldown_until = 0.0
        self._rest_until = 0.0
        self._active_id = None
        self._active_time = None
        self._nudge_times.clear()
        self._last_nudge_time = -float("inf")

    def set_sensitivity(self, value: str) -> bool:
        if value not in THRESHOLDS:
            return False
        self.sensitivity = value
        return True

    def _prune(self, now: float) -> None:
        while self._nudge_times and self._nudge_times[0] < now - 86400:
            self._nudge_times.popleft()

    def is_resting(self, now: Optional[float] = None) -> bool:
        return (time.time() if now is None else now) < self._rest_until

    def evaluate(
        self,
        score: float,
        app_category: str,
        fullscreen: bool,
        paused: bool,
        now: Optional[float] = None,
        reasons: Optional[list[str]] = None,
    ) -> Optional[dict]:
        current = time.time() if now is None else now
        self._prune(current)
        if self._active_id is not None and self._active_time is not None:
            if current - self._active_time >= 60:
                self.respond(self._active_id, "not_now", current)
                self._cooldown_until = current + 600
        protected = app_category in PROTECTED or fullscreen
        blocked = paused or self.is_resting(current) or protected
        blocked = blocked or current < self._cooldown_until
        blocked = blocked or current - self._last_nudge_time < (30 if self.demo_mode else 1200)
        blocked = blocked or (
            not self.demo_mode and len(self._nudge_times) >= self.max_nudges_per_day
        )
        if blocked or score < THRESHOLDS[self.sensitivity]:
            self._above_since = None
            return None
        if self._above_since is None:
            self._above_since = current
            if self.sustained_seconds > 0:
                return None
        if current - self._above_since < self.sustained_seconds:
            return None
        self._above_since = None
        nudge_id = str(uuid.uuid4())
        message = MESSAGE
        if message == self._last_message:
            message = "Would one very small step or a short break help?"
        self._last_message = message
        self._active_id = nudge_id
        self._active_time = current
        self._last_nudge_time = current
        self._active_category = app_category if app_category in STEPS else "unknown"
        self._nudge_times.append(current)
        return {
            "id": nudge_id,
            "message": message,
            "options": OPTIONS,
            "reasons": reasons or ["the document has been still for a while"],
            "silent": True,
        }

    def respond(self, nudge_id: str, choice: str, now: Optional[float] = None) -> Optional[dict]:
        if self._active_id != nudge_id:
            return None
        current = time.time() if now is None else now
        self._active_id = None
        self._active_time = None
        if choice == "tiny_step":
            self._cooldown_until = current + (30 if self.demo_mode else 120)
            self._consecutive_quiet = 0
            return {"type": "tiny_step", "text": STEPS[self._active_category]}
        if choice == "change":
            self._cooldown_until = current + (30 if self.demo_mode else 120)
            return {"type": "tiny_step", "text": SMALLER_STEPS[self._active_category]}
        if choice == "rest":
            self._rest_until = current + (30 if self.demo_mode else 600)
            self._cooldown_until = self._rest_until
            self._consecutive_quiet = 0
            return {"type": "rest_started", "minutes": 10}
        if choice == "i_am_fine":
            self._cooldown_until = current + (30 if self.demo_mode else 5400)
            self._consecutive_quiet += 1
            self._lower_sensitivity()
            self._adapt_if_needed()
            return None
        if choice == "not_now":
            self._cooldown_until = current + (30 if self.demo_mode else 2700)
            self._consecutive_quiet += 1
            self._adapt_if_needed()
        return None

    def _lower_sensitivity(self) -> None:
        if self.sensitivity == "high":
            self.sensitivity = "medium"
        elif self.sensitivity == "medium":
            self.sensitivity = "low"

    def _adapt_if_needed(self) -> None:
        if self._consecutive_quiet < 3 or self._adapted:
            return
        self._adapted = True
        if self.sensitivity == "high":
            self.sensitivity = "medium"
        elif self.sensitivity == "medium":
            self.sensitivity = "low"
        if self.on_adaptation:
            self.on_adaptation(
                "I’ll check in less often for a while. You can change this anytime."
            )

    def rest_finished(self, now: Optional[float] = None) -> bool:
        current = time.time() if now is None else now
        if self._rest_until and current >= self._rest_until:
            self._rest_until = 0.0
            self._cooldown_until = current
            return True
        return False

    @property
    def nudges_today(self) -> int:
        self._prune(time.time())
        return len(self._nudge_times)
