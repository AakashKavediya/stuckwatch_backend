"""Transparent weighted-evidence estimator for stuck likelihood.

CRITICAL ETHICAL RULES:
  - Output is documented as an uncertain proxy indicator ('might be stuck'), NEVER a clinical fact.
  - NO clinical or diagnostic labels are ever emitted.
  - The model fuses weak signals transparently and explains the top contributing reasons.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from stuckwatch.config import ModelConfig
from stuckwatch.logging_setup import get_logger
from stuckwatch.model.baseline import BaselineProfile

logger = get_logger("model.estimator")


class StuckEstimator:
    """Transparent weighted evidence model combining vision and context signals."""

    def __init__(self, config: ModelConfig) -> None:
        self.config = config
        self._smoothed_score: float = 0.0
        self._sample_count: int = 0

    @property
    def smoothed_score(self) -> float:
        return self._smoothed_score

    def reset(self) -> None:
        self._smoothed_score = 0.0
        self._sample_count = 0

    def estimate(
        self,
        idle_seconds: float,
        typing_rate: float,
        app_switches_per_min: float,
        app_category: str,
        blink_rate: Optional[float],
        gaze_stability: Optional[float],
        head_motion: Optional[float],
        baseline: BaselineProfile,
        mode: str = "camera",
        face_detected: bool = False,
    ) -> Tuple[float, float, List[str]]:
        """Compute stuck_likelihood, confidence, and human-readable explanation reasons.

        Returns:
            (stuck_likelihood, confidence, reasons)
        """
        evidence_scores: dict[str, float] = {}
        reason_phrases: dict[str, str] = {}

        # ---------------- 1. Screen Stagnation Evidence ----------------
        # Long idle time while an editor or document application is in the foreground
        if app_category == "editor":
            if idle_seconds > 45.0:
                idle_min = int(idle_seconds // 60)
                stagnation = min(1.0, (idle_seconds - 45.0) / 180.0)
                evidence_scores["screen_stagnation"] = stagnation
                if idle_min >= 2:
                    reason_phrases["screen_stagnation"] = (
                        f"no typing for {idle_min} minutes in your editor"
                    )
                else:
                    reason_phrases["screen_stagnation"] = (
                        "screen idle in your editor for over a minute"
                    )
            else:
                evidence_scores["screen_stagnation"] = 0.0
        else:
            evidence_scores["screen_stagnation"] = 0.0

        # ---------------- 2. Erratic Switching Evidence ----------------
        # Rapid switching between apps/tabs without productive keystroke input
        if app_switches_per_min >= 3.0 and typing_rate < 35.0:
            switch_intensity = min(1.0, (app_switches_per_min - 2.5) / 5.0)
            low_typing_factor = max(0.0, (40.0 - typing_rate) / 40.0)
            erratic = switch_intensity * low_typing_factor
            evidence_scores["erratic_switching"] = erratic
            reason_phrases["erratic_switching"] = (
                "frequent switching between apps without much progress"
            )
        else:
            evidence_scores["erratic_switching"] = 0.0

        # ---------------- 3. Fixed Gaze Evidence (Vision) ----------------
        # High gaze stability (staring blankly) while input activity is idle
        if gaze_stability is not None and idle_seconds > 20.0 and face_detected:
            gaze_factor = min(1.0, max(0.0, gaze_stability))
            idle_factor = min(1.0, idle_seconds / 90.0)
            fixed_gaze = gaze_factor * idle_factor
            evidence_scores["fixed_gaze"] = fixed_gaze
            if fixed_gaze > 0.4:
                reason_phrases["fixed_gaze"] = (
                    "fixed gaze on screen with no recent typing"
                )
        else:
            evidence_scores["fixed_gaze"] = 0.0

        # ---------------- 4. Blink Anomaly Evidence (Vision) ----------------
        # Blink rate deviates significantly (high or low) from user's personal baseline
        if blink_rate is not None and face_detected:
            z_blink = baseline.zscore("blink_rate", blink_rate)
            abs_z = abs(z_blink)
            if abs_z > 1.2:
                blink_anomaly = min(1.0, (abs_z - 1.0) / 1.5)
                evidence_scores["blink_anomaly"] = blink_anomaly
                if z_blink < -1.2:
                    reason_phrases["blink_anomaly"] = (
                        "prolonged staring with notably reduced blinking"
                    )
                else:
                    reason_phrases["blink_anomaly"] = (
                        "blink rate notably higher than your normal baseline"
                    )
            else:
                evidence_scores["blink_anomaly"] = 0.0
        else:
            evidence_scores["blink_anomaly"] = 0.0

        # ---------------- 5. Frozen Posture Evidence (Vision) ----------------
        # Extremely low head motion while idle
        if head_motion is not None and idle_seconds > 45.0 and face_detected:
            z_motion = baseline.zscore("head_motion", head_motion)
            if z_motion < -0.8:
                frozen = min(1.0, (-z_motion - 0.5) / 1.8)
                evidence_scores["frozen_posture"] = frozen
                if frozen > 0.4:
                    reason_phrases["frozen_posture"] = (
                        "very still, frozen posture at the screen"
                    )
            else:
                evidence_scores["frozen_posture"] = 0.0
        else:
            evidence_scores["frozen_posture"] = 0.0

        # ---------------- Fusion via Logistic Combination ----------------
        logit = (
            self.config.bias
            + self.config.w_screen_stagnation * evidence_scores.get("screen_stagnation", 0.0)
            + self.config.w_erratic_switching * evidence_scores.get("erratic_switching", 0.0)
            + self.config.w_fixed_gaze * evidence_scores.get("fixed_gaze", 0.0)
            + self.config.w_blink_anomaly * evidence_scores.get("blink_anomaly", 0.0)
            + self.config.w_frozen_posture * evidence_scores.get("frozen_posture", 0.0)
        )

        raw_score = 1.0 / (1.0 + math.exp(-max(-10.0, min(10.0, logit))))

        vision_active = (
            mode in ("camera", "hybrid")
            and face_detected
            and any(value is not None for value in (blink_rate, gaze_stability, head_motion))
        )

        # In screen-only mode, cap score so webcam-free mode requires solid evidence to nudge
        if mode == "screen_only" or not vision_active:
            raw_score = min(self.config.screen_only_score_cap, raw_score)

        # ---------------- Temporal Smoothing (EMA) ----------------
        self._sample_count += 1
        if self._sample_count <= 1:
            self._smoothed_score = raw_score
        else:
            alpha = self.config.ema_alpha
            self._smoothed_score = (alpha * raw_score) + ((1.0 - alpha) * self._smoothed_score)

        # ---------------- Confidence Computation ----------------
        confidence = 0.90
        # Personal baseline bonus/penalty
        if not baseline.has_feature("blink_rate") and not baseline.has_feature("typing_rate"):
            confidence -= 0.15

        if mode == "screen_only" or not vision_active:
            confidence -= 0.25
        elif vision_active:
            if baseline.has_feature("blink_rate") or baseline.has_feature("typing_rate"):
                confidence += 0.20

        confidence = max(0.10, min(1.0, confidence))

        # ---------------- Explainability (Top Reasons) ----------------
        # Sort evidence contributors by weighted impact
        weights = {
            "screen_stagnation": self.config.w_screen_stagnation,
            "erratic_switching": self.config.w_erratic_switching,
            "fixed_gaze": self.config.w_fixed_gaze,
            "blink_anomaly": self.config.w_blink_anomaly,
            "frozen_posture": self.config.w_frozen_posture,
        }
        scored_reasons = []
        for k, score in evidence_scores.items():
            if score > 0.2 and k in reason_phrases:
                weighted_impact = score * weights.get(k, 1.0)
                scored_reasons.append((weighted_impact, reason_phrases[k]))

        scored_reasons.sort(key=lambda x: x[0], reverse=True)
        top_reasons = [item[1] for item in scored_reasons[:3]]

        return float(self._smoothed_score), float(confidence), top_reasons
