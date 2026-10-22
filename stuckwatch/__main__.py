"""Entry point for running stuckwatch backend directly or via python -m stuckwatch."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from stuckwatch import __version__
from stuckwatch.config import StuckwatchConfig
from stuckwatch.engine import Engine
from stuckwatch.logging_setup import get_logger, setup_logging

logger = get_logger("cli")


def run_self_test(camera_index: int = 0) -> int:
    """Run environment and dependency checks and emit a single JSON result line."""
    results: dict = {
        "self_test": True,
        "version": __version__,
        "platform": platform.platform(),
        "python_version": sys.version.split()[0],
        "dependencies": {},
        "camera_available": False,
        "ok": True,
    }

    # Check dependencies
    deps = ["cv2", "mediapipe", "numpy", "sklearn", "pynput", "psutil"]
    for dep in deps:
        try:
            __import__(dep)
            results["dependencies"][dep] = True
        except ImportError:
            results["dependencies"][dep] = False
            # cv2 and mediapipe are optional without vision, but required for camera
            if dep not in ("cv2", "mediapipe"):
                results["ok"] = False

    # Check camera availability safely without saving anything
    try:
        import cv2

        if sys.platform == "win32":
            cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(camera_index)
        else:
            cap = cv2.VideoCapture(camera_index)

        if cap.isOpened():
            results["camera_available"] = True
            cap.release()
    except Exception:
        results["camera_available"] = False

    # Emit JSON result to stdout
    sys.stdout.write(json.dumps(results, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if results["ok"] else 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="stuckwatch",
        description="stuckwatch: local-only proxy indicator companion for task paralysis",
    )
    parser.add_argument(
        "--mode",
        choices=["camera", "screen_only", "hybrid", "simulate"],
        default="hybrid",
        help="Initial operational mode (default: hybrid)",
    )
    parser.add_argument(
        "--camera-index",
        type=int,
        default=0,
        help="Hardware camera device index (default: 0)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".stuckwatch_data"),
        help="Directory to store SQLite database (default: .stuckwatch_data)",
    )
    parser.add_argument(
        "--sensitivity",
        choices=["low", "medium", "high"],
        default="medium",
        help="Nudge sensitivity threshold (default: medium)",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        default=True,
        help="Wait for explicit 'start' command from frontend (default: True)",
    )
    parser.add_argument(
        "--autostart",
        dest="no_autostart",
        action="store_false",
        help="Start monitoring loop immediately upon launch",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable verbose debug logging to stderr",
    )
    parser.add_argument(
        "--camera-consent",
        action="store_true",
        help="Pre-grant camera consent for local development and testing only",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        default=False,
        help="Run quick dependency and camera self-test, print JSON report, and exit",
    )
    parser.add_argument(
        "--scenario",
        choices=["cycle", "stuck_doc", "watching_video", "busy_typing"],
        default="cycle",
        help="Synthetic scenario used with --mode simulate",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    setup_logging(debug=args.debug)

    if args.self_test:
        sys.exit(run_self_test(camera_index=args.camera_index))

    config = StuckwatchConfig(
        mode=args.mode,
        data_dir=args.data_dir,
    )
    config.vision.camera_index = args.camera_index
    config.nudge.current_sensitivity = args.sensitivity
    config.consent_camera = args.camera_consent
    if args.camera_consent:
        logger.warning("Camera consent pre-granted via CLI flag (dev/testing)")

    engine = Engine(config)
    engine.simulator.scenario = args.scenario
    try:
        engine.start(autostart_monitoring=not args.no_autostart)
    except KeyboardInterrupt:
        engine.shutdown()
        sys.exit(0)
    except Exception as exc:
        logger.error("Fatal exception in engine: %s", exc, exc_info=True)
        engine.shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
