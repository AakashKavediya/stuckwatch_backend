"""Configuration dataclasses and settings management for stuckwatch."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict


@dataclass
class VisionConfig:
    """Settings for camera and facial landmark extraction."""
    camera_index: int = 0
    frame_width: int = 640
    frame_height: int = 480
    target_fps: float = 12.0
    ear_blink_threshold: float = 0.21
    min_blink_frames: int = 2
    blink_window_seconds: float = 60.0
    blink_min_data_seconds: float = 20.0
    gaze_window_seconds: float = 15.0
    head_motion_window_seconds: float = 15.0


@dataclass
class ContextConfig:
    """Settings for keyboard, mouse, and active-window tracking."""
    poll_interval_seconds: float = 1.0
    idle_away_threshold_seconds: float = 900.0  # 15 minutes without activity = away
    rolling_rate_window_seconds: float = 60.0


@dataclass
class ModelConfig:
    """Weights and parameters for the transparent evidence estimator.

    All evidence items represent weak proxy indicators, never definitive clinical states:
      - w_screen_stagnation: long idle time while an editor/document app is foreground
      - w_erratic_switching: low typing rate coupled with high app switches
      - w_fixed_gaze: high gaze stability while no input is occurring
      - w_blink_anomaly: blink rate deviation (|z-score|) from personal baseline
      - w_frozen_posture: extremely low head motion over an extended period
    """
    bias: float = -2.2
    w_screen_stagnation: float = 1.8
    w_erratic_switching: float = 1.4
    w_fixed_gaze: float = 1.2
    w_blink_anomaly: float = 0.9
    w_frozen_posture: float = 1.0
    ema_alpha: float = 0.08  # ~25-30s smoothing time constant at 1 Hz
    screen_only_score_cap: float = 0.85
    min_confidence_threshold: float = 0.30


@dataclass
class NudgeConfig:
    """Policy rules, cooldowns, and thresholds for gentleness."""
    # Sensitivity thresholds for stuck_likelihood
    sensitivity_thresholds: Dict[str, float] = field(
        default_factory=lambda: {
            "low": 0.85,
            "medium": 0.75,
            "high": 0.65,
        }
    )
    current_sensitivity: str = "medium"
    sustained_seconds: float = 90.0  # Must stay above threshold for 90s
    default_cooldown_seconds: float = 1800.0  # 30 minutes
    max_cooldown_seconds: float = 10800.0  # 3 hours
    rest_cooldown_seconds: float = 3600.0  # 1 hour
    auto_expire_seconds: float = 120.0  # 2 minutes auto-expiry for ignored nudges
    max_nudges_per_hour: int = 3
    max_nudges_per_day: int = 6
    consecutive_dismissals_for_quieter: int = 3


@dataclass
class StuckwatchConfig:
    """Root configuration holding all module tunables."""
    mode: str = "hybrid"  # 'camera', 'screen_only', 'hybrid', or 'simulate'
    consent_camera: bool = False
    data_dir: Path = field(default_factory=lambda: Path(".stuckwatch_data"))
    vision: VisionConfig = field(default_factory=VisionConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    nudge: NudgeConfig = field(default_factory=NudgeConfig)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["data_dir"] = str(self.data_dir)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StuckwatchConfig:
        data_dir = Path(data.get("data_dir", ".stuckwatch_data"))
        cfg = cls(data_dir=data_dir)
        if "mode" in data:
            cfg.mode = data["mode"]
        if "consent_camera" in data:
            cfg.consent_camera = bool(data["consent_camera"])
        if "vision" in data and isinstance(data["vision"], dict):
            for k, v in data["vision"].items():
                if hasattr(cfg.vision, k):
                    setattr(cfg.vision, k, v)
        if "context" in data and isinstance(data["context"], dict):
            for k, v in data["context"].items():
                if hasattr(cfg.context, k):
                    setattr(cfg.context, k, v)
        if "model" in data and isinstance(data["model"], dict):
            for k, v in data["model"].items():
                if hasattr(cfg.model, k):
                    setattr(cfg.model, k, v)
        if "nudge" in data and isinstance(data["nudge"], dict):
            for k, v in data["nudge"].items():
                if hasattr(cfg.nudge, k):
                    setattr(cfg.nudge, k, v)
        return cfg
