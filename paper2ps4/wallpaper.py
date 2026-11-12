"""Headless scene rendering with linux-wallpaperengine.

A PS4 theme cannot run Wallpaper Engine's shaders. The only way to keep a
scene that *does* animate (puppetry, particles, rain, pulsing lights, custom
shaders) is to render it and ship the result as a frame sequence, which the
console plays back from the RAF scene.

linux-wallpaperengine renders the real thing. It needs a display, so we use:
  - Wayland + headless monitor (GPU-accelerated, correct aspect via --scaling fill)
    when running under a Wayland compositor that supports it (Hyprland, sway, KWin).
  - Xvfb + ffmpeg x11grab as a fallback (software rendering, slower).

The engine is looked for in this order:

  1. the folder given with ``--wpe`` (or the ``PAPER2PS4_WPE`` env var)
  2. ``vendor/linux-wallpaperengine`` next to the package (the bundled copy)
  3. PATH

The bundled copy may keep its assets compressed (``assets.tar.xz``); they are
unpacked once, on first use. Rendering is Linux-only: on Windows the caller
falls back to the static renderer (or to a scene that is a plain video).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .media import FFMPEG, require_tools

#: The environment variable that points at a linux-wallpaperengine folder.
WPE_ENV_VAR = "PAPER2PS4_WPE"

#: Folder shipped with this repository.
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "linux-wallpaperengine"

#: Assets may be shipped compressed; these are unpacked on first use.
ASSET_ARCHIVES = ("assets.tar.xz", "assets.tar.gz", "assets.tar.bz2", "assets.zip")

#: Seconds to let the scene run before grabbing frames. Particles and
#: transitions start from their initial state; a short warm-up avoids
#: capturing an empty first frame.
WARMUP_SECONDS = 1.0

#: How long to wait for the engine to report it has a rendered frame.
READY_TIMEOUT = 120.0

#: The engine's internal render rate (frames it tries to produce per second).
ENGINE_FPS = 60

#: How often to grab the window when using Xvfb fallback.
CAPTURE_FPS = 12

#: The grab is done in short batches: a fast scene finishes after the first
#: one, a slow scene keeps going until enough distinct frames exist.
CAPTURE_CHUNK_SECONDS = 2.0

#: The longest the frame capture may take. Software rendering of a heavy
#: scene can be slow; this is the point where the sequence is good enough.
MAX_CAPTURE_SECONDS = 20.0

_engine_dir: Path | None = None


class WallpaperError(RuntimeError):
    """The scene could not be rendered."""


@dataclass(frozen=True)
class Engine:
    executable: Path
    root: Path
    assets: Path


# ---------------------------------------------------------------------------
# Finding the engine
# ---------------------------------------------------------------------------

def set_engine_dir(directory: Path | None) -> None:
    """Point at the folder holding the engine (from --wpe)."""
    global _engine_dir
    _engine_dir = Path(directory) if directory is not None else None


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    if _engine_dir is not None:
        dirs.append(_engine_dir)
    env = os.environ.get(WPE_ENV_VAR, "").strip()
    if env:
        dirs.append(Path(env))
    dirs.append(VENDOR_DIR)

    found = shutil.which("linux-wallpaperengine")
    if found:
        dirs.append(Path(found).resolve().parent)

    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def _assets_beside(root: Path) -> Path | None:
    """Find (or unpack) the engine's assets folder for `root`."""
    for name in ("assets",):
        candidate = root / name
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate

    for archive_name in ASSET_ARCHIVES:
        archive = root / archive_name
        if not archive.is_file():
            continue
        target = root / "assets"
        try:
            _extract_assets(archive, target)
        except OSError:
            # A read-only install (system-wide) still has to work.
            target = Path.home() / ".cache" / "paper2ps4" / "wpe-assets"
            if not (target.is_dir() and any(target.iterdir())):
                _extract_assets(archive, target)
        return target
    return None


def _extract_assets(archive: Path, target: Path) -> None:
    print(f"    [wpe] unpacking {archive.name} (one time) ...")
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target.parent)
    else:
        import tarfile

        with tarfile.open(archive) as bundle:
            try:
                bundle.extractall(target.parent, filter="data")
            except TypeError:
                # Python < 3.11.4 has no extraction filter.
                bundle.extractall(target.parent)
    # The archives hold a top-level "assets/" folder; make sure the target
    # exists whichever layout was used.
    if not target.is_dir():
        raise OSError(f"{archive.name} did not contain an assets folder")


def find_engine() -> Engine | None:
    """Locate the engine, its assets and its shared library."""
    if sys.platform != "linux":
        return None

    names = ("linux-wallpaperengine",)
    for root in _candidate_dirs():
        executable = next(
            (root / name for name in names if (root / name).is_file()),
            None,
        )
        if executable is None:
            continue
        assets = _assets_beside(root)
        if assets is None:
            continue
        return Engine(executable=executable, root=root, assets=assets)

    found = shutil.which("linux-wallpaperengine")
    if found:
        path = Path(found).resolve()
        roots = [
            path.parent,
            path.parent.parent,
            path.parent.parent / "share" / "linux-wallpaperengine",
            path.parent.parent / "lib" / "linux-wallpaperengine",
        ]
        for root in roots:
            assets = _assets_beside(root)
            if assets is not None:
                return Engine(executable=path, root=root, assets=assets)
    return None


def unavailable_reason() -> str | None:
    """Why scene rendering cannot be used, or None when it can."""
    if sys.platform != "linux":
        return ("linux-wallpaperengine only runs on Linux; on Windows the "
                "scene is rendered as a still image")
    if shutil.which("Xvfb") is None:
        return "Xvfb is not installed (apt install xvfb)"
    if not FFMPEG:
        return "ffmpeg is not on the PATH"
    if find_engine() is None:
        return ("linux-wallpaperengine was not found; put it in "
                f"vendor/linux-wallpaperengine, install it on PATH, or pass "
                f"--wpe DIR / set {WPE_ENV_VAR}")
    return None


def available() -> bool:
    return unavailable_reason() is None


# ---------------------------------------------------------------------------
# Does the scene animate?
# ---------------------------------------------------------------------------

def _value_of(value, default=True):
    if isinstance(value, dict):
        return _value_of(value.get("value"), default)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return bool(value)


def scene_animation_reasons(src_dir: Path) -> list[str]:
    """What makes the scene move, if anything.

    Wallpaper Engine keeps the animation in three places: effects on the
    scene's objects (shake, sway, pulse), particles, and puppet animation
    layers in the model files. Any of them means the still compositor is not
    enough. A plain video wallpaper is reported as "video": it can be read
    directly without rendering the scene.
    """
    import json

    reasons: list[str] = []
    scene_json = Path(src_dir) / "scene.json"
    if not scene_json.is_file():
        return reasons

    try:
        data = json.loads(scene_json.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return reasons

    general = data.get("general") or {}
    if _value_of(general.get("camerashake"), False):
        reasons.append("camera shake")

    for obj in data.get("objects") or []:
        if not isinstance(obj, dict) or not _value_of(obj.get("visible"), True):
            continue
        if obj.get("particle"):
            reasons.append("particles")
        effects = obj.get("effects") or []
        if any(isinstance(e, dict) and _value_of(e.get("visible"), True)
               for e in effects):
            reasons.append("effects")
        if obj.get("animations"):
            reasons.append("puppetry")

        model_path = Path(src_dir) / str(obj.get("image") or "")
        if model_path.is_file():
            try:
                model = json.loads(model_path.read_text(
                    encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                model = {}
            if model.get("animationlayers"):
                reasons.append("puppetry")

    from . import constants

    for path in Path(src_dir).rglob("*"):
        if path.is_file() and path.suffix.lower() in constants.VIDEO_EXTS:
            reasons.append("video")
            break

    # Keep the order but drop duplicates.
    return list(dict.fromkeys(reasons))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _free_display() -> int:
    for number in range(90, 190):
        if (Path("/tmp/.X11-unix") / f"X{number}").exists():
            continue
        if Path(f"/tmp/.X{number}-lock").exists():
            continue
        return number
    raise WallpaperError("no free X display number between 90 and 189")


def _wait_for(predicate, timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _tail(path: Path, lines: int = 12) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _unique_frames(frames: list[Path]) -> list[Path]:
    """Drop consecutive duplicates (software rendering is slower than capture)."""
    import hashlib

    unique: list[Path] = []
    previous = None
    for frame in frames:
        digest = hashlib.md5(frame.read_bytes()).hexdigest()
        if digest != previous:
            unique.append(frame)
            previous = digest
    return unique


def _pick_evenly(frames: list[Path], count: int) -> list[Path]:
    """Pick `count` frames spread across the list, keeping the order."""
    if len(frames) <= count:
        return frames
    if count <= 1:
        return frames[:1]
    step = (len(frames) - 1) / (count - 1)
    return [frames[round(index * step)] for index in range(count)]


def _crop_and_scale_frame(src: Path, dest: Path, target_size: tuple[int, int],
                           proj_size: tuple[int, int], fill_size: tuple[int, int],
                           fill_offset: tuple[int, int]) -> None:
    """Crop to fill region then scale to target size using ffmpeg.

    The raw frame is at proj_size (projection resolution). We crop to the
    fill region (fill_size at fill_offset), then scale to target_size.
    """
    require_tools()
    target_width, target_height = target_size
    proj_width, proj_height = proj_size
    fill_width, fill_height = fill_size
    offset_x, offset_y = fill_offset

    # ffmpeg crop: crop=width:height:x:y
    # scale: scale=target_width:target_height
    vf = f"crop={fill_width}:{fill_height}:{offset_x}:{offset_y},scale={target_width}:{target_height}:flags=lanczos"
    
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vf", vf,
        "-frames:v", "1",
        "-c:v", "png", "-pix_fmt", "rgba",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        raise WallpaperError(f"ffmpeg crop+scale failed: {proc.stderr.strip() or 'no output'}")


def _convert_ppm_to_png(src: Path, dest: Path) -> None:
    """Convert PPM to PNG using ffmpeg."""
    require_tools()
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-frames:v", "1",
        "-c:v", "png", "-pix_fmt", "rgba",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        raise WallpaperError(f"ffmpeg PPM->PNG failed: {proc.stderr.strip() or 'no output'}")


def _wayland_available() -> bool:
    """Check if we're on a Wayland compositor that supports headless outputs."""
    return (
        os.environ.get("WAYLAND_DISPLAY")
        and shutil.which("grim") is not None
        and shutil.which("hyprctl") is not None
    )


def _create_headless_monitor(name: str, width: int, height: int) -> bool:
    """Create a headless monitor on Hyprland using 'output create headless'.

    Note: Hyprland's headless monitor is always 1920x1080. We capture at that
    resolution and scale down to the target size.
    Returns True on success.
    """
    try:
        subprocess.run(
            ["hyprctl", "output", "create", "headless", name],
            capture_output=True, check=True, timeout=5
        )
        # Wait for monitor to appear
        for _ in range(30):
            result = subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=2
            )
            try:
                monitors = json.loads(result.stdout)
                for m in monitors:
                    if m.get("name") == name:
                        return True
            except Exception:
                pass
            time.sleep(0.1)
    except Exception:
        pass
    return False


def _remove_headless_monitor(name: str) -> None:
    """Remove the headless monitor."""
    try:
        subprocess.run(
            ["hyprctl", "keyword", "monitor", f"{name},disable"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass


def _capture_wayland_monitor(monitor_name: str, capture_dir: Path, count: int, fps: float) -> list[Path]:
    """Capture frames from a Wayland monitor using grim (PPM for speed)."""
    captured: list[Path] = []
    frame_interval = 1.0 / fps
    next_capture = time.monotonic()
    
    for i in range(count):
        target = capture_dir / f"{i:04d}.ppm"
        # Use grim with PPM format (no compression, much faster)
        proc = subprocess.run(
            ["grim", "-o", monitor_name, "-t", "ppm", str(target)],
            capture_output=True
        )
        if proc.returncode != 0 or not target.exists():
            break
        captured.append(target)
        next_capture += frame_interval
        sleep_time = next_capture - time.monotonic()
        if sleep_time > 0:
            time.sleep(sleep_time)
    
    return captured


def render_frames(src_dir: Path, dest_dir: Path, count: int,
                  size: tuple[int, int], wait: float,
                  *, engine: Engine | None = None,
                  log_dir: Path | None = None) -> tuple[list[Path], float]:
    """Render `count` frames of the scene, one every `wait` seconds.

    Uses the existing Wayland compositor with a headless monitor for
    GPU-accelerated rendering. The engine runs with --screen-root and
    --scaling fill on the headless monitor, and frames are captured with grim.

    Falls back to Xvfb + x11grab if no Wayland compositor is available.
    """
    if not available() and engine is None:
        raise WallpaperError(unavailable_reason() or "cannot render the scene")
    require_tools()

    engine = engine or find_engine()
    if engine is None:
        raise WallpaperError(unavailable_reason() or "no engine found")

    count = max(1, min(int(count), 48))
    width, height = size
    src_dir = Path(src_dir).resolve()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for stale in dest_dir.glob("*.png"):
        stale.unlink()

    log_dir = Path(log_dir or dest_dir.parent)
    log_dir.mkdir(parents=True, exist_ok=True)
    engine_log = log_dir / "linux-wallpaperengine.log"
    ready_png = log_dir / "wpe-ready.png"
    ready_png.unlink(missing_ok=True)
    capture_started = time.monotonic()

    # Try Wayland headless monitor first (GPU-accelerated, correct --scaling fill)
    if _wayland_available():
        monitor_name = f"paper2ps4-{os.getpid()}"
        print(f"  [wpe] Wayland compositor detected, creating headless monitor {monitor_name}")
        if _create_headless_monitor(monitor_name, width, height):
            try:
                frames, actual_wait = _render_wayland_headless(
                    engine, src_dir, dest_dir, count, size, wait, monitor_name,
                    engine_log, ready_png, log_dir, capture_started
                )
                print(f"  [wpe] {len(frames)} frame(s) captured, actual wait {actual_wait:.3f}s ({1/actual_wait:.1f} fps)")
                return frames, actual_wait
            finally:
                _remove_headless_monitor(monitor_name)
        print("  [wpe] Wayland headless monitor failed, falling back to Xvfb")

    # Fallback to Xvfb
    print("  [wpe] Using Xvfb fallback (software rendering)")
    frames, actual_wait = _render_xvfb_fallback(
        engine, src_dir, dest_dir, count, size, wait,
        engine_log, ready_png, log_dir, capture_started
    )

    print(f"  [wpe] {len(frames)} frame(s) captured, target wait {actual_wait:.3f}s ({1/actual_wait:.1f} fps)")
    return frames, actual_wait


def _render_wayland_headless(engine: Engine, src_dir: Path, dest_dir: Path, count: int,
                             size: tuple[int, int], wait: float, monitor_name: str,
                             engine_log: Path, ready_png: Path, log_dir: Path,
                             capture_started: float) -> tuple[list[Path], float]:
    """Render using Wayland headless monitor with grim capture.

    The engine runs with --screen-root <monitor> --bg <scene> --scaling fill
    on the headless monitor. Frames are captured with grim -o <monitor>.
    """
    target_width, target_height = size
    wpe = None
    capture_dir = dest_dir / ".capture"
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir()

    env = os.environ.copy()
    env["XDG_SESSION_TYPE"] = "wayland"
    env["WAYLAND_DISPLAY"] = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    library = str(engine.root)
    current = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = library + (":" + current if current else "")

    command = [
        str(engine.executable),
        "--screen-root", monitor_name,
        "--bg", str(src_dir),
        "--scaling", "fill",
        "--fps", str(ENGINE_FPS),
        "--silent",
        "--disable-mouse",
        "--disable-parallax",
        "--assets-dir", str(engine.assets),
        "--screenshot", str(ready_png),
        "--screenshot-delay", str(ENGINE_FPS // 2),
    ]
    with open(engine_log, "wb") as log:
        wpe = subprocess.Popen(
            command, cwd=str(engine.root), env=env,
            stdout=log, stderr=subprocess.STDOUT,
        )

    ready = _wait_for(
        lambda: ready_png.is_file() and ready_png.stat().st_size > 0
        or wpe.poll() is not None,
        READY_TIMEOUT,
    )
    if wpe.poll() is not None:
        raise WallpaperError(
            "linux-wallpaperengine exited before rendering. "
            + (_tail(engine_log) or ""))
    if not ready:
        raise WallpaperError(
            f"... no frame after {READY_TIMEOUT:.0f}s. "
            + (_tail(engine_log) or ""))

    time.sleep(WARMUP_SECONDS)

    # Capture at target rate - use 2x to ensure enough distinct frames
    capture_fps = max(30.0, 1.0 / max(0.01, wait) * 2)
    captured = _capture_wayland_monitor(monitor_name, capture_dir, count * 2, capture_fps)
    _terminate(wpe)

    if not captured:
        raise WallpaperError("grim captured no frames.")

    unique = _unique_frames(captured)
    if len(unique) < 2:
        raise WallpaperError("not enough distinct frames captured")

    # Use the requested wait (target playback rate)
    actual_wait = wait

    chosen = _pick_evenly(unique[:count], count)
    frames = []
    # Headless monitor is 1920x1080; scale down to target size
    for index, frame in enumerate(chosen):
        target = dest_dir / f"{index:02d}.png"
        _scale_frame(frame, target, (target_width, target_height))
        frames.append(target)

    shutil.rmtree(capture_dir, ignore_errors=True)
    return frames, actual_wait


def _scale_frame(src: Path, dest: Path, size: tuple[int, int]) -> None:
    """Scale an image to the target size using ffmpeg (high quality)."""
    require_tools()
    target_width, target_height = size
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vf", f"scale={target_width}:{target_height}:flags=lanczos",
        "-frames:v", "1",
        "-c:v", "png", "-pix_fmt", "rgba",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        raise WallpaperError(f"ffmpeg scale failed: {proc.stderr.strip() or 'no output'}")


def _read_scene_projection(src_dir: Path) -> tuple[int, int] | None:
    """Read the orthogonalprojection from scene.json if present."""
    import json
    scene_json = src_dir / "scene.json"
    if not scene_json.is_file():
        return None
    try:
        data = json.loads(scene_json.read_text(encoding="utf-8", errors="replace"))
        proj = data.get("general", {}).get("orthogonalprojection", {})
        if proj and "width" in proj and "height" in proj:
            return int(proj["width"]), int(proj["height"])
    except Exception:
        pass
    return None


def _render_xvfb_fallback(engine: Engine, src_dir: Path, dest_dir: Path, count: int,
                 size: tuple[int, int], wait: float,
                 engine_log: Path, ready_png: Path, log_dir: Path,
                 capture_started: float) -> tuple[list[Path], float]:
    """Render using Xvfb + ffmpeg x11grab (software rendering fallback).

    To correctly emulate --scaling fill (which the engine only supports for
    --screen-root), we render at a resolution with the same aspect ratio as
    the scene's projection, so DefaultUVs = StretchUVs (full projection visible).
    We use a reduced resolution (max ~1.5MP) for llvmpipe performance, then
    crop to the fill region (16:9 at full projection height) and scale to target.
    """
    target_width, target_height = size
    
    # Read scene projection to compute correct fill region
    proj = _read_scene_projection(src_dir)
    if proj:
        proj_width, proj_height = proj
    else:
        proj_width, proj_height = 3440, 1440
    
    # Fill region in projection coordinates: 16:9 crop at full height, centered
    fill_height = proj_height
    fill_width = int(fill_height * 16 / 9)
    fill_x_offset = max(0, (proj_width - fill_width) // 2)
    
    # Render at a resolution with the SAME ASPECT RATIO as the projection,
    # so DefaultUVs = StretchUVs (shows full projection). Cap at ~1.5MP for speed.
    proj_aspect = proj_width / proj_height
    max_render_pixels = 1_500_000
    render_height = int((max_render_pixels / proj_aspect) ** 0.5)
    render_height = min(render_height, proj_height)
    render_height = max(render_height, 720)
    render_width = int(render_height * proj_aspect)

    playback_fps = max(1.0, 1.0 / max(0.01, wait))
    capture_dir = dest_dir / ".capture"
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir()

    xvfb = None
    wpe = None
    display = _free_display()
    display_name = f":{display}"

    try:
        xvfb = subprocess.Popen(
            ["Xvfb", display_name, "-screen", "0", f"{render_width}x{render_height}x24",
             "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not _wait_for(
                lambda: (Path("/tmp/.X11-unix") / f"X{display}").exists()
                or xvfb.poll() is not None, 10.0):
            raise WallpaperError("Xvfb did not start")
        if xvfb.poll() is not None:
            raise WallpaperError(f"Xvfb exited with code {xvfb.returncode}")

        env = os.environ.copy()
        env.pop("WAYLAND_DISPLAY", None)
        env["XDG_SESSION_TYPE"] = "x11"
        env["DISPLAY"] = display_name
        library = str(engine.root)
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = library + (":" + current if current else "")

        command = [
            str(engine.executable),
            "--window", f"0x0x{render_width}x{render_height}",
            "--fps", str(ENGINE_FPS),
            "--silent",
            "--disable-mouse",
            "--disable-parallax",
            "--assets-dir", str(engine.assets),
            "--screenshot", str(ready_png),
            "--screenshot-delay", str(ENGINE_FPS // 2),
            str(src_dir),
        ]
        with open(engine_log, "wb") as log:
            wpe = subprocess.Popen(
                command, cwd=str(engine.root), env=env,
                stdout=log, stderr=subprocess.STDOUT,
            )

        ready = _wait_for(
            lambda: ready_png.is_file() and ready_png.stat().st_size > 0
            or wpe.poll() is not None,
            READY_TIMEOUT,
        )
        if wpe.poll() is not None:
            raise WallpaperError(
                "linux-wallpaperengine exited before rendering. "
                + (_tail(engine_log) or ""))
        if not ready:
            raise WallpaperError(
                f"... no frame after {READY_TIMEOUT:.0f}s. "
                + (_tail(engine_log) or ""))

        time.sleep(WARMUP_SECONDS)

        chunk_count = max(4, int(round(CAPTURE_FPS * CAPTURE_CHUNK_SECONDS)))
        captured: list[Path] = []
        chunk_index = 0
        unique: list[Path] = []

        while True:
            cmd = [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "x11grab", "-draw_mouse", "0",
                "-framerate", f"{CAPTURE_FPS:.6f}",
                "-video_size", f"{render_width}x{render_height}",
                "-i", display_name,
                "-frames:v", str(chunk_count),
                str(capture_dir / f"{chunk_index:03d}_%04d.png"),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                tail = proc.stderr.strip().splitlines()[-6:]
                raise WallpaperError(
                    "ffmpeg could not capture the frames: "
                    + ("\n".join(tail) or f"code {proc.returncode}"))

            captured += sorted(capture_dir.glob(f"{chunk_index:03d}_*.png"))
            chunk_index += 1
            if not captured:
                raise WallpaperError("ffmpeg captured no frames.")

            unique = _unique_frames(captured)
            if (len(unique) >= count
                    or time.monotonic() - capture_started >= MAX_CAPTURE_SECONDS):
                break

        _terminate(wpe)

        actual_wait = wait
        if len(unique) < count:
            print(f"  [wpe] only {len(unique)} distinct frames, capping at timeout")

        chosen = _pick_evenly(unique, count)
        frames: list[Path] = []
        
        # Compute fill region in render coordinates (scaled from projection)
        scale = render_width / proj_width
        render_fill_width = int(fill_width * scale)
        render_fill_height = int(fill_height * scale)
        render_fill_x = int(fill_x_offset * scale)
        
        for index, frame in enumerate(chosen):
            target = dest_dir / f"{index:02d}.png"
            _crop_and_scale_frame(
                frame, target, (target_width, target_height),
                (render_width, render_height),  # source (render) size
                (render_fill_width, render_fill_height),  # crop size
                (render_fill_x, 0)  # crop offset
            )
            frames.append(target)

        shutil.rmtree(capture_dir, ignore_errors=True)
        return frames, actual_wait

    finally:
        if wpe is not None:
            _terminate(wpe)
        if xvfb is not None:
            _terminate(xvfb)
        ready_png.unlink(missing_ok=True)
        shutil.rmtree(capture_dir, ignore_errors=True)
