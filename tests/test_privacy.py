"""Static analysis privacy tests for stuckwatch codebase.

Enforces non-negotiable privacy constraints:
  - NEVER write, save, or encode image/video frames (no cv2.imwrite, no VideoWriter).
  - NEVER make outbound network calls (no requests, no urllib.request, no socket transmission).
"""

from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = PROJECT_ROOT / "stuckwatch"


def test_no_frame_persistence_or_recording():
    """Verify that cv2.imwrite and cv2.VideoWriter are never invoked anywhere in stuckwatch."""
    forbidden_terms = ["imwrite", "VideoWriter"]

    for py_file in SOURCE_DIR.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in forbidden_terms:
                    raise AssertionError(
                        f"Privacy violation: Forbidden frame persistence method '{node.attr}' "
                        f"found in {py_file} at line {node.lineno}"
                    )
            elif isinstance(node, ast.Name):
                if node.id in forbidden_terms:
                    raise AssertionError(
                        f"Privacy violation: Forbidden term '{node.id}' found in {py_file} at line {node.lineno}"
                    )


def test_no_outbound_network_libraries():
    """Verify that network libraries (requests, urllib.request, etc.) are never imported."""
    forbidden_modules = [
        "requests",
        "urllib.request",
        "urllib3",
        "aiohttp",
        "httpx",
        "http.client",
        "ftplib",
        "smtplib",
    ]

    for py_file in SOURCE_DIR.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_modules:
                        if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                            raise AssertionError(
                                f"Privacy violation: Outbound network module '{alias.name}' "
                                f"imported in {py_file} at line {node.lineno}"
                            )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for forbidden in forbidden_modules:
                        if node.module == forbidden or node.module.startswith(forbidden + "."):
                            raise AssertionError(
                                f"Privacy violation: Outbound network module '{node.module}' "
                                f"imported in {py_file} at line {node.lineno}"
                            )
