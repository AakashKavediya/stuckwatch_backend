"""Logging configuration for stuckwatch.

All backend logging is directed strictly to sys.stderr so that sys.stdout remains
purely dedicated to JSON-lines IPC protocol events for Tauri or child-process runners.
"""

from __future__ import annotations

import logging
import sys


def setup_logging(debug: bool = False) -> None:
    """Configure root logger to write exclusively to stderr."""
    root_logger = logging.getLogger()
    # Remove any existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    level = logging.DEBUG if debug else logging.INFO
    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        fmt="[stuckwatch %(levelname)s %(asctime)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handler.setFormatter(formatter)
    handler.setLevel(level)
    root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger scoped to the stuckwatch package."""
    return logging.getLogger(f"stuckwatch.{name}")
