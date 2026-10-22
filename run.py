#!/usr/bin/env python3
"""Runner script for stuckwatch.

Standard library only. Ensures .venv exists (triggering setup_env.py if missing),
then launches 'python -m stuckwatch' using the .venv interpreter, forwarding
all CLI arguments and preserving stdin/stdout/stderr unbuffered for IPC with Tauri.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"


def get_venv_python() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def main() -> None:
    venv_python = get_venv_python()

    if not venv_python.exists():
        sys.stderr.write(
            "[stuckwatch runner] Setting up... .venv not found; "
            "running setup_env.py first.\n"
        )
        setup_script = PROJECT_ROOT / "setup_env.py"
        try:
            res = subprocess.run([sys.executable, str(setup_script)], check=True)
        except Exception as exc:
            sys.stderr.write(f"[stuckwatch runner] ERROR: setup_env.py failed: {exc}\n")
            sys.exit(1)

    if not venv_python.exists():
        sys.stderr.write(f"[stuckwatch runner] ERROR: Venv interpreter still missing at {venv_python}\n")
        sys.exit(1)

    # Ensure unbuffered I/O for clean real-time newline-delimited JSON IPC
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    cmd = [str(venv_python), "-m", "stuckwatch"] + sys.argv[1:]
    try:
        # Run with inherited stdio so stdin/stdout/stderr pipe directly to the caller (e.g. Tauri)
        result = subprocess.run(cmd, env=env)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        sys.stderr.write(f"[stuckwatch runner] Fatal error launching process: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
