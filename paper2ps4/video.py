"""Video -> frame extraction via ffmpeg.

Extracts a fixed number of frames from a video file, scaled and center-cropped
to a target resolution, ready to be compressed to DXT1a for the RAF scene.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .media import require_tools, FFMPEG, scale_crop_filter, extraction_fps


def extract_frames(src: Path, dest_dir: Path, count: int, size: tuple[int, int],
                   duration: float) -> list[Path]:
    """Extract `count` frames from `src` as raw RGBA arrays written to PNGs.

    Returns the list of written frame paths (padded by repeating the last frame
    if ffmpeg emits fewer than requested).
    """
    require_tools()
    dest_dir.mkdir(parents=True, exist_ok=True)
    for stale in dest_dir.glob("*.png"):
        stale.unlink()

    width, height = size
    fps = extraction_fps(count, duration)
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vf", f"fps={fps:.9f},{scale_crop_filter(width, height)}",
        "-frames:v", str(count),
        "-fps_mode", "passthrough",
        "-f", "image2", "-pix_fmt", "rgba",
        str(dest_dir / "%02d.png"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = result.stderr.strip().splitlines()[-6:]
        raise RuntimeError("ffmpeg failed: " + ("\n".join(tail) or "no output"))

    frames = sorted(dest_dir.glob("*.png"))
    if not frames:
        raise RuntimeError("ffmpeg produced no frames. The file may be corrupt.")

    # Pad by repeating the last frame so index.xml matches the files on disk.
    while len(frames) < count:
        clone = dest_dir / f"{len(frames):02d}.png"
        clone.write_bytes(frames[-1].read_bytes())
        frames.append(clone)

    return frames[:count]


def probe_video(src: Path):
    """Return (duration, width, height) for a video file."""
    from .media import probe
    info = probe(src)
    return info.duration, info.width, info.height
