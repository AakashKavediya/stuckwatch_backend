#!/usr/bin/env python3
"""Create and provision the supported stuckwatch Python environment."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
SUPPORTED_VERSIONS = ("3.10", "3.11", "3.12")
DOWNLOAD_MESSAGE = (
    "Install Python 3.10 (recommended) from https://www.python.org/downloads/ "
    "or on Windows run: winget install Python.Python.3.10"
)


def version_of(command: list[str]) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            command + ["-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip().splitlines()[-1:]
    if not value or value[0] not in SUPPORTED_VERSIONS:
        return None
    major, minor = value[0].split(".")
    return int(major), int(minor)


def find_supported_python() -> tuple[list[str], tuple[int, int]] | None:
    current = sys.version_info[:2]
    if current in {(3, 10), (3, 11), (3, 12)}:
        return [sys.executable], current

    candidates: list[str] = []
    if sys.platform == "win32":
        for version in ("3.10", "3.11", "3.12"):
            candidates.append(["py", f"-{version}"])
        try:
            output = subprocess.run(
                ["py", "-0p"], check=False, capture_output=True, text=True
            ).stdout
            for line in output.splitlines():
                path = line.strip().split()[-1] if line.strip() else ""
                if path:
                    candidates.append([path])
        except OSError:
            pass
    else:
        candidates.extend([["python3.10"], ["python3.11"], ["python3.12"]])

    for candidate in candidates:
        executable = candidate[0]
        if len(candidate) == 1 and not (Path(executable).exists() or shutil.which(executable)):
            continue
        found_version = version_of(candidate)
        if found_version is not None:
            return candidate, found_version
    return None


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run_command(command: list[str], description: str) -> None:
    print(f"[stuckwatch setup] {description}...", file=sys.stderr, flush=True)
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        code = exc.returncode if isinstance(exc, subprocess.CalledProcessError) else 1
        print(
            f"[stuckwatch setup] ERROR: {description} failed (exit code {code}).",
            file=sys.stderr,
        )
        raise SystemExit(code)


def installed_version(python: Path, package: str) -> str | None:
    result = subprocess.run(
        [str(python), "-c", f"import importlib.metadata as m; print(m.version('{package}'))"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def verify_pins(python: Path) -> None:
    numpy_version = installed_version(python, "numpy")
    mediapipe_version = installed_version(python, "mediapipe")
    if numpy_version is None or tuple(map(int, numpy_version.split(".")[:2])) >= (2, 0):
        print("[stuckwatch setup] Reinstalling pinned NumPy (<2)...", file=sys.stderr)
        run_command([str(python), "-m", "pip", "install", "--force-reinstall", "numpy<2"],
                    "Reinstalling NumPy")
    if mediapipe_version != "0.10.14":
        print("[stuckwatch setup] Reinstalling MediaPipe 0.10.14...", file=sys.stderr)
        run_command(
            [str(python), "-m", "pip", "install", "--force-reinstall", "mediapipe==0.10.14"],
            "Reinstalling MediaPipe",
        )


def verify_imports(python: Path) -> None:
    checks = [
        ([str(python), "-c", "import mediapipe as mp; print(mp.solutions.face_mesh)"],
         "MediaPipe legacy mp.solutions API"),
        ([str(python), "-c", "import cv2, numpy; print(cv2.__version__, numpy.__version__)"],
         "OpenCV and NumPy imports"),
    ]
    for command, description in checks:
        run_command(command, f"Verifying {description}")


def main() -> None:
    selected = find_supported_python()
    if selected is None:
        print(
            "[stuckwatch setup] ERROR: No supported Python interpreter found.\n"
            "[stuckwatch setup] Supported versions: Python 3.10, 3.11, or 3.12.\n"
            f"[stuckwatch setup] {DOWNLOAD_MESSAGE}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    interpreter, chosen_version = selected

    python = venv_python()
    if python.exists():
        existing = version_of([str(python)])
        if existing not in {(3, 10), (3, 11), (3, 12)}:
            existing_label = (
                f"{existing[0]}.{existing[1]}" if existing is not None else "unavailable"
            )
            print(
                f"[stuckwatch setup] Existing .venv uses unsupported Python "
                f"{existing_label}; recreating.",
                file=sys.stderr,
            )
            shutil.rmtree(VENV_DIR)

    if not python.exists():
        run_command(
            interpreter + ["-m", "venv", str(VENV_DIR)],
            f"Creating .venv with Python {chosen_version[0]}.{chosen_version[1]}",
        )

    print(f"[stuckwatch setup] Chosen Python: {chosen_version[0]}.{chosen_version[1]}", file=sys.stderr)
    run_command([str(python), "-m", "pip", "install", "--upgrade", "pip"], "Upgrading pip")
    run_command(
        [str(python), "-m", "pip", "install", "numpy<2", "mediapipe==0.10.14"],
        "Installing pinned vision dependencies",
    )
    run_command(
        [str(python), "-m", "pip", "install", "-r", "requirements.txt"],
        "Installing requirements.txt",
    )
    verify_pins(python)
    verify_imports(python)
    print(str(python))


if __name__ == "__main__":
    main()
