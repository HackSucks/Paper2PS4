"""paper2ps4 command-line interface.

Turns a Wallpaper Engine project into a PS4 installable FPKG theme.

The simple way to use it: point it at the project folder and wait.

    paper2ps4 ~/wallpapers/projects/2521462823 -o out/niko.pkg

The folder is expected to hold what Wallpaper Engine downloaded:

    <project>/
      project.json     the title and the properties
      preview.jpg      the author's preview image (the workshop thumbnail)
      scene.pkg        the scene itself

The output extension is the caller's choice: .pkg and .fpkg are the same
thing, and some installers only accept one of them.

The Windows helper tools (orbis-pub-cmd.exe, ps4_at9tool.exe) are looked for
in the folders given with --tools, in the PAPER2PS4_TOOLS environment
variable and on PATH. Nothing is hardcoded.

Pipeline:
    1. Unpack scene.pkg (PKGV/PKGM container).
    2. Render the scene: the objects are composited the way Wallpaper Engine
       draws them, so the theme looks like the wallpaper.
    3. Build the theme assets: theme.xml, param.sfo, icon0.png, pic1.png,
       preview.png, plus the menu music (ATRAC9) when the scene has audio.
    4. Package with orbis-pub-cmd.exe.

Shaders and particles are the one thing that cannot be reproduced: a PS4
theme cannot run custom HLSL/GLSL. Their output is already baked into the
textures, so what is missing is the animation, not the artwork.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import audio
from . import compose
from . import constants
from . import overrides as overrides_mod
from . import pkg as pkg_mod
from . import scene as scene_mod
from . import theme as theme_mod
from . import tools
from . import unpack
from . import verify as verify_mod
from . import video


# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------

PREVIEW_NAMES = ("preview.jpg", "preview.jpeg", "preview.png", "preview.webp",
                 "preview.gif", "preview.bmp")


def find_scene_pkg(project_dir: Path) -> Path:
    """The scene.pkg inside a project folder."""
    for name in ("scene.pkg", "scene.mpkg"):
        candidate = project_dir / name
        if candidate.is_file():
            return candidate
    candidates = sorted(project_dir.glob("*.pkg"))
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"no scene.pkg found in {project_dir}")


def find_preview(project_dir: Path) -> Path | None:
    """The preview image Wallpaper Engine downloaded next to the scene."""
    for name in PREVIEW_NAMES:
        candidate = project_dir / name
        if candidate.is_file():
            return candidate
    return None


def load_project_metadata(project_dir: Path) -> dict:
    """Read project.json if it is there."""
    path = project_dir / "project.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def project_title(metadata: dict, fallback: str) -> str:
    for key in ("title", "Title", "name"):
        value = metadata.get(key)
        if value:
            return str(value)
    return fallback


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def convert(project_dir: Path, out_fpkg: Path, *,
            title: str | None = None,
            title_id: str = constants.PKG_DEFAULTS["title_id"],
            content_id: str | None = None,
            category: str = constants.PKG_DEFAULTS["category"],
            passcode: str = constants.PKG_DEFAULTS["passcode"],
            animated: bool = False,
            frames: int | None = None,
            size: str | None = None,
            wait: float = constants.DEFAULT_WAIT_SECONDS,
            audio_src: Path | None = None,
            no_music: bool = False,
            keep_work: bool = False,
            icons_dir: Path | None = None,
            sfx_dir: Path | None = None,
            ) -> Path:
    """Run the whole conversion. Returns the path to the produced package."""
    project_dir = project_dir.resolve()
    if not project_dir.is_dir():
        raise FileNotFoundError(f"project folder not found: {project_dir}")

    out_fpkg = Path(out_fpkg)
    if not out_fpkg.suffix:
        # No extension given: .pkg is what the PS4's own installer takes.
        out_fpkg = out_fpkg.with_suffix(".pkg")

    scene_pkg = find_scene_pkg(project_dir)
    metadata = load_project_metadata(project_dir)

    if title is None:
        title = project_title(metadata, project_dir.name)

    if content_id is None:
        clean = "".join(c for c in title.upper() if c.isalnum())[:16] or "THEME"
        clean = clean.ljust(16, "0")
        content_id = f"{constants.PKG_DEFAULTS['content_prefix']}-{title_id}_00-{clean}"

    # Working directory next to the output. It carries the full output name
    # so two outputs in the same folder do not share it.
    work = out_fpkg.parent / f"{out_fpkg.name}.work"
    work_src = work / "src"
    work_assets = work / "assets"
    work_assets.mkdir(parents=True, exist_ok=True)

    # 1. Unpack.
    print(f"[1/5] unpacking {scene_pkg.name} ...")
    magic = unpack.unpack_pkg(scene_pkg, work_src)
    print(f"    magic={magic}")

    # 2. Render the scene.
    print("[2/5] rendering the scene ...")
    scene = scene_mod.load_scene(work_src)
    print(f"    {scene.width}x{scene.height}, {len(scene.objects)} object(s)")

    videos = [p for p in sorted(work_src.rglob("*"))
              if p.is_file() and p.suffix.lower() in constants.VIDEO_EXTS]
    textures = [p for p in sorted(work_src.rglob("*"))
                if p.is_file() and p.suffix.lower() in constants.TEXTURE_EXTS]
    audio_files = [p for p in sorted(work_src.rglob("*"))
                   if p.is_file() and p.suffix.lower() in constants.AUDIO_EXTS]
    shader_count = sum(1 for p in sorted(work_src.rglob("*"))
                       if p.is_file() and p.suffix.lower() in constants.SHADER_EXTS)

    if shader_count:
        print(f"    [*] {shader_count} shader file(s) - custom shaders cannot "
              "run on a PS4 theme; their artwork is already in the textures.")

    background_png: Path | None = None
    frame_pngs: list[Path] | None = None
    animated_size = constants.ANIMATED_SIZE
    if size:
        w, h = size.lower().split("x")
        animated_size = (int(w), int(h))

    budget = min(
        constants.RAF_SIZE_LIMIT // constants.dds_bytes_for(*animated_size),
        constants.MAX_FRAME_COUNT,
    )

    if animated or videos:
        # --- animated theme ----------------------------------------------
        source = videos[0] if videos else None
        if source is None:
            raise RuntimeError(
                "--animated was asked for but the scene has no video file.")
        print(f"    source: {source.relative_to(work_src)}")
        duration, width, height = video.probe_video(source)
        print(f"    {width}x{height}, {duration:.2f}s")

        count = min(frames or budget, budget)
        print(f"    {count} frames at {animated_size[0]}x{animated_size[1]} "
              f"(console budget={budget})")
        frame_pngs = video.extract_frames(source, work_assets / "frames",
                                          count, animated_size, duration)
        background_png = frame_pngs[0]
    else:
        # --- static theme: render the scene ------------------------------
        # The scratch files go to the work folder: the unpacked scene is
        # never written to.
        canvas = compose.render_scene(scene, work_src, work_dir=work_assets / "scratch")
        rendered = work_assets / "scene.png"
        compose.write_render(canvas, rendered)
        background_png = rendered
        print(f"    rendered {canvas.shape[1]}x{canvas.shape[0]}")

    # 3. Music.
    at9_path: Path | None = None
    homebgm_enable = False
    if no_music:
        print("[3/5] --no-music: skipping the menu music.")
    elif audio_files:
        chosen = audio.select_bgm(audio_files, preferred=audio_src)
        if chosen is not None:
            print(f"[3/5] music: {len(audio_files)} track(s) in the scene")
            try:
                at9_path = work_assets / "bgm.at9"
                if len(audio_files) > 1 and chosen in audio_files:
                    # A scene often carries a whole playlist: join them into
                    # one file and encode that.
                    wav = audio.concatenate(audio_files, work_assets / "bgm_join.wav")
                    audio.prepare_bgm(wav, work_assets, at9_path)
                else:
                    audio.prepare_bgm(chosen, work_assets, at9_path)
                homebgm_enable = True
                print(f"    -> bgm_home.at9 ({at9_path.stat().st_size / 1024:.1f} KB)")
            except Exception as exc:  # noqa: BLE001
                print(f"    [!] music conversion skipped: {exc}")
    else:
        print("[3/5] no audio in the scene: the theme will be silent.")

    # 4. Assemble the theme.
    print("[4/5] assembling the theme ...")
    content_dir = work / "content"

    # The user's icon and sound effect replacements.
    icon_overrides = overrides_mod.load(icons_dir, sfx_dir)
    if not icon_overrides.empty:
        print(f"    {len(icon_overrides.icons)} icon(s) and "
              f"{len(icon_overrides.sfx)} sound effect(s) to replace")

    theme_mod.assemble_theme(
        work_dir=work,
        content_dir=content_dir,
        title=title,
        background_image=background_png,
        frame_pngs=frame_pngs,
        wait=wait,
        animated_size=animated_size,
        at9_path=at9_path,
        homebgm_enable=homebgm_enable,
        preview_image=find_preview(project_dir),
        overrides=icon_overrides,
    )

    # 5. Package.
    print("[5/5] packaging ...")
    if not pkg_mod.tools_available():
        raise RuntimeError(
            "orbis-pub-cmd.exe not found: "
            + tools.describe_search(constants.ORBIS_PUB_CMD_NAMES) + ". "
            "It needs its ext/ folder (sc.exe, di.exe, libatrac9.dll) next "
            "to it."
        )
    meta = pkg_mod.PackageMeta(
        title=title,
        title_id=title_id,
        content_id=content_id,
        category=category,
        passcode=passcode,
    )
    built = pkg_mod.build_package(work, content_dir, meta)

    # The tool always writes a .pkg; the name the user asked for is what
    # they get, whether that is .pkg or .fpkg.
    out_fpkg.parent.mkdir(parents=True, exist_ok=True)
    if out_fpkg.exists():
        out_fpkg.unlink()
    shutil.move(str(built), str(out_fpkg))
    print(f"[done] {out_fpkg} ({out_fpkg.stat().st_size / 1048576:.1f} MB)")

    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"    work kept at {work}")

    return out_fpkg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("project", type=Path,
                    help="the Wallpaper Engine project folder "
                         "(holds scene.pkg, project.json and the preview)")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output path. The extension is yours to choose: "
                         ".pkg and .fpkg are the same thing, and some "
                         "installers only accept one of them")
    ap.add_argument("-t", "--title", default=None,
                    help="theme title (default: the title in project.json)")
    ap.add_argument("--title-id", default=constants.PKG_DEFAULTS["title_id"],
                    help=f"title ID (default: {constants.PKG_DEFAULTS['title_id']})")
    ap.add_argument("--content-id", default=None,
                    help="content ID (default: generated from the title)")
    ap.add_argument("--category", default=constants.PKG_DEFAULTS["category"],
                    help=f"package category (default: {constants.PKG_DEFAULTS['category']})")
    ap.add_argument("--passcode", default=constants.PKG_DEFAULTS["passcode"],
                    help="32-character package passcode (default: 32 zeros)")
    ap.add_argument("--animated", action="store_true",
                    help="force an animated theme even if the scene has no video")
    ap.add_argument("--frames", type=int, default=None,
                    help=f"number of animated frames (default: {constants.MAX_FRAMES}, "
                         "capped by the console budget)")
    ap.add_argument("--size", default=None,
                    help="animated resolution WxH (default: 1280x720; "
                         "960x540 and 640x360 fit more frames)")
    ap.add_argument("--wait", type=float, default=constants.DEFAULT_WAIT_SECONDS,
                    help=f"seconds per animated frame (default: {constants.DEFAULT_WAIT_SECONDS})")
    ap.add_argument("--audio", type=Path, default=None,
                    help="use only this audio file as the music")
    ap.add_argument("--no-music", action="store_true",
                    help="build the theme without music")
    ap.add_argument("--icons", type=Path, default=None,
                    help="folder with system icon replacements "
                         "(power.png, browser.png, icon0.png, ...)")
    ap.add_argument("--sfx", type=Path, default=None,
                    help="folder with sound effect replacements "
                         "(se_decide, se_cursor, se_cancel)")
    ap.add_argument("--keep-work", action="store_true",
                    help="keep the intermediate working folder")
    ap.add_argument("--tools", type=Path, action="append", default=None,
                    metavar="DIR",
                    help="folder holding orbis-pub-cmd.exe (and its ext/ "
                         "subfolder) and ps4_at9tool.exe. May be given more "
                         "than once. They are also looked for on PATH, and "
                         f"the {tools.TOOLS_ENV_VAR} environment variable "
                         "adds folders too")
    ap.add_argument("--verify", action="store_true",
                    help="check the finished package and report what is inside")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # The tool folders have to be known before anything looks for the tools.
    tools.set_tool_dirs(args.tools or [])

    try:
        out = convert(
            project_dir=args.project,
            out_fpkg=args.out,
            title=args.title,
            title_id=args.title_id,
            content_id=args.content_id,
            category=args.category,
            passcode=args.passcode,
            animated=args.animated,
            frames=args.frames,
            size=args.size,
            wait=args.wait,
            audio_src=args.audio,
            no_music=args.no_music,
            keep_work=args.keep_work,
            icons_dir=args.icons,
            sfx_dir=args.sfx,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    if args.verify:
        ok, problems, contents = verify_mod.verify_theme_package(out)
        print("\n=== package contents ===")
        for entry in contents:
            print(f"  {entry}")
        if ok:
            print("\nThe package is a valid PS4 theme.")
        else:
            print("\nProblems found:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
