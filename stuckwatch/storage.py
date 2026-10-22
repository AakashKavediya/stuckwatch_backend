"""SQLite storage backend for stuckwatch.

Stores ONLY minimal operational metadata:
  - settings (key, value)
  - baseline (per-feature mean, std, updated_at)
  - feedback_counts (aggregate counts per choice per day)

NEVER stores video frames, window titles, keystrokes, coordinates, or audio.
Provides a delete_data() method to completely wipe all stored local data.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

from stuckwatch.logging_setup import get_logger

logger = get_logger("storage")


class Storage:
    """Thread-safe SQLite storage wrapper."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is None:
            env_dir = os.environ.get("STUCKWATCH_DATA_DIR")
            self.data_dir = Path(env_dir) if env_dir else Path(".stuckwatch_data")
        else:
            self.data_dir = Path(data_dir)

        self.db_path = self.data_dir / "stuckwatch.db"
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                timeout=10.0,
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS baseline (
                        feature TEXT PRIMARY KEY,
                        mean REAL NOT NULL,
                        std REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS feedback_counts (
                        choice TEXT NOT NULL,
                        count INTEGER NOT NULL DEFAULT 0,
                        day TEXT NOT NULL,
                        PRIMARY KEY (choice, day)
                    )
                    """
                )
        logger.debug("Database initialized at %s", self.db_path)

    # ----------------- Settings API -----------------
    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    # ----------------- Baseline API -----------------
    def save_baseline_feature(self, feature: str, mean: float, std: float) -> None:
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO baseline (feature, mean, std, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (feature, float(mean), float(std), time.time()),
                )

    def get_baseline_feature(self, feature: str) -> Optional[tuple[float, float, float]]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute(
                "SELECT mean, std, updated_at FROM baseline WHERE feature = ?", (feature,)
            )
            row = cur.fetchone()
            if row:
                return (float(row["mean"]), float(row["std"]), float(row["updated_at"]))
            return None

    def get_all_baselines(self) -> Dict[str, tuple[float, float, float]]:
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute("SELECT feature, mean, std, updated_at FROM baseline")
            return {
                row["feature"]: (
                    float(row["mean"]),
                    float(row["std"]),
                    float(row["updated_at"]),
                )
                for row in cur.fetchall()
            }

    def clear_baseline(self) -> None:
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM baseline")

    # ----------------- Feedback API -----------------
    def record_feedback(self, choice: str) -> None:
        today_str = date.today().isoformat()
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO feedback_counts (choice, count, day)
                    VALUES (?, 1, ?)
                    ON CONFLICT(choice, day) DO UPDATE SET count = count + 1
                    """,
                    (choice, today_str),
                )

    def get_feedback_counts_for_day(self, day_str: Optional[str] = None) -> Dict[str, int]:
        if day_str is None:
            day_str = date.today().isoformat()
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute(
                "SELECT choice, count FROM feedback_counts WHERE day = ?", (day_str,)
            )
            return {row["choice"]: int(row["count"]) for row in cur.fetchall()}

    def get_nudge_count_for_day(self, day_str: Optional[str] = None) -> int:
        return sum(self.get_feedback_counts_for_day(day_str).values())

    # ----------------- Privacy Data Wipe -----------------
    def delete_data(self) -> None:
        """Immediately and irrevocably delete all stored local data."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

            if self.data_dir.exists():
                shutil.rmtree(self.data_dir, ignore_errors=True)
                logger.info("Local data directory wiped: %s", self.data_dir)

        # Re-initialize clean empty schema
        self._init_db()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
