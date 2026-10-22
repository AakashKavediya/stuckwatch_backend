"""IPC communication protocol for stuckwatch.

Implements newline-delimited JSON streaming over stdin/stdout.
  - sys.stdout: ONLY protocol events. Every line is flushed immediately.
  - sys.stdin: Commands from frontend/Tauri read by a dedicated background thread.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from stuckwatch.logging_setup import get_logger

logger = get_logger("ipc")

PROTOCOL_VERSION = 1


@dataclass
class ProtocolEvent:
    """Base event payload sent from backend to frontend."""
    type: str
    v: int = PROTOCOL_VERSION
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IPC:
    """Thread-safe IPC manager for streaming events and receiving commands."""

    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        self._command_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_command_callback: Optional[Callable[[dict[str, Any]], None]] = None

    def start(self, on_command: Optional[Callable[[dict[str, Any]], None]] = None) -> None:
        """Start the background stdin reader thread."""
        self._on_command_callback = on_command
        self._stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="stuckwatch-ipc-reader", daemon=True
        )
        self._reader_thread.start()
        logger.debug("IPC reader thread started.")

    def stop(self) -> None:
        """Signal reader thread to stop."""
        self._stop_event.set()

    def get_command_queue(self) -> queue.Queue[dict[str, Any]]:
        return self._command_queue

    def emit(self, event_data: dict[str, Any] | ProtocolEvent) -> None:
        """Serialize event as a single JSON line to stdout and flush."""
        if isinstance(event_data, ProtocolEvent):
            payload = event_data.to_dict()
        else:
            payload = dict(event_data)

        if "v" not in payload:
            payload["v"] = PROTOCOL_VERSION
        if "ts" not in payload:
            payload["ts"] = round(time.time(), 3)

        try:
            line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            with self._write_lock:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
        except Exception as exc:
            logger.error("Failed to emit IPC event: %s", exc)

    def emit_ready(self, version: str, modes: list[str], platform_name: str) -> None:
        self.emit({
            "type": "ready",
            "version": version,
            "modes": modes,
            "platform": platform_name,
        })

    def emit_status(
        self,
        state: str,
        mode: str,
        camera_active: bool,
        consent: bool,
        paused_until: Optional[float],
        sensitivity: str = "medium",
        vision_status: str = "no_consent",
        hybrid: bool = False,
        vision_detail: str = "",
    ) -> None:
        self.emit({
            "type": "status",
            "state": state,
            "mode": mode,
            "camera_active": camera_active,
            "consent": consent,
            "paused_until": paused_until,
            "sensitivity": sensitivity,
            "vision_status": vision_status,
            "hybrid": hybrid,
            "vision_detail": vision_detail,
        })

    def emit_signals(
        self,
        blink_rate_per_min: Optional[float],
        gaze_stability: Optional[float],
        head_motion: Optional[float],
        idle_seconds: float,
        app_switches_per_min: float,
        typing_rate: float,
        app_category: str,
        stuck_likelihood: float,
        confidence: float,
        reasons: list[str],
        vision_available: bool = False,
        vision_status: str = "screen_only_mode",
        face_detected: Optional[bool] = None,
        vision_warmup_seconds_left: Optional[int] = None,
        vision_detail: str = "",
    ) -> None:
        self.emit({
            "type": "signals",
            "blink_rate_per_min": round(blink_rate_per_min, 2) if blink_rate_per_min is not None else None,
            "gaze_stability": round(gaze_stability, 3) if gaze_stability is not None else None,
            "head_motion": round(head_motion, 4) if head_motion is not None else None,
            "idle_seconds": round(idle_seconds, 1),
            "app_switches_per_min": round(app_switches_per_min, 2),
            "typing_rate": round(typing_rate, 1),
            "app_category": app_category,
            "stuck_likelihood": round(stuck_likelihood, 3),
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "vision_available": vision_available,
            "vision_status": vision_status,
            "face_detected": face_detected,
            "vision_warmup_seconds_left": vision_warmup_seconds_left,
            "vision_detail": vision_detail,
        })

    def emit_nudge(
        self,
        nudge_id: str,
        message: str,
        options: list[str],
        reasons: list[str],
        silent: bool = True,
    ) -> None:
        self.emit({
            "type": "nudge",
            "id": nudge_id,
            "message": message,
            "options": options,
            "reasons": reasons,
            "silent": silent,
        })

    def emit_intervention(self, event_type: str, **fields: Any) -> None:
        payload = {"type": event_type}
        payload.update(fields)
        self.emit(payload)

    def emit_calibration_progress(self, percent: int) -> None:
        self.emit({
            "type": "calibration_progress",
            "percent": int(percent),
        })

    def emit_calibration_done(self, ok: bool, samples: int) -> None:
        self.emit({
            "type": "calibration_done",
            "ok": ok,
            "samples": int(samples),
        })

    def emit_ack(
        self,
        cmd: str,
        ok: bool,
        error: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": "ack",
            "cmd": cmd,
            "ok": ok,
            "error": error,
        }
        if details:
            payload.update(details)
        self.emit(payload)

    def emit_error(self, code: str, message: str) -> None:
        self.emit({
            "type": "error",
            "code": code,
            "message": message,
        })

    def _reader_loop(self) -> None:
        """Read lines from sys.stdin and put parsed JSON objects in command queue."""
        logger.debug("Entering stdin reader loop...")
        while not self._stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    # EOF reached - parent process died or closed stdin
                    logger.info("stdin reached EOF. Enqueueing shutdown command.")
                    self._command_queue.put({"cmd": "shutdown", "_source": "stdin_eof"})
                    break

                stripped = line.strip()
                if not stripped:
                    continue

                try:
                    data = json.loads(stripped)
                except Exception as json_err:
                    logger.warning("Malformed JSON received: %s", json_err)
                    self.emit_ack("unknown", False, f"Malformed JSON: {json_err}")
                    continue

                if not isinstance(data, dict) or "cmd" not in data:
                    logger.warning("Invalid command payload (missing 'cmd'): %s", data)
                    self.emit_ack(str(data.get("cmd", "unknown")), False, "Missing 'cmd' property")
                    continue

                self._command_queue.put(data)
                if self._on_command_callback:
                    try:
                        self._on_command_callback(data)
                    except Exception as cb_err:
                        logger.error("Error in on_command callback: %s", cb_err)

            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.error("Exception in IPC reader loop: %s", exc)
                break

        logger.debug("IPC reader loop exited.")
