"""PKGV / PKGM container unpacking.

Implements the documented Wallpaper Engine container layout: a length-prefixed
magic string, an entry table (name, offset, size), followed by the raw entry
data. Entries are stored uncompressed. Adapted from we_mobile_convert.py.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class PkgFormatError(Exception):
    """Raised when a .pkg/.mpkg file doesn't match the expected container layout."""


@dataclass
class PkgEntry:
    name: str
    offset: int
    size: int


def _read_u32(f) -> int:
    return struct.unpack("<I", f.read(4))[0]


def unpack_pkg(pkg_path: Path, out_dir: Path) -> str:
    """Extract a .pkg/.mpkg container into out_dir. Returns the magic string."""
    out_dir.mkdir(parents=True, exist_ok=True)

    with pkg_path.open("rb") as f:
        header_len = _read_u32(f)
        magic = f.read(header_len).decode("utf-8", errors="replace")
        if not (magic.startswith("PKGV") or magic.startswith("PKGM")):
            raise PkgFormatError(
                f"unrecognized container magic {magic!r} in {pkg_path} "
                "(expected a PKGV.../PKGM... header)"
            )

        entry_count = _read_u32(f)
        entries: list[PkgEntry] = []
        for _ in range(entry_count):
            name_len = _read_u32(f)
            name = f.read(name_len).decode("utf-8", errors="replace")
            offset = _read_u32(f)
            size = _read_u32(f)
            entries.append(PkgEntry(name, offset, size))

        data_start = f.tell()

        for entry in entries:
            dest = (out_dir / entry.name).resolve()
            out_dir_resolved = out_dir.resolve()
            try:
                dest.relative_to(out_dir_resolved)
            except ValueError:
                raise PkgFormatError(
                    f"entry {entry.name!r} escapes the output directory"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            f.seek(data_start + entry.offset)
            dest.write_bytes(f.read(entry.size))

    return magic


def pack_pkg(src_dir: Path, pkg_path: Path, magic: str = "PKGV0013") -> Path:
    """Pack a directory back into a PKGV/PKGM container.

    Entries are stored uncompressed, which is what the format allows. This
    exists to round-trip a package (and to build test fixtures); the theme
    pipeline itself never needs to write one.
    """
    files = sorted(p for p in src_dir.rglob("*") if p.is_file())

    entries: list[tuple[str, int, int]] = []
    offset = 0
    for path in files:
        rel = path.relative_to(src_dir).as_posix()
        size = path.stat().st_size
        entries.append((rel, offset, size))
        offset += size

    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    magic_bytes = magic.encode("utf-8")
    with pkg_path.open("wb") as f:
        f.write(struct.pack("<I", len(magic_bytes)))
        f.write(magic_bytes)
        f.write(struct.pack("<I", len(entries)))
        for rel, off, size in entries:
            name_bytes = rel.encode("utf-8")
            f.write(struct.pack("<I", len(name_bytes)))
            f.write(name_bytes)
            f.write(struct.pack("<I", off))
            f.write(struct.pack("<I", size))
        for path in files:
            f.write(path.read_bytes())

    return pkg_path
