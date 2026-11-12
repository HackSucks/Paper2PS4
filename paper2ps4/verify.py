"""Verification helpers for a built .fpkg.

A PS4 package is not a ZIP, so checking what went in means reading it as the
binary it is. Two things matter for a theme:

  * param.sfo -- the descriptor that tells the console this is a theme
                 (volume_type=pkg_ps4_theme + IRO_TAG=2 + CATEGORY=ac).
  * the file list -- what ended up inside.

The .sfo is parsed here in pure Python. The file list comes from
orbis-pub-cmd's own `img_file_list`, which is the tool that wrote the package
and therefore the authority on its layout.
"""

from __future__ import annotations

import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import constants
from . import tools

#: param.sfo magic: b"\x00PSF"
SFO_MAGIC = b"\x00PSF"

#: Size of one index-table entry.
SFO_ENTRY_SIZE = 16

#: The `fmt` codes the PS4 uses.
SFO_FMT_INT32 = 0x0404
SFO_FMT_INT64 = 0x040C
SFO_FMT_UTF8 = 0x0402
SFO_FMT_UTF8_SPECIAL = 0x0204
SFO_FMT_BINARY = 0x0004


class VerifyError(Exception):
    """The package could not be read."""


@dataclass
class SfoEntry:
    key: str
    fmt: int
    value: str
    max_len: int


@dataclass
class Sfo:
    entries: list[SfoEntry]

    def get(self, key: str) -> str | None:
        for entry in self.entries:
            if entry.key == key:
                return entry.value
        return None

    def as_dict(self) -> dict[str, str]:
        return {e.key: e.value for e in self.entries}


def _read_cstring(data: bytes, offset: int) -> str:
    end = data.find(b"\x00", offset)
    if end == -1:
        end = len(data)
    return data[offset:end].decode("utf-8", "replace")


def parse_sfo(data: bytes) -> Sfo:
    """Parse a PS4 param.sfo.

    Layout:
        0x00  magic "\\0PSF"
        0x04  version
        0x08  key table offset
        0x0C  data table offset
        0x10  entry count
        0x14  index table: one 16-byte entry per parameter
                  u16 key offset (from the key table)
                  u16 format
                  u32 length
                  u32 max length
                  u32 data offset (from the data table)
    """
    if len(data) < 20 or data[:4] != SFO_MAGIC:
        raise VerifyError("not a param.sfo (bad magic)")

    _version, key_table, data_table, count = struct.unpack_from("<IIII", data, 4)

    entries: list[SfoEntry] = []
    for index in range(count):
        base = 20 + index * SFO_ENTRY_SIZE
        if base + SFO_ENTRY_SIZE > len(data):
            raise VerifyError("param.sfo index table is truncated")
        key_off, fmt, length, max_len, data_off = struct.unpack_from(
            "<HHIII", data, base)

        key = _read_cstring(data, key_table + key_off)
        value_off = data_table + data_off

        if fmt == SFO_FMT_INT32:
            value = str(struct.unpack_from("<i", data, value_off)[0])
        elif fmt == SFO_FMT_INT64:
            value = str(struct.unpack_from("<q", data, value_off)[0])
        elif fmt == SFO_FMT_BINARY:
            value = data[value_off:value_off + length].hex()
        else:
            raw = data[value_off:value_off + length]
            value = raw.split(b"\x00")[0].decode("utf-8", "replace")

        entries.append(SfoEntry(key, fmt, value, max_len))

    return Sfo(entries)


def find_param_sfo(pkg_path: Path) -> bytes:
    """Locate the param.sfo embedded in a built package.

    The .sfo is stored verbatim, so it is found by scanning for its magic and
    then validating that the tables it points at are inside the file.
    """
    data = Path(pkg_path).read_bytes()
    offset = 0
    while True:
        index = data.find(SFO_MAGIC, offset)
        if index == -1:
            raise VerifyError("no param.sfo found inside the package")
        try:
            sfo = parse_sfo(data[index:index + 4096])
            if sfo.entries and sfo.get("TITLE_ID"):
                return data[index:index + _sfo_length(data, index)]
        except (VerifyError, struct.error):
            pass
        offset = index + 4


def _sfo_length(data: bytes, offset: int) -> int:
    """Total size of the .sfo starting at `offset`."""
    _version, key_table, data_table, count = struct.unpack_from(
        "<IIII", data, offset + 4)
    end = offset + 20 + count * SFO_ENTRY_SIZE
    for i in range(count):
        base = offset + 20 + i * SFO_ENTRY_SIZE
        key_off, fmt, length, max_len, data_off = struct.unpack_from(
            "<HHIII", data, base)
        end = max(end,
                  offset + key_table + key_off + 32,
                  offset + data_table + data_off + length)
    return end - offset


def list_package_entries(pkg_path: Path) -> list[str]:
    """List the files inside a built package.

    Uses orbis-pub-cmd's `img_file_list`: it is the tool that wrote the
    package, so it knows the container layout better than a reimplementation
    would.
    """
    exe = tools.find_tool(constants.ORBIS_PUB_CMD_NAMES)
    if exe is None:
        raise VerifyError("orbis-pub-cmd.exe not found; cannot list the package")

    cmd = tools._windows_tool_cmd(exe) + [
        "img_file_list",
        "--passcode", constants.PKG_DEFAULTS["passcode"],
        str(pkg_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = proc.stdout or ""
    if proc.returncode != 0 and not out.strip():
        raise VerifyError(f"img_file_list failed: {out.strip()[:200]}")

    entries: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(("Image0/", "Sc0/")):
            entries.append(line)
    return entries


def run_img_verify(pkg_path: Path) -> tuple[bool, list[str]]:
    """Run orbis-pub-cmd's own img_verify on the finished package.

    This is the authority on whether the console will accept the package: it
    knows the parameter area sizes, the PNG colour depths and the AT9 limits
    that the lighter checks here only approximate.
    """
    exe = tools.find_tool(constants.ORBIS_PUB_CMD_NAMES)
    if exe is None:
        raise VerifyError("orbis-pub-cmd.exe not found; cannot run img_verify")

    cmd = tools._windows_tool_cmd(exe) + [
        "img_verify",
        "--passcode", constants.PKG_DEFAULTS["passcode"],
        str(pkg_path),
    ]
    import os

    env = os.environ.copy()
    env["WINEDEBUG"] = "-all"
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    out = proc.stdout or ""

    errors: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("[Error]"):
            continue
        message = line[len("[Error]"):].strip()
        if message.startswith(("Number of Error", "Result :", "Result:")):
            continue
        if "Process finished" in message:
            continue
        errors.append(message)
    return not errors, errors


def verify_theme_package(pkg_path: Path,
                         deep: bool = False) -> tuple[bool, list[str], list[str]]:
    """Check that a package is a PS4 *theme* and list its contents.

    Returns (ok, problems, contents). The rules that matter, all measured on
    a real console:
      * param.sfo must declare IRO_TAG = 2 (without it the package installs
        as an app with a "Start" button and throws CE-37695-0)
      * CATEGORY must be 'ac' ('gd' shows up in the Themes list but crashes
        with CE-36329-3)
      * theme.xml must be at the root of Image0
      * a theme has no sce_sys/pic1.png (the checker rejects it)
      * the .at9 music must be 144 kbps, or the console stays silent

    `deep` also runs orbis-pub-cmd's img_verify, which is slower but checks
    the details (parameter sizes, PNG bit depths, AT9 limits) exactly.
    """
    pkg_path = Path(pkg_path)
    problems: list[str] = []

    try:
        contents = list_package_entries(pkg_path)
    except VerifyError as exc:
        contents = []
        problems.append(str(exc))

    try:
        sfo = parse_sfo(find_param_sfo(pkg_path))
    except (VerifyError, struct.error) as exc:
        problems.append(f"param.sfo unreadable: {exc}")
        return False, problems, contents

    iro = sfo.get("IRO_TAG")
    if iro != "2":
        problems.append(f"IRO_TAG is {iro!r}, expected '2' "
                        "(the package would install as an app)")
    category = sfo.get("CATEGORY")
    if category != "ac":
        problems.append(f"CATEGORY is {category!r}, expected 'ac' "
                        "(the console crashes when applying the theme)")
    if sfo.get("APP_VER") is not None:
        problems.append("APP_VER is present: that is an application, not a theme")
    if not sfo.get("PROVIDER"):
        problems.append("param.sfo has no PROVIDER")

    if "Image0/theme.xml" not in contents:
        problems.append("Image0/theme.xml is missing")
    if "Sc0/param.sfo" not in contents:
        problems.append("Sc0/param.sfo is missing")
    if "Sc0/icon0.png" not in contents:
        problems.append("Sc0/icon0.png is missing")
    if "Sc0/pic1.png" in contents:
        problems.append("Sc0/pic1.png must not exist in a theme package")

    if deep:
        try:
            _ok, errors = run_img_verify(pkg_path)
            problems.extend(errors)
        except (VerifyError, subprocess.SubprocessError) as exc:
            problems.append(f"img_verify could not run: {exc}")

    return not problems, problems, contents
