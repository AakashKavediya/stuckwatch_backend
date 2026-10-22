"""Main engine and orchestrator for stuckwatch.

Coordinates:
  - State machine: idle, calibrating, monitoring, paused, stopped
  - Vision loop (~12 FPS when camera active)
  - Context & activity tracking (1 Hz)
  - Explainable estimator and suppression policy
  - IPC command handling and periodic signal streaming (~2s)
"""

from __future__ import annotations

import datetime
import platform
import queue
import signal
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from stuckwatch import __version__
from stuckwatch.config import StuckwatchConfig
from stuckwatch.context.activity import ActivityTracker
from stuckwatch.context.windows import WindowClassifier
from stuckwatch.ipc import IPC
from stuckwatch.interventions import InterventionEngine
from stuckwatch.logging_setup import get_logger
from stuckwatch.model.baseline import BaselineCalibrator, BaselineProfile
from stuckwatch.model.estimator import StuckEstimator
from stuckwatch.policy.nudge import NudgePolicy
from stuckwatch.policy.suppression import SuppressionEngine
from stuckwatch.simulate import SignalSimulator
from stuckwatch.storage import Storage
from stuckwatch.vision.camera import CameraManager
from stuckwatch.vision.features import (
    BlinkDetector,
    GazeStabilityTracker,
    HeadMotionTracker,
    calculate_ear_from_landmarks,
)
from stuckwatch.vision.landmarks import LandmarkDetector

logger = get_logger("engine")


class Engine:
    """Central coordinator for stuckwatch subsystems."""

    def __init__(self, config: StuckwatchConfig) -> None:
        self.config = config

        # State machine: idle, calibrating, monitoring, paused, stopped
        self.state: str = "idle"
        self.paused_until: Optional[float] = None

        # Threading control
        self._stop_event = threading.Event()
        self._main_thread: Optional[threading.Thread] = None
        self._vision_thread: Optional[threading.Thread] = None

        # IPC
        self.ipc = IPC()

        # Storage & Baseline
        self.storage = Storage(self.config.data_dir)
        self.calibrator = BaselineCalibrator(self.storage)
        self.baseline_profile: BaselineProfile = self.calibrator.load_profile()

        # Context trackers
        self.activity_tracker = ActivityTracker(
            rolling_window_seconds=self.config.context.rolling_rate_window_seconds,
            on_error=self._on_subsystem_error,
        )
        self.window_classifier = WindowClassifier()

        # Vision components
        self.camera_manager = CameraManager(
            self.config.vision, on_error=self._on_camera_error
        )
        self.landmark_detector = LandmarkDetector()
        self.blink_detector = BlinkDetector(
            ear_threshold=self.config.vision.ear_blink_threshold,
            min_consecutive_frames=self.config.vision.min_blink_frames,
            window_seconds=self.config.vision.blink_window_seconds,
            min_valid_data_seconds=self.config.vision.blink_min_data_seconds,
        )
        self.gaze_tracker = GazeStabilityTracker(
            window_seconds=self.config.vision.gaze_window_seconds
        )
        self.head_tracker = HeadMotionTracker(
            window_seconds=self.config.vision.head_motion_window_seconds
        )

        # Simulator for testing / demo
        self.simulator = SignalSimulator()

        # Model and Policy
        self.estimator = StuckEstimator(self.config.model)
        self.suppression_engine = SuppressionEngine()
        self.nudge_policy = NudgePolicy(
            self.config.nudge,
            on_sensitivity_change=self._on_sensitivity_changed,
        )
        self.interventions = InterventionEngine(
            sensitivity=self.config.nudge.current_sensitivity,
            sustained_seconds=self.config.nudge.sustained_seconds,
            max_nudges_per_day=3,
            on_adaptation=self._on_intervention_adaptation,
        )

        # Latest cached vision metrics (updated by vision thread, read by context loop)
        self._vision_lock = threading.Lock()
        self._latest_blink_rate: Optional[float] = None
        self._latest_gaze_stability: Optional[float] = None
        self._latest_head_motion: Optional[float] = None
        self._face_detected: bool = False
        self._last_face_seen_time: float = 0.0

        # Cached window state
        self._prev_category: str = "unknown"
        if config.mode == "screen_only":
            self._vision_status = "screen_only_mode"
        elif config.consent_camera:
            self._vision_status = "warming_up"
        else:
            self._vision_status = "no_consent"
        self._vision_started_at: Optional[float] = None
        self._vision_detail = ""
        self._latest_signals: dict[str, Any] = {}

    @property
    def vision_status(self) -> str:
        return self._vision_status

    def _clear_vision(self, status: Optional[str] = None) -> None:
        with self._vision_lock:
            self._latest_blink_rate = None
            self._latest_gaze_stability = None
            self._latest_head_motion = None
            self._face_detected = False
        if status is not None:
            self._vision_status = status
            self._vision_detail = ""

    def start(self, autostart_monitoring: bool = False) -> None:
        """Start background threads, register signal handlers, and enter main loop."""
        logger.info("Initializing stuckwatch engine v%s...", __version__)

        # Setup OS signal handling for graceful shutdown
        def handle_signal(_signum, _frame):
            logger.info("Received termination signal. Shutting down...")
            self.shutdown()

        try:
            signal.signal(signal.SIGINT, handle_signal)
            signal.signal(signal.SIGTERM, handle_signal)
        except (ValueError, AttributeError):
            pass

        # Start IPC command reader thread
        self.ipc.start(on_command=None)

        # Emit initial ready and status events immediately
        self.ipc.emit_ready(
            version=__version__,
            modes=["camera", "screen_only", "hybrid", "simulate"],
            platform_name=platform.platform(),
        )

        # Start input activity monitoring
        self.activity_tracker.start()

        if autostart_monitoring:
            self.state = "monitoring"
            self._ensure_camera_state()

        self._emit_current_status()

        # Start vision thread
        self._vision_thread = threading.Thread(
            target=self._vision_loop, name="stuckwatch-vision", daemon=True
        )
        self._vision_thread.start()

        # Start orchestrator loop (blocks main thread until shutdown)
        self._orchestrator_loop()

    def shutdown(self) -> None:
        """Cleanly release all hardware resources, listeners, and exit."""
        if self._stop_event.is_set():
            return
        logger.info("Shutting down stuckwatch engine...")
        self._stop_event.set()
        self.state = "stopped"

        # Release camera immediately
        self.camera_manager.release()
        self._clear_vision("no_camera")
        self._vision_detail = "Camera released during shutdown."
        self.landmark_detector.close()

        # Stop activity listeners
        self.activity_tracker.stop()

        # Stop IPC
        self.ipc.stop()

        # Close storage
        self.storage.close()

        logger.info("Stuckwatch shutdown complete.")

    def _on_subsystem_error(self, code: str, msg: str) -> None:
        logger.warning("Subsystem error [%s]: %s", code, msg)
        self.ipc.emit_error(code, msg)

    def _on_camera_error(self, code: str, msg: str) -> None:
        logger.warning("Camera error [%s]: %s. Falling back to screen-only vision.", code, msg)
        self.ipc.emit_error(code, msg)
        status = code if code in {
            "no_camera", "camera_busy", "error"
        } else "error"
        self._vision_detail = msg[:160]
        self._clear_vision(status)
        if self.config.mode == "camera":
            self.config.mode = "hybrid"
        self.camera_manager.release()
        self._emit_current_status()

    def _on_sensitivity_changed(self, new_level: str) -> None:
        logger.info("Adaptive sensitivity adjusted to %s", new_level)
        self._emit_current_status()

    def _on_intervention_adaptation(self, message: str) -> None:
        self.ipc.emit_intervention("adaptation", message=message)

    def _emit_current_status(self) -> None:
        self.ipc.emit_status(
            state=self.state,
            mode=self.config.mode,
            camera_active=self.camera_manager.is_active,
            consent=self.config.consent_camera,
            paused_until=self.paused_until,
            sensitivity=self.interventions.sensitivity,
            vision_status=self.vision_status,
            hybrid=self.config.mode == "hybrid",
            vision_detail=self._vision_detail,
        )

    def _ensure_camera_state(self) -> None:
        """Open or release camera according to current state, mode, consent, and pause."""
        should_camera_be_open = (
            self.state in ("monitoring", "calibrating")
            and self.config.mode in ("camera", "hybrid")
            and self.config.consent_camera
            and (self.paused_until is None or time.time() >= self.paused_until)
        )

        if should_camera_be_open:
            if not self.camera_manager.is_active and self.camera_manager.retry_due:
                success = self.camera_manager.open()
                if not success:
                    if self._vision_status not in {"no_camera", "camera_busy", "error"}:
                        self._vision_status = "no_camera"
                        self._vision_detail = "Camera could not be opened."
                else:
                    self._vision_status = "warming_up"
                    self._vision_detail = "Camera opened; collecting enough face data."
                    self._vision_started_at = time.time()
        else:
            if self.camera_manager.is_active:
                self.camera_manager.release()
            if self.config.mode == "screen_only":
                self._clear_vision("screen_only_mode")
                self._vision_detail = "Camera disabled by screen-only mode."
            elif self.paused_until is not None or self.state == "paused":
                self._clear_vision("no_camera")
                self._vision_detail = "Camera released while paused."
            elif not self.config.consent_camera:
                self._clear_vision("no_consent")
                self._vision_detail = "Camera consent has not been granted."

    def _process_commands(self) -> None:
        """Drain command queue from IPC reader."""
        cmd_queue = self.ipc.get_command_queue()
        while not cmd_queue.empty():
            try:
                cmd_dict = cmd_queue.get_nowait()
                self._handle_command(cmd_dict)
            except queue.Empty:
                break
            except Exception as exc:
                logger.error("Error executing command: %s", exc)

    def _handle_command(self, data: dict[str, Any]) -> None:
        cmd = str(data.get("cmd", "")).strip().lower()
        logger.debug("Handling command: %s", cmd)

        if cmd == "hello":
            self.ipc.emit_ready(
                version=__version__,
                modes=["camera", "screen_only", "hybrid", "simulate"],
                platform_name=platform.platform(),
            )
            self._emit_current_status()

        elif cmd == "consent":
            # {"cmd":"consent","camera":true|false}
            camera_consent = bool(data.get("camera", False))
            self.config.consent_camera = camera_consent
            logger.info("Camera consent updated to: %s", camera_consent)
            if not camera_consent:
                self.camera_manager.release()
                self._clear_vision("no_consent")
            self._ensure_camera_state()
            self.ipc.emit_ack("consent", True)
            self._emit_current_status()

        elif cmd == "set_mode":
            # {"cmd":"set_mode","mode":"camera|screen_only|simulate"}
            new_mode = str(data.get("mode", "")).strip().lower()
            if new_mode in ("camera", "screen_only", "hybrid", "simulate"):
                self.config.mode = new_mode
                self._clear_vision(
                    "screen_only_mode" if new_mode == "screen_only"
                    else ("no_consent" if not self.config.consent_camera else "warming_up")
                )
                logger.info("Mode changed to: %s", new_mode)
                self._ensure_camera_state()
                self.ipc.emit_ack("set_mode", True)
                self._emit_current_status()
            else:
                self.ipc.emit_ack("set_mode", False, f"Invalid mode '{new_mode}'")

        elif cmd == "demo_mode":
            enabled = bool(data.get("enabled", False))
            self.interventions.set_demo_mode(enabled)
            self.ipc.emit_ack("demo_mode", True, details={"demo_mode": enabled})
            self._emit_current_status()

        elif cmd == "demo_scenario":
            scenario = str(data.get("scenario", "")).strip().lower()
            if scenario in ("stuck_doc", "watching_video", "busy_typing"):
                self.config.mode = "simulate"
                self.simulator.scenario = scenario
                self.simulator.reset()
                self.state = "monitoring"
                self.paused_until = None
                self.interventions.reset()
                self.ipc.emit_ack("demo_scenario", True, details={"scenario": scenario})
                self._emit_current_status()
            else:
                self.ipc.emit_ack("demo_scenario", False, f"Unknown scenario '{scenario}'")

        elif cmd == "reset_demo":
            self.simulator.reset()
            self.interventions.reset()
            self.paused_until = None
            if self.state == "paused":
                self.state = "monitoring"
            self.ipc.emit_ack("reset_demo", True)
            self._emit_current_status()

        elif cmd == "start":
            if self.state in ("idle", "stopped"):
                self.state = "monitoring"
                self.paused_until = None
                self._ensure_camera_state()
                logger.info("Monitoring started.")
            self.ipc.emit_ack("start", True)
            self._emit_current_status()

        elif cmd == "stop":
            self.state = "idle"
            self.paused_until = None
            self.camera_manager.release()
            self._clear_vision("no_consent" if not self.config.consent_camera else "warming_up")
            self._ensure_camera_state()
            logger.info("Monitoring stopped.")
            self.ipc.emit_ack("stop", True)
            self._emit_current_status()

        elif cmd == "pause":
            # {"cmd":"pause","minutes":N}
            minutes_value = data.get("minutes", 30)
            if minutes_value == "today":
                now_dt = datetime.datetime.now()
                midnight = (now_dt + datetime.timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                self.paused_until = midnight.timestamp()
                minutes = 0.0
            else:
                minutes = float(minutes_value)
            self.paused_until = time.time() + (minutes * 60.0)
            if minutes_value == "today":
                self.paused_until = midnight.timestamp()
            self.state = "paused"
            self.camera_manager.release()
            self._clear_vision("no_camera")
            self._vision_detail = "Camera released while paused."
            self._ensure_camera_state()
            logger.info("Paused for %.1f minutes.", minutes)
            self.ipc.emit_ack("pause", True)
            self._emit_current_status()

        elif cmd == "pause_today":
            # Pause until local midnight
            now_dt = datetime.datetime.now()
            midnight = (now_dt + datetime.timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            self.paused_until = midnight.timestamp()
            self.state = "paused"
            self.camera_manager.release()
            self._clear_vision("no_camera")
            self._vision_detail = "Camera released while paused."
            self._ensure_camera_state()
            logger.info("Paused until local midnight.")
            self.ipc.emit_ack("pause_today", True)
            self._emit_current_status()

        elif cmd == "resume":
            self.paused_until = None
            if self.state == "paused":
                self.state = "monitoring"
            self._ensure_camera_state()
            logger.info("Monitoring resumed.")
            self.ipc.emit_ack("resume", True)
            self._emit_current_status()

        elif cmd == "calibrate_start":
            seconds = int(data.get("seconds", 180))
            self.state = "calibrating"
            self._ensure_camera_state()
            self.calibrator.start(seconds)
            self.ipc.emit_ack("calibrate_start", True)
            self._emit_current_status()

        elif cmd == "calibrate_cancel":
            self.calibrator.cancel()
            self.state = "monitoring"
            self._ensure_camera_state()
            self.ipc.emit_ack("calibrate_cancel", True)
            self._emit_current_status()

        elif cmd == "feedback":
            # {"cmd":"feedback","nudge_id":"...","choice":"tiny_start|rest|not_now|dismissed|change"}
            nudge_id = str(data.get("nudge_id", ""))
            choice = str(data.get("choice", "")).strip().lower()
            if choice in ("tiny_start", "rest", "not_now", "dismissed", "change"):
                self.storage.record_feedback(choice)
                ok, sens_changed = self.nudge_policy.handle_feedback(nudge_id, choice)
                self.ipc.emit_ack("feedback", ok)
                if sens_changed:
                    self._emit_current_status()
            else:
                self.ipc.emit_ack("feedback", False, f"Unknown choice '{choice}'")

        elif cmd == "nudge_response":
            nudge_id = str(data.get("id", ""))
            choice = str(data.get("choice", "")).strip().lower()
            if choice in ("tiny_step", "rest", "not_now", "i_am_fine", "change"):
                result = self.interventions.respond(nudge_id, choice)
                if result:
                    self.ipc.emit_intervention(result.pop("type"), **result)
                self.storage.record_feedback(choice)
                self.ipc.emit_ack("nudge_response", True)
            else:
                self.ipc.emit_ack("nudge_response", False, f"Unknown choice '{choice}'")

        elif cmd == "set_sensitivity":
            level = str(data.get("value", data.get("level", ""))).strip().lower()
            if level in ("low", "medium", "high"):
                self.nudge_policy.set_sensitivity(level)
                self.interventions.set_sensitivity(level)
                self.ipc.emit_ack("set_sensitivity", True)
                self._emit_current_status()
            else:
                self.ipc.emit_ack("set_sensitivity", False, f"Invalid sensitivity '{level}'")

        elif cmd == "delete_data":
            # Wipe all stored local data
            self.storage.delete_data()
            self.baseline_profile = self.calibrator.load_profile()
            self.interventions = InterventionEngine(
                sensitivity=self.config.nudge.current_sensitivity,
                on_adaptation=self._on_intervention_adaptation,
            )
            logger.info("Local data deleted per user command.")
            self.ipc.emit_ack("delete_data", True)

        elif cmd == "get_status":
            self.ipc.emit_ack("get_status", True)
            self._emit_current_status()

        elif cmd == "get_seen":
            with self._vision_lock:
                signals = {
                    "blink_rate_per_min": self._latest_blink_rate,
                    "gaze_stability": self._latest_gaze_stability,
                    "head_motion": self._latest_head_motion,
                    "face_detected": self._face_detected,
                }
            signals.update(self._latest_signals)
            self.ipc.emit_intervention(
                "seen",
                signals=signals,
                nudges_today=self.interventions.nudges_today,
            )

        elif cmd == "shutdown":
            self.ipc.emit_ack("shutdown", True)
            self.shutdown()

        elif cmd == "vision_test":
            self._run_vision_test()

        else:
            self.ipc.emit_ack(cmd, False, f"Unrecognized command '{cmd}'")

    def _run_vision_test(self) -> None:
        if not self.config.consent_camera:
            self.ipc.emit_ack("vision_test", False, "Camera consent is required")
            return
        if self.config.mode == "screen_only":
            self.ipc.emit_ack("vision_test", False, "Vision is disabled in screen_only mode")
            return

        was_active = self.camera_manager.is_active
        if not was_active and not self.camera_manager.open():
            self.ipc.emit_ack("vision_test", False, "Camera unavailable")
            return

        started = time.time()
        frames = 0
        faces = 0
        while time.time() - started < 5.0 and not self._stop_event.is_set():
            frame = self.camera_manager.read_frame()
            if frame is None:
                continue
            frames += 1
            landmarks = self.landmark_detector.process_frame(frame)
            if landmarks is not None:
                faces += 1

        elapsed = max(0.001, time.time() - started)
        if not was_active:
            self.camera_manager.release()
            self._clear_vision("warming_up")
        self.ipc.emit_ack(
            "vision_test",
            True,
            details={
                "face_detected_ratio": round(faces / max(1, frames), 3),
                "fps": round(frames / elapsed, 2),
            },
        )

    def _vision_loop(self) -> None:
        """Background thread grabbing camera frames and updating face features."""
        logger.debug("Vision loop started.")
        while not self._stop_event.is_set():
            if not self.camera_manager.is_active:
                time.sleep(0.1)
                continue

            try:
                frame = self.camera_manager.read_frame()
                if frame is None:
                    continue

                # Landmarks detection (frame is deleted inside process_frame)
                landmarks = self.landmark_detector.process_frame(frame)
                now = time.time()

                if landmarks is not None:
                    pts = landmarks.points
                    ear = calculate_ear_from_landmarks(pts)
                    b_rate = self.blink_detector.update(ear, timestamp=now)
                    g_stab = self.gaze_tracker.update(pts, timestamp=now)
                    h_motion = self.head_tracker.update(pts, timestamp=now)

                    with self._vision_lock:
                        self._latest_blink_rate = b_rate
                        self._latest_gaze_stability = g_stab
                        self._latest_head_motion = h_motion
                        self._face_detected = True
                        self._last_face_seen_time = now

                    # If calibrating, feed sample
                    if self.calibrator.is_active:
                        self.calibrator.add_sample({
                            "blink_rate": b_rate,
                            "gaze_stability": g_stab,
                            "head_motion": h_motion,
                        })
                else:
                    self.blink_detector.update(None, timestamp=now)
                    with self._vision_lock:
                        self._latest_blink_rate = None
                        self._latest_gaze_stability = None
                        self._latest_head_motion = None
                        self._face_detected = False
                    self._vision_status = "no_face"

            except Exception as exc:
                logger.debug("Error in vision loop step: %s", exc)
                time.sleep(0.05)

        logger.debug("Vision loop exited.")

    def _orchestrator_loop(self) -> None:
        """Main loop: polls context, processes commands, and streams signals."""
        last_signal_time = 0.0
        last_calib_check_time = 0.0

        while not self._stop_event.is_set():
            loop_start = time.time()

            # 1. Process pending stdin commands
            self._process_commands()

            # Check pause expiry
            if self.paused_until is not None and time.time() >= self.paused_until:
                self.paused_until = None
                if self.state == "paused":
                    self.state = "monitoring"
                self._ensure_camera_state()
                self._emit_current_status()

            # Keep a consented hybrid/camera stream alive and retry unavailable
            # devices at the camera manager's 30-second retry interval.
            self._ensure_camera_state()

            # 2. Gather context metrics
            idle_sec, typing_rate, pointer_act, app_switches = (
                self.activity_tracker.get_metrics()
            )
            app_category, is_fullscreen = self.window_classifier.get_active_window_info()

            if app_category != self._prev_category and self._prev_category != "unknown":
                self.activity_tracker.record_app_switch()
            self._prev_category = app_category

            # Read cached vision metrics
            with self._vision_lock:
                blink_rate = self._latest_blink_rate
                gaze_stab = self._latest_gaze_stability
                head_motion = self._latest_head_motion
                face_detected = self._face_detected

            if self.config.mode == "screen_only":
                self._vision_status = "screen_only_mode"
            elif self.paused_until is not None or self.state == "paused":
                self._vision_status = "no_camera"
                self._vision_detail = "Camera released while paused."
            elif not self.config.consent_camera:
                self._vision_status = "no_consent"
            elif (
                self.camera_manager.is_active
                and not face_detected
                and self._vision_status != "warming_up"
            ):
                self._vision_status = "no_face"
            elif self.camera_manager.is_active and any(
                value is not None for value in (blink_rate, gaze_stab, head_motion)
            ):
                self._vision_status = "active"

            # 3. If in simulate mode, override with synthetic signals
            is_simulated = self.config.mode == "simulate"
            if is_simulated:
                syn = self.simulator.get_signals()
                idle_sec = syn.idle_seconds
                typing_rate = syn.typing_rate
                app_switches = syn.app_switches_per_min
                app_category = syn.app_category
                blink_rate = syn.blink_rate
                gaze_stab = syn.gaze_stability
                head_motion = syn.head_motion
                face_detected = syn.face_detected
                is_fullscreen = syn.is_fullscreen
                self._vision_status = "active"
                self._vision_detail = "Synthetic demo signals; no camera frames are used."

            # 4. Handle Calibration Mode
            if self.state == "calibrating":
                now = time.time()
                if (now - last_calib_check_time) >= 1.0:
                    last_calib_check_time = now
                    # Feed context samples
                    self.calibrator.add_sample({
                        "typing_rate": typing_rate,
                        "app_switches": app_switches,
                    })
                    pct = self.calibrator.get_progress_percent()
                    self.ipc.emit_calibration_progress(pct)

                    if self.calibrator.is_complete():
                        ok, samples, profile = self.calibrator.finalize()
                        self.baseline_profile = profile
                        self.ipc.emit_calibration_done(ok, samples)
                        self.state = "monitoring"
                        self._emit_current_status()

            # 5. Model Estimation
            stuck_likelihood, confidence, reasons = self.estimator.estimate(
                idle_seconds=idle_sec,
                typing_rate=typing_rate,
                app_switches_per_min=app_switches,
                app_category=app_category,
                blink_rate=blink_rate,
                gaze_stability=gaze_stab,
                head_motion=head_motion,
                baseline=self.baseline_profile,
                mode=self.config.mode,
                face_detected=face_detected,
            )
            if self.interventions.demo_mode and is_simulated:
                if self.simulator.scenario == "stuck_doc":
                    stuck_likelihood = min(0.95, 0.20 + (idle_sec / 5.0) * 0.75)
                    reasons = [
                        "the document has been still with no recent typing",
                        "gaze appears unusually steady",
                    ]
                elif self.simulator.scenario == "watching_video":
                    stuck_likelihood = min(0.72, 0.25 + (idle_sec / 20.0) * 0.45)
                    reasons = ["the screen has been still while gaze stays on it"]
            self._latest_signals = {
                "idle_seconds": idle_sec,
                "typing_rate": typing_rate,
                "app_switches_per_min": app_switches,
                "app_category": app_category,
                "stuck_likelihood": stuck_likelihood,
            }

            # 6. Suppression Evaluation
            in_cooldown = self.nudge_policy.is_in_cooldown()
            rate_limited = self.nudge_policy.is_rate_limited()
            is_suppressed, supp_reason = self.suppression_engine.is_suppressed(
                app_category=app_category,
                is_fullscreen=is_fullscreen,
                paused_until=self.paused_until,
                mode=self.config.mode,
                consent_camera=self.config.consent_camera,
                idle_seconds=idle_sec,
                face_detected=face_detected,
                away_threshold_seconds=self.config.context.idle_away_threshold_seconds,
                in_cooldown=in_cooldown,
                confidence=confidence,
                min_confidence=self.config.model.min_confidence_threshold,
                rate_limited=rate_limited,
            )

            # 7. Intervention evaluation (only active monitoring, never paused).
            if self.state == "monitoring" or (is_simulated and self.state != "paused"):
                nudge_payload = self.interventions.evaluate(
                    score=stuck_likelihood,
                    app_category=app_category,
                    fullscreen=is_fullscreen,
                    paused=(
                        self.state == "paused"
                        or self.paused_until is not None
                        or is_suppressed
                    ),
                    reasons=reasons,
                )
                if nudge_payload is not None:
                    self.ipc.emit_nudge(
                        nudge_id=nudge_payload["id"],
                        message=nudge_payload["message"],
                        options=nudge_payload["options"],
                        reasons=nudge_payload["reasons"],
                        silent=nudge_payload["silent"],
                    )

            if self.interventions.rest_finished():
                self.ipc.emit_intervention("rest_finished", message="Ready when you are")

            # 8. Emit signals event periodically (~2.0 seconds), only while active
            now = time.time()
            if self.state in ("monitoring", "calibrating") and (now - last_signal_time) >= 2.0:
                last_signal_time = now
                vision_available = self._vision_status == "active"
                warmup_left = None
                if self._vision_status == "warming_up":
                    warmup_left = max(
                        0,
                        int(20 - (now - (self._vision_started_at or now))),
                    )
                self.ipc.emit_signals(
                    blink_rate_per_min=blink_rate,
                    gaze_stability=gaze_stab,
                    head_motion=head_motion,
                    idle_seconds=idle_sec,
                    app_switches_per_min=app_switches,
                    typing_rate=typing_rate,
                    app_category=app_category,
                    stuck_likelihood=stuck_likelihood,
                    confidence=confidence,
                    reasons=reasons,
                    vision_available=vision_available,
                    vision_status=self._vision_status,
                    face_detected=face_detected if vision_available or self._vision_status == "no_face" else None,
                    vision_warmup_seconds_left=warmup_left,
                    vision_detail=self._vision_detail,
                )

            # Throttle main loop to ~1.0s interval
            elapsed = time.time() - loop_start
            sleep_time = max(0.05, self.config.context.poll_interval_seconds - elapsed)
            time.sleep(sleep_time)
