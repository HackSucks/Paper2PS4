"""Tool resolution and launching.

The PS4 packaging / conversion helpers are Windows .exe binaries. On Linux they
run through wine; on Windows they launch directly. This module resolves them
from a list of candidate directories (default: /mnt/windows2 and friends) and
builds the right subprocess command for the current platform.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from . import constants


def _windows_tool_cmd(exe_path: Path) -> list[str]:
    """Build the command to launch a Windows exe."""
    if sys.platform == "win32":
        return [str(exe_path)]
    wine = shutil.which("wine")
    if wine:
        return [wine, str(exe_path)]
    return [str(exe_path)]


def find_tool(names: tuple[str, ...], search_dirs: tuple[Path, ...] | None = None) -> Path | None:
    """Find the first existing tool matching any of `names`.

    Searches, in order:
      1. each dir in `search_dirs` (default: constants.TOOL_SEARCH_DIRS)
      2. PATH (for wine-native tools like ffmpeg)
    """
    dirs = search_dirs or constants.TOOL_SEARCH_DIRS
    for directory in dirs:
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    for name in names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def run_tool(exe_path: Path, args: list[str], timeout: int = 600,
             capture: bool = True) -> subprocess.CompletedProcess:
    """Launch a Windows tool (through wine on Linux) and return the process."""
    cmd = _windows_tool_cmd(exe_path) + args
    return subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout)
