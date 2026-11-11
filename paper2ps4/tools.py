"""Tool resolution and launching.

The PS4 packaging helpers are Windows .exe binaries. On Linux they run
through wine; on Windows they launch directly.

Where they are looked for, in order:

  1. the directories given with `--tools` (or the PAPER2PS4_TOOLS
     environment variable)
  2. PATH

Nothing is hardcoded: a machine with the tools in /opt/ps4-tools, in a
Windows drive or anywhere else just needs the folder pointed at, or the
folder added to PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

#: Extra folders to look in, set by --tools or PAPER2PS4_TOOLS.
_extra_dirs: list[Path] = []

#: The environment variable that adds folders to the search.
TOOLS_ENV_VAR = "PAPER2PS4_TOOLS"


def set_tool_dirs(dirs) -> None:
    """Set the extra folders to search for the tools.

    Replaces whatever was set before. The folders given on the command line
    come first, then the ones in the environment variable, so an explicit
    choice always wins over the machine's default.
    """
    global _extra_dirs
    _extra_dirs = []

    for directory in dirs or ():
        directory = Path(directory)
        if directory not in _extra_dirs:
            _extra_dirs.append(directory)

    from_env = os.environ.get(TOOLS_ENV_VAR, "")
    for part in from_env.split(os.pathsep):
        part = part.strip()
        if part:
            directory = Path(part)
            if directory not in _extra_dirs:
                _extra_dirs.append(directory)


def tool_dirs() -> list[Path]:
    """The extra folders currently being searched."""
    return list(_extra_dirs)


def _windows_tool_cmd(exe_path: Path) -> list[str]:
    """Build the command to launch a Windows exe."""
    if sys.platform == "win32":
        return [str(exe_path)]
    wine = shutil.which("wine")
    if wine:
        return [wine, str(exe_path)]
    return [str(exe_path)]


def find_tool(names: tuple[str, ...], search_dirs=None) -> Path | None:
    """Find the first existing tool matching any of `names`.

    Searches the folders given with `--tools` first, then PATH.
    """
    dirs = list(search_dirs or []) + tool_dirs()
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


def describe_search(names: tuple[str, ...]) -> str:
    """A message that says where the tool was looked for."""
    dirs = tool_dirs()
    where = ", ".join(str(d) for d in dirs) if dirs else "(no --tools given)"
    return (f"looked for {', '.join(names)} in {where} and in PATH")
