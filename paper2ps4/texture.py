"""Wallpaper Engine texture handling.

Textures come as .tex files (see tex.py for the format). They are decoded
natively here: no external tool needed, so this works the same on Linux and
Windows.

Three shapes of texture matter for a theme:
  * a plain image  -> the static background
  * a GIF texture -> an animation (frames cropped out of a sprite sheet)
  * a video texture -> an MP4 stream embedded in the .tex
"""

from __future__ import annotations

from pathlib import Path

from . import tex as tex_mod
from .tex import TexError


def is_tex(path: Path) -> bool:
    """Cheap check: does this file start with the .tex container magic?"""
    try:
        with path.open("rb") as fh:
            return fh.read(8) == b"TEXV0005"
    except OSError:
        return False


def describe(path: Path) -> str:
    """One-line summary of a .tex, for the log."""
    try:
        t = tex_mod.parse_tex(path)
    except TexError as exc:
        return f"{path.name}: unreadable ({exc})"
    kind = "gif" if t.is_gif else ("video" if t.is_video else "image")
    return (f"{path.name}: {kind}, {t.image_width}x{t.image_height}, "
            f"{len(t.images)} image(s)")


def decode_to_png(path: Path, out_png: Path) -> Path:
    """Decode a .tex into a PNG (the base mipmap of the first image)."""
    t = tex_mod.parse_tex(path)
    if t.is_gif:
        frames = tex_mod.decode_gif_frames(t)
        if not frames:
            raise TexError(f"{path.name}: GIF texture has no usable frames")
        return tex_mod.write_png(out_png, frames[0])
    if t.is_video:
        raise TexError(f"{path.name}: video texture, not an image")
    return tex_mod.tex_to_png(t, out_png)


def decode_gif_frames(path: Path) -> list:
    """Decode a GIF texture into its animation frames (RGBA arrays)."""
    t = tex_mod.parse_tex(path)
    return tex_mod.decode_gif_frames(t)


def decode_frames_as_pngs(path: Path, out_dir: Path, limit: int | None = None) -> list[Path]:
    """Decode a GIF texture into a sequence of PNG files."""
    frames = decode_gif_frames(path)
    if limit is not None:
        frames = frames[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, frame in enumerate(frames):
        dest = out_dir / f"{index:02d}.png"
        tex_mod.write_png(dest, frame)
        written.append(dest)
    return written


def extract_video(path: Path, out_mp4: Path) -> Path:
    """Extract the MP4 stream embedded in a video texture."""
    t = tex_mod.parse_tex(path)
    payload = tex_mod.extract_video(t, t.images[0])
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    out_mp4.write_bytes(payload)
    return out_mp4
