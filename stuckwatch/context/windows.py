"""Active window inspection and coarse category classification.

CRITICAL PRIVACY GUARANTEE:
  - Raw window titles and window handles are NEVER saved, logged, persisted, or emitted.
  - Text is examined in a local scope solely to map to one of 7 coarse categories:
      video, call, editor, browser, game, other, unknown
  - Once classified, the raw string is immediately discarded.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Optional, Tuple

from stuckwatch.logging_setup import get_logger

logger = get_logger("context.windows")

# Keywords for coarse categorization (matching against lowercase title & process name)
CATEGORY_KEYWORDS = {
    "video": [
        "youtube", "netflix", "vlc", "mpv", "prime video", "hotstar", "disney",
        "hulu", "twitch", "plex", "potplayer", "media player", "spotify video",
        "video player", "crunchyroll", "hbo max", "peacock", "paramount+",
    ],
    "call": [
        "zoom", "teams", "meet", "discord call", "webex", "skype", "slack call",
        "facetime", "google meet", "chime", "ringcentral", "bluejeans", "signal call",
    ],
    "editor": [
        "code", "vscode", "visual studio", "pycharm", "intellij", "idea",
        "vim", "neovim", "emacs", "sublime", "atom", "word", "docs", "notepad",
        "obsidian", "notion", "latex", "overleaf", "powerpoint", "writer",
        "excel", "sheets", "typora", "cursor", "zed", "nano", "kate", "gedit",
    ],
    "browser": [
        "chrome", "firefox", "edge", "safari", "brave", "opera", "vivaldi",
        "arc", "chromium", "tor browser",
    ],
    "game": [
        "steam", "epic games", "riot client", "valorant", "league of legends",
        "minecraft", "overwatch", "counter-strike", "dota", "fortnite",
        "unity", "unreal", "blizzard", "battle.net", "ea desktop", "genshin",
    ],
}


def classify_text(text: str) -> str:
    """Classify window text / process identifier into coarse category."""
    if not text:
        return "unknown"

    lower = text.lower()

    # Check categories in priority order (calls and video first for suppression safety)
    for cat in ["call", "video", "editor", "game", "browser"]:
        for kw in CATEGORY_KEYWORDS[cat]:
            if kw in lower:
                return cat

    return "other"


class WindowClassifier:
    """Detects active foreground application category and fullscreen status."""

    def __init__(self) -> None:
        self._last_category: str = "unknown"

    def get_active_window_info(self) -> Tuple[str, bool]:
        """Fetch coarse foreground category and fullscreen flag.

        Returns:
            (category, is_fullscreen): category is one of
            'video', 'call', 'editor', 'browser', 'game', 'other', 'unknown'.
        """
        category = "unknown"
        is_fullscreen = False

        try:
            if sys.platform == "win32":
                category, is_fullscreen = self._get_windows_info()
            elif sys.platform == "darwin":
                category, is_fullscreen = self._get_macos_info()
            else:
                category, is_fullscreen = self._get_linux_info()
        except Exception as exc:
            logger.debug("Failed active window lookup: %s", exc)
            category = "unknown"
            is_fullscreen = False

        self._last_category = category
        return category, is_fullscreen

    def _get_windows_info(self) -> Tuple[str, bool]:
        try:
            import win32gui
            import win32process
            import win32api
            import psutil

            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return "unknown", False

            raw_title = win32gui.GetWindowText(hwnd) or ""
            proc_name = ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid > 0:
                    proc_name = psutil.Process(pid).name()
            except Exception:
                pass

            # Classify using combined title + process name
            combined = f"{proc_name} {raw_title}".strip()
            category = classify_text(combined)

            # Explicitly discard raw strings
            del raw_title
            del combined

            # Fullscreen detection
            is_fullscreen = False
            try:
                rect = win32gui.GetWindowRect(hwnd)
                # Compare window dimensions to primary monitor resolution
                screen_w = win32api.GetSystemMetrics(0)
                screen_h = win32api.GetSystemMetrics(1)
                win_w = rect[2] - rect[0]
                win_h = rect[3] - rect[1]
                if win_w >= screen_w and win_h >= screen_h:
                    is_fullscreen = True
            except Exception:
                pass

            return category, is_fullscreen

        except ImportError:
            # Fallback if pywin32 not present
            return "unknown", False

    def _get_macos_info(self) -> Tuple[str, bool]:
        try:
            from AppKit import NSWorkspace
            active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if active_app:
                name = str(active_app.localizedName() or "")
                cat = classify_text(name)
                del name
                return cat, False
        except Exception:
            pass

        # AppleScript fallback
        if shutil.which("osascript"):
            try:
                script = 'tell application "System Events" to get name of first process whose frontmost is true'
                res = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if res.returncode == 0:
                    name = res.stdout.strip()
                    cat = classify_text(name)
                    del name
                    return cat, False
            except Exception:
                pass

        return "unknown", False

    def _get_linux_info(self) -> Tuple[str, bool]:
        # On Linux X11, xdotool / xprop
        if shutil.which("xdotool"):
            try:
                res = subprocess.run(
                    ["xdotool", "getactivewindow", "getwindowname"],
                    capture_output=True,
                    text=True,
                    timeout=1.0,
                )
                if res.returncode == 0:
                    title = res.stdout.strip()
                    cat = classify_text(title)
                    del title
                    return cat, False
            except Exception:
                pass

        return "unknown", False
