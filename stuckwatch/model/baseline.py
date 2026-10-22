"""Personal baseline calibration and z-score computation.

Allows optional per-user calibration over a short working session (default 180s)
to adapt to individual blink frequencies, gaze patterns, and typing paces.
Falls back to population defaults when no calibration data exists.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from stuckwatch.logging_setup import get_logger
from stuckwatch.storage import Storage

logger = get_logger("model.baseline")

# Population defaults for proxy signals when no individual calibration exists
POPULATION_DEFAULTS: Dict[str, Tuple[float, float]] = {
    # feature_name: (mean, std)
    "blink_rate": (16.0, 5.0),          # Blinks per minute
    "gaze_stability": (0.60, 0.15),      # Normalized stability (0-1)
    "head_motion": (0.035, 0.015),       # Normalized variance
    "typing_rate": (100.0, 50.0),        # Keystrokes per minute
    "app_switches": (2.5, 1.5),          # Category changes per minute
}


@dataclass
class BaselineProfile:
    """Stores per-feature mean and standard deviation."""
    # feature -> (mean, std, updated_at)
    features: Dict[str, Tuple[float, float, float]] = field(default_factory=dict)

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def get_stats(self, feature: str) -> Tuple[float, float, bool]:
        """Return (mean, std, is_personal_baseline)."""
        if feature in self.features:
            mean, std, _ = self.features[feature]
            return mean, max(std, 1e-4), True

        if feature in POPULATION_DEFAULTS:
            mean, std = POPULATION_DEFAULTS[feature]
            return mean, std, False

        return 0.0, 1.0, False

    def zscore(self, feature: str, value: Optional[float]) -> float:
        """Compute clamped z-score in [-3.0, 3.0].

        Returns 0.0 if value is None.
        """
        if value is None:
            return 0.0

        mean, std, _ = self.get_stats(feature)
        if std < 1e-5:
            return 0.0

        raw_z = (float(value) - mean) / std
        return float(max(-3.0, min(3.0, raw_z)))


class BaselineCalibrator:
    """Manages an opt-in calibration session while user works normally."""

    def __init__(self, storage: Storage, target_seconds: int = 180) -> None:
        self.storage = storage
        self.target_seconds = target_seconds
        self._samples: Dict[str, List[float]] = {
            "blink_rate": [],
            "gaze_stability": [],
            "head_motion": [],
            "typing_rate": [],
            "app_switches": [],
        }
        self._start_time: Optional[float] = None
        self._is_active = False

    @property
    def is_active(self) -> bool:
        return self._is_active

    def start(self, seconds: Optional[int] = None) -> None:
        if seconds is not None and seconds > 10:
            self.target_seconds = seconds
        self._start_time = time.time()
        self._is_active = True
        for k in self._samples:
            self._samples[k].clear()
        logger.info("Starting baseline calibration for %d seconds...", self.target_seconds)

    def cancel(self) -> None:
        self._is_active = False
        self._start_time = None
        for k in self._samples:
            self._samples[k].clear()
        logger.info("Baseline calibration cancelled.")

    def add_sample(self, feature_data: Dict[str, Optional[float]]) -> None:
        if not self._is_active:
            return

        for k, v in feature_data.items():
            if k in self._samples and v is not None and not math.isnan(v):
                self._samples[k].append(float(v))

    def get_progress_percent(self) -> int:
        if not self._is_active or self._start_time is None:
            return 0
        elapsed = time.time() - self._start_time
        pct = int(min(100.0, (elapsed / max(1.0, float(self.target_seconds))) * 100.0))
        return pct

    def is_complete(self) -> bool:
        if not self._is_active or self._start_time is None:
            return False
        return (time.time() - self._start_time) >= self.target_seconds

    def finalize(self) -> Tuple[bool, int, BaselineProfile]:
        """Compute mean and std for each feature, persist to SQLite, and return profile."""
        if not self._is_active:
            return False, 0, self.load_profile()

        total_samples = 0
        profile = BaselineProfile()

        for feature, vals in self._samples.items():
            if len(vals) >= 5:
                mean = float(np.mean(vals))
                std = float(np.std(vals))
                if std < 1e-4:
                    # Provide slight variance buffer if values were static
                    std = max(1e-2, abs(mean) * 0.1)
                self.storage.save_baseline_feature(feature, mean, std)
                profile.features[feature] = (mean, std, time.time())
                total_samples += len(vals)
            else:
                logger.warning(
                    "Feature %s had only %d samples during calibration; falling back to default.",
                    feature,
                    len(vals),
                )

        self._is_active = False
        self._start_time = None
        ok = total_samples >= 15
        logger.info("Calibration finalized. Success: %s, total samples: %d", ok, total_samples)
        return ok, total_samples, profile

    def load_profile(self) -> BaselineProfile:
        """Load currently stored baseline profile from database."""
        raw_map = self.storage.get_all_baselines()
        return BaselineProfile(features=raw_map)
