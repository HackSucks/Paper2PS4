"""ffmpeg / ffprobe wrappers (shared with video extraction).

A small self-contained subset of PS4-Ultimate-Theme-Creator/app/media.py so
paper2ps4 can work without importing the whole web app.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

#: Keeps Windows from opening a console window on every ffmpeg call.
NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


class MediaError(RuntimeError):
    """ffmpeg/ffprobe failure with the useful part of stderr extracted."""


def require_tools() -> None:
    if not FFMPEG or not FFPROBE:
        raise MediaError("ffmpeg/ffprobe are not on the PATH.")


@dataclass(frozen=True)
class ProbeResult:
    duration: float
    width: int
    height: int
    frame_count: int | None
    codec: str
    is_animated: bool


def _run(cmd: list[str], *, capture_stdout: bool = False) -> bytes:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError("\n".join(tail[-6:]) or f"{cmd[0]} exited with code {proc.returncode}")
    return proc.stdout or b""


def probe(path: Path) -> ProbeResult:
    """Duration and geometry of a media file."""
    require_tools()
    out = _run(
        [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream=width,height,duration,nb_frames,avg_frame_rate,codec_name:format=duration",
            "-of", "json", str(path),
        ],
        capture_stdout=True,
    )
    data = json.loads(out or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("The file has no video stream (check that it is not corrupt).")
    st = streams[0]

    def as_float(value) -> float:
        try:
            f = float(value)
            return f if f > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0

    duration = as_float(data.get("format", {}).get("duration")) or as_float(st.get("duration"))

    nb_frames = None
    try:
        nb_frames = int(st["nb_frames"])
    except (KeyError, TypeError, ValueError):
        pass

    if not duration and nb_frames:
        rate = st.get("avg_frame_rate") or "0/0"
        try:
            num, den = (int(x) for x in rate.split("/"))
            if den and num:
                duration = nb_frames * den / num
        except ValueError:
            pass

    if not duration:
        counted = _run(
            [
                FFPROBE, "-v", "error", "-select_streams", "v:0",
                "-count_packets", "-show_entries", "stream=nb_read_packets,avg_frame_rate",
                "-of", "json", str(path),
            ],
            capture_stdout=True,
        )
        cst = (json.loads(counted or b"{}").get("streams") or [{}])[0]
        nb_frames = nb_frames or int(cst.get("nb_read_packets") or 0) or None
        try:
            num, den = (int(x) for x in (cst.get("avg_frame_rate") or "0/1").split("/"))
            if nb_frames and den and num:
                duration = nb_frames * den / num
        except ValueError:
            pass

    if not duration:
        raise MediaError("Could not determine the file duration.")

    return ProbeResult(
        duration=duration,
        width=int(st.get("width") or 0),
        height=int(st.get("height") or 0),
        frame_count=nb_frames,
        codec=str(st.get("codec_name") or "?"),
        is_animated=bool(nb_frames is None or nb_frames > 1),
    )


def scale_crop_filter(width: int, height: int) -> str:
    """Fill WxH exactly without deforming: scale up and crop to center."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1"
    )


def extraction_fps(frame_count: int, duration: float) -> float:
    """fps needed to extract `frame_count` samples spread across `duration`."""
    if frame_count < 1:
        raise ValueError("frame_count must be >= 1")
    if duration <= 0:
        raise ValueError("duration must be > 0")
    return frame_count / duration


def image_to_rgba(src: Path, size: tuple[int, int]) -> np.ndarray:
    """Image -> RGBA array (H, W, 4) scaled and center-cropped."""
    require_tools()
    width, height = size
    raw = _run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-i", str(src),
            "-vf", scale_crop_filter(width, height),
            "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-",
        ],
        capture_stdout=True,
    )
    expected = width * height * 4
    if len(raw) < expected:
        raise MediaError("ffmpeg returned an incomplete frame.")
    return np.frombuffer(raw[:expected], dtype=np.uint8).reshape(height, width, 4)


def to_wav(src: Path, dest: Path, rate: int, max_seconds: float | None = None,
           gain_db: float = 0.0, loudnorm_lufs: float | None = None) -> Path:
    """Any audio -> WAV PCM 16-bit stereo (what ps4_at9tool eats).

    `loudnorm_lufs` levels the result to that integrated loudness, which is
    what keeps a loud song from drowning out the console's own sounds.
    """
    require_tools()
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]
    if max_seconds:
        args += ["-t", f"{max_seconds:g}"]
    filters = []
    if loudnorm_lufs is not None:
        filters.append(f"loudnorm=I={loudnorm_lufs:g}:TP=-2:LRA=11")
    if abs(gain_db) > 0.01:
        filtro = f"volume={gain_db:+.2f}dB"
        if gain_db > 0:
            filtro += ",alimiter=limit=0.98"
        filters.append(filtro)
    if filters:
        args += ["-af", ",".join(filters)]
    args += ["-c:a", "pcm_s16le", "-ac", "2", "-ar", str(rate), str(dest)]
    _run(args)
    if not dest.is_file():
        raise MediaError("ffmpeg produced no intermediate WAV.")
    return dest


def pcm_mono(src: Path, rate: int, max_seconds: float | None = None,
             gain_db: float = 0.0) -> np.ndarray:
    """Decode any audio to PCM mono 16-bit at the requested rate.

    The system effects are PS-ADPCM, which is mono, so the audio is mixed
    down here. The gain is applied before the compression: raising it after
    would only add quantisation noise.
    """
    require_tools()
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if max_seconds:
        args += ["-t", f"{max_seconds:g}"]
    if abs(gain_db) > 0.01:
        filtro = f"volume={gain_db:+.2f}dB"
        if gain_db > 0:
            filtro += ",alimiter=limit=0.98"
        args += ["-af", filtro]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(rate), "-"]
    raw = _run(args, capture_stdout=True)
    if not raw:
        raise MediaError("ffmpeg extracted no audio from the file.")
    return np.frombuffer(raw, dtype="<i2")


def render_png(src: Path, dest: Path, size: tuple[int, int]) -> Path:
    """Scale and crop an image to exactly WxH and write it as a PNG.

    The icons carry transparency, so the output keeps its alpha channel.
    """
    require_tools()
    dest.parent.mkdir(parents=True, exist_ok=True)
    width, height = size
    args = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vf", scale_crop_filter(width, height),
        "-frames:v", "1",
        "-c:v", "png", "-pix_fmt", "rgba",
        str(dest),
    ]
    _run(args)
    if not dest.exists():
        raise MediaError("ffmpeg produced no output image.")
    return dest


def flatten_alpha(rgba: np.ndarray, background=(0, 0, 0)) -> np.ndarray:
    """Composite over an opaque background and set alpha to 255.

    PS4 backgrounds don't use transparency; leaving alpha loose can make some
    encoders switch to DXT5 (double the size, breaks the 16 MiB budget).
    Flattening here keeps it deterministic.
    """
    if rgba.shape[2] != 4:
        return rgba
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    bg = np.array(background, dtype=np.float32)
    rgb = rgba[..., :3].astype(np.float32) * alpha + bg * (1.0 - alpha)
    out = np.empty_like(rgba)
    out[..., :3] = np.rint(rgb).astype(np.uint8)
    out[..., 3] = 255
    return out
