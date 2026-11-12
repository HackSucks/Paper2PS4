"""Headless scene rendering with linux-wallpaperengine.

A PS4 theme cannot run Wallpaper Engine's shaders. The only way to keep a
scene that *does* animate (puppetry, particles, rain, pulsing lights, custom
shaders) is to render it and ship the result as a frame sequence, which the
console plays back from the RAF scene.

linux-wallpaperengine renders the real thing. It needs a window, so on a
machine without a display it runs on a private Xvfb display, and ffmpeg
grabs the frames with x11grab. Nothing is shown to the user.

The engine is looked for in this order:

  1. the folder given with ``--wpe`` (or the ``PAPER2PS4_WPE`` env var)
  2. ``vendor/linux-wallpaperengine`` next to the package (the bundled copy)
  3. PATH

The bundled copy may keep its assets compressed (``assets.tar.xz``); they are
unpacked once, on first use. Rendering is Linux-only: on Windows the caller
falls back to the static renderer (or to a scene that is a plain video).
"""

from __future__ import annotations

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

#: The engine renders at this rate while the frames are grabbed.
ENGINE_FPS = 30

#: How often to grab the window. The engine may render more slowly than
#: this; the duplicates are dropped afterwards.
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


def render_frames(src_dir: Path, dest_dir: Path, count: int,
                  size: tuple[int, int], wait: float,
                  *, engine: Engine | None = None,
                  log_dir: Path | None = None) -> list[Path]:
    """Render `count` frames of the scene, one every `wait` seconds.

    The engine runs on a private Xvfb display at `size`; ffmpeg grabs the
    window at the final playback rate so the motion is even. Software
    rendering can be slower than that rate, so the capture runs longer than
    the final loop and the distinct frames are spread across it: otherwise
    a slow scene would produce the same picture over and over.
    """
    if not available() and engine is None:
        raise WallpaperError(unavailable_reason() or "cannot render the scene")
    require_tools()

    engine = engine or find_engine()
    if engine is None:
        raise WallpaperError(unavailable_reason() or "no engine found")

    count = max(1, min(int(count), 48))
    width, height = size
    playback_fps = max(1.0, 1.0 / max(0.01, wait))
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
    capture_dir = dest_dir / ".capture"
    if capture_dir.exists():
        shutil.rmtree(capture_dir)
    capture_dir.mkdir()

    # Capture in short batches and stop as soon as enough distinct frames
    # exist. A fast scene needs one batch; a heavy one under software
    # rendering keeps going until the deadline.
    chunk_count = max(4, int(round(CAPTURE_FPS * CAPTURE_CHUNK_SECONDS)))
    capture_started = time.monotonic()
    captured: list[Path] = []
    chunk_index = 0
    unique: list[Path] = []

    xvfb = None
    wpe = None
    display = _free_display()
    display_name = f":{display}"

    try:
        xvfb = subprocess.Popen(
            ["Xvfb", display_name, "-screen", "0", f"{width}x{height}x24",
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
            "--window", f"0x0x{width}x{height}",
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

        # The screenshot means the window exists; give the scene a moment to
        # animate away from its initial state.
        time.sleep(WARMUP_SECONDS)

        while True:
            cmd = [
                FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "x11grab", "-draw_mouse", "0",
                "-framerate", f"{CAPTURE_FPS:.6f}",
                "-video_size", f"{width}x{height}",
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

        chosen = _pick_evenly(unique, count)

        frames: list[Path] = []
        for index, frame in enumerate(chosen):
            target = dest_dir / f"{index:02d}.png"
            shutil.copyfile(frame, target)
            frames.append(target)
        # index.xml and the console expect consecutive names; a scene with
        # fewer distinct frames than asked for still plays, just faster.
        print(f"  [wpe] {len(unique)} distinct frame(s) captured, "
              f"{len(frames)} used")
        return frames

    finally:
        if wpe is not None:
            _terminate(wpe)
        if xvfb is not None:
            _terminate(xvfb)
        ready_png.unlink(missing_ok=True)
        shutil.rmtree(capture_dir, ignore_errors=True)
