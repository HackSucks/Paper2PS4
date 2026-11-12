"""PS4 theme asset assembly.

Takes the unpacked/converted Wallpaper Engine assets and produces the content
folder the PS4 expects:

    texture/background/homescreen.png      1920x1080 (also functionscreen)
    texture/preview.png                    740x416
    sce_sys/icon0.png                      512x512
    theme.xml                              descriptor
    sound/bgm_home.at9                     menu music (optional)
    scene/background.raf/                  animated background (optional)
        NN.dds                             DXT1a frames
        plane.mdx                          shared plane model
        index.xml                          scene

For a static theme the single background image is used for every slot.
For an animated theme the frames become background.raf/ and the home image
becomes the theme's list thumbnail / icon.

The theme package deliberately has no sce_sys/pic1.png: orbis-pub-cmd's
img_verify rejects it ("The file/directory must not exist") for themes.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import constants
from . import overrides as overrides_mod
from .dxt1 import encode_dds
from .media import image_to_rgba, flatten_alpha, render_png
from .mdx import quad_model
from .raf import build_scene as build_raf_scene, plane_half_size
from .theme_xml import build_theme_xml


class ThemeError(RuntimeError):
    """Theme asset assembly failure."""


def _write_dds(rgba, dest: Path) -> int:
    """Encode an RGBA array to a DXT1a .dds and write it. Returns byte size."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = encode_dds(rgba)
    dest.write_bytes(data)
    return len(data)


def _image_is_large_enough(path: Path, min_width: int) -> bool:
    """Is the image at least `min_width` wide?

    Used to decide whether a workshop preview is usable as the theme's
    thumbnail: a 194x194 preview blown up to 740x416 looks like a mistake.
    """
    try:
        from PIL import Image  # optional
        with Image.open(path) as img:
            return img.width >= min_width
    except Exception:
        pass
    # No PIL: ask ffmpeg instead.
    try:
        from .media import FFPROBE, require_tools, _run
        require_tools()
        out = _run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width", "-of", "csv=p=0", str(path)],
                   capture_stdout=True)
        width = int(out.decode().strip().split(",")[0])
        return width >= min_width
    except Exception:
        return False


def _prepare_icon(source: Path | None, fallback: Path | None, dest: Path,
                  size: tuple[int, int], alpha: bool = True) -> Path:
    """Render one icon slot: use `source` if present, else `fallback`."""
    src = source or fallback
    if src is None:
        raise ThemeError(f"no image available for {dest.name}")
    return render_png(src, dest, size, alpha=alpha)


def assemble_theme(work_dir: Path, content_dir: Path, *,
                   title: str, background_image: Path | None = None,
                   frame_pngs: list[Path] | None = None, wait: float = 0.1,
                   animated_size: tuple[int, int] = constants.ANIMATED_SIZE,
                   at9_path: Path | None = None,
                   homebgm_enable: bool = False,
                   theme_color: int = 0,
                   preview_image: Path | None = None,
                   overrides: "overrides_mod.Overrides | None" = None) -> None:
    """Assemble the full theme content tree inside `content_dir`.

    Either `background_image` (static) or `frame_pngs` (animated) must be given.
    `preview_image` is the author's own preview of the wallpaper; when it is
    there it becomes the theme's thumbnail, which is what the console shows
    in the theme list. `overrides` carries the icon and sound effect
    replacements the user asked for.
    """
    content_dir.mkdir(parents=True, exist_ok=True)

    tex_bg = content_dir / "texture" / "background"
    tex_bg.mkdir(parents=True, exist_ok=True)
    tex_preview = content_dir / "texture"
    tex_preview.mkdir(parents=True, exist_ok=True)
    sce_sys = content_dir / "sce_sys"
    sce_sys.mkdir(parents=True, exist_ok=True)
    sound_dir = content_dir / "sound"

    # --- animated background (RAF scene) takes precedence -----------------
    raf_dir = content_dir / constants.RAF_PARENT / constants.RAF_DIR_NAME
    is_animated = bool(frame_pngs)
    total_raf_bytes = 0

    if is_animated:
        raf_dir.mkdir(parents=True, exist_ok=True)
        # Plane model shared by all frames.
        half_w, half_h = plane_half_size()
        (raf_dir / "plane.mdx").write_bytes(
            quad_model(half_w=half_w, half_h=half_h)
        )

        # Respect the frame budget: cap by weight and count.
        max_frames = min(
            constants.RAF_SIZE_LIMIT // constants.dds_bytes_for(*animated_size),
            constants.MAX_FRAME_COUNT,
        )
        frames = frame_pngs[:max_frames]
        if len(frame_pngs) > max_frames:
            print(f"  [!] truncated {len(frame_pngs)} frames to {max_frames} "
                  f"(console cap)")

        dds_names: list[str] = []
        for index, frame in enumerate(frames):
            name = f"{index:02d}.dds"
            rgba = image_to_rgba(frame, animated_size)
            rgba = flatten_alpha(rgba)
            size = _write_dds(rgba, raf_dir / name)
            total_raf_bytes += size
            dds_names.append(name)
            print(f"  [raf] {name} ({size / 1024:.1f} KB)")

        # Budget checks.
        if total_raf_bytes > constants.RAF_SIZE_LIMIT:
            raise ThemeError(
                f"RAF frames total {total_raf_bytes / 1048576:.2f} MiB, over the "
                f"{constants.RAF_SIZE_LIMIT / 1048576:.0f} MiB console limit."
            )
        if len(frames) > constants.MAX_FRAME_COUNT:
            raise ThemeError(
                f"{len(frames)} frames exceeds the {constants.MAX_FRAME_COUNT} "
                "frame cap."
            )

        index_xml = build_raf_scene(dds_names, wait)
        (raf_dir / "index.xml").write_text(index_xml, encoding="utf-8")
        print(f"  [raf] index.xml ({len(frames)} frames, wait={wait}s, "
              f"{total_raf_bytes / 1024:.1f} KiB total)")

        # Home image becomes the theme thumbnail/icon.
        home_source = background_image
    else:
        home_source = background_image

    if home_source is None:
        raise ThemeError("no background image provided (static or animated).")

    # --- homescreen.png / functionscreen.png (1920x1080) -----------------
    # Only for a static theme: with a RAF scene the console paints the scene
    # on both screens, so the still backgrounds would never be seen. Leaving
    # them out also keeps the package smaller.
    if not is_animated:
        # These are real PNG files despite living next to DXT1a data
        # elsewhere in the theme: only background.raf/*.dds is DXT1a. A DDS
        # stored under a .png name is what makes the console call the theme
        # invalid when it is selected.
        homescreen_png = tex_bg / "homescreen.png"
        render_png(home_source, homescreen_png, constants.STATIC_SIZE, alpha=False)
        print(f"  [bg] homescreen.png "
              f"({constants.STATIC_SIZE[0]}x{constants.STATIC_SIZE[1]})")

        # functionscreen.png copies from home (only visible without RAF).
        funcscreen_png = tex_bg / "functionscreen.png"
        shutil.copy2(homescreen_png, funcscreen_png)
    else:
        print("  [bg] animated: the still backgrounds are left out "
              "(the scene covers both screens)")

    # --- preview.png (740x416) ------------------------------------------
    # The author's preview image is the theme's thumbnail: it is what the
    # console shows in the theme list, so it should be the wallpaper's own
    # artwork rather than a crop of the background. Workshop previews are
    # often tiny though, and a blurry thumbnail is worse than none, so a
    # too-small preview falls back to the background.
    preview_png = tex_preview / "preview.png"
    preview_source = home_source
    if preview_image and preview_image.is_file():
        if _image_is_large_enough(preview_image, constants.PREVIEW_MIN_WIDTH):
            preview_source = preview_image
        else:
            print(f"  [bg] preview image is too small - using the background "
                  "for the thumbnail")
    render_png(preview_source, preview_png, constants.PREVIEW_SIZE, alpha=False)
    print(f"  [bg] preview.png ({constants.PREVIEW_SIZE[0]}x{constants.PREVIEW_SIZE[1]}"
          f"{' from ' + preview_source.name if preview_source != home_source else ''})")

    # --- sce_sys/icon0.png (512x512) -------------------------------------
    # 24-bit on purpose: the checker rejects a 32-bit PNG here. There is no
    # pic1.png in a theme package (the checker rejects it as well).
    icon0 = sce_sys / "icon0.png"
    _prepare_icon(home_source, home_source, icon0, constants.SCE_ICON_SIZE,
                  alpha=False)

    # --- theme.xml --------------------------------------------------------
    theme_xml = build_theme_xml(
        label=title,
        themecolor=theme_color,
        homebgm_enable=homebgm_enable and at9_path is not None,
    )
    (content_dir / "theme.xml").write_text(theme_xml, encoding="utf-8")

    # --- music ------------------------------------------------------------
    if at9_path is not None:
        sound_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(at9_path, sound_dir / "bgm_home.at9")
        print(f"  [audio] sound/bgm_home.at9")

    # --- the user's icon and sound effect replacements --------------------
    if overrides is not None and not overrides.empty:
        written = overrides_mod.apply_icons(content_dir, overrides)
        for path in written:
            print(f"  [icon] {path}")
        written = overrides_mod.apply_sfx(content_dir, overrides)
        for path in written:
            print(f"  [sfx]  {path}")
    else:
        print("  [icon] no replacements given: the console's own icons are kept")

def estimate_raf_bytes(frame_count: int, size: tuple[int, int] = constants.ANIMATED_SIZE) -> int:
    """Bytes a frame sequence would occupy as DXT1a .dds files."""
    return frame_count * constants.dds_bytes_for(*size)
