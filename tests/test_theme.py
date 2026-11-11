"""End-to-end tests: a project folder in, a valid PS4 theme out.

These use the real Wallpaper Engine projects on this machine when they are
there, and a synthetic project otherwise.
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper2ps4 import cli, compose, constants, scene as scene_mod, tex as T  # noqa: E402
from paper2ps4 import unpack, verify as verify_mod  # noqa: E402

PROJECTS = Path.home() / "wallpapers" / "projects" / "myprojects"

FAILS: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    print(("  OK  " if condition else " FAIL ") + label + (f"   {extra}" if extra else ""))
    if not condition:
        FAILS.append(label)


def skip(label: str, why: str) -> None:
    print(f"  SKIP  {label}   {why}")


# ---------------------------------------------------------------------------
# A synthetic project, so the tests do not depend on what is downloaded
# ---------------------------------------------------------------------------

def build_synthetic_project(root: Path) -> Path:
    """A project folder with a scene.pkg, a project.json and a preview."""
    src = root / "src"
    (src / "materials").mkdir(parents=True, exist_ok=True)
    (src / "models").mkdir(parents=True, exist_ok=True)

    # A 64x32 pink texture.
    pixels = np_pink(64, 32)
    tex_path = build_tex(src / "materials" / "bg.tex", pixels)

    (src / "materials" / "bg.json").write_text(json.dumps({
        "passes": [{"blending": "translucent", "cullmode": "nocull",
                    "depthtest": "disabled", "depthwrite": "disabled",
                    "shader": "genericimage2", "textures": ["bg"]}]
    }, indent=2), encoding="utf-8")

    (src / "models" / "bg.json").write_text(json.dumps({
        "autosize": True, "material": "materials/bg.json"
    }, indent=2), encoding="utf-8")

    (src / "scene.json").write_text(json.dumps({
        "camera": {"center": "0 0 0", "eye": "0 0 1", "up": "0 1 0"},
        "general": {
            "clearcolor": "0.0 0.0 0.0",
            "orthogonalprojection": {"height": 32, "width": 64},
        },
        "objects": [{
            "alignment": "center", "alpha": 1.0, "brightness": 1.0,
            "color": {"user": "schemecolor", "value": "1.0 1.0 1.0"},
            "id": 1, "image": "models/bg.json", "name": "Background",
            "origin": "32.0 16.0 0.0", "scale": "1.0 1.0 1.0",
            "size": "64.0 32.0", "solid": True, "visible": True,
        }],
        "version": 1,
    }, indent=2), encoding="utf-8")

    unpack.pack_pkg(src, root / "scene.pkg")

    (root / "project.json").write_text(json.dumps({
        "title": "Synthetic Test", "type": "scene", "version": 0,
        "file": "scene.json", "preview": "preview.png",
    }, indent=2), encoding="utf-8")

    # A small preview image.
    T.write_png(root / "preview.png", pixels)
    return root


def np_pink(width: int, height: int):
    import numpy as np
    img = np.zeros((height, width, 4), dtype=np.uint8)
    img[..., 0] = 200
    img[..., 1] = 60
    img[..., 2] = 180
    img[..., 3] = 255
    return img


def build_tex(path: Path, pixels) -> Path:
    """A .tex holding a raw RGBA8888 mipmap."""
    height, width = pixels.shape[:2]
    payload = pixels.tobytes()
    out = bytearray()
    out += b"TEXV0005\x00"
    out += b"TEXI0001\x00"
    out += struct.pack("<7I", T.TEX_FORMAT_RGBA8888, 0, width, height,
                       width, height, 0)
    out += b"TEXB0003\x00"
    out += struct.pack("<i", 1)
    out += struct.pack("<i", -1)
    out += struct.pack("<i", 1)
    out += struct.pack("<ii", width, height)
    out += struct.pack("<ii", 0, len(payload))
    out += struct.pack("<i", len(payload))
    out += payload
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_scene_graph() -> None:
    print("\n=== the scene graph ===")
    with tempfile.TemporaryDirectory() as tmp:
        project = build_synthetic_project(Path(tmp) / "project")
        src = Path(tmp) / "unpacked"
        unpack.unpack_pkg(project / "scene.pkg", src)
        scene = scene_mod.load_scene(src)
        check("the scene is read", len(scene.objects) == 1, str(len(scene.objects)))
        check("the canvas comes from the projection",
              (scene.width, scene.height) == (64, 32),
              f"{scene.width}x{scene.height}")
        obj = scene.objects[0]
        check("the texture is resolved",
              obj.texture is not None and obj.texture.name == "bg.tex",
              str(obj.texture))
        check("the background is flagged", obj.is_background)


def test_render_does_not_touch_the_source() -> None:
    print("\n=== rendering leaves the source alone ===")
    with tempfile.TemporaryDirectory() as tmp:
        project = build_synthetic_project(Path(tmp) / "project")
        src = Path(tmp) / "unpacked"
        unpack.unpack_pkg(project / "scene.pkg", src)

        before = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}
        scene = scene_mod.load_scene(src)
        canvas = compose.render_scene(scene, src,
                                      work_dir=Path(tmp) / "scratch")
        after = {p: p.stat().st_mtime_ns for p in src.rglob("*") if p.is_file()}

        check("the canvas has the right size", canvas.shape == (32, 64, 4),
              str(canvas.shape))
        check("no file was added to the source",
              set(before) == set(after),
              str(set(after) - set(before)))
        check("no file in the source was modified",
              all(before[p] == after[p] for p in before if p in after))


def test_render_matches_the_background_colour() -> None:
    print("\n=== the render shows the background ===")
    with tempfile.TemporaryDirectory() as tmp:
        project = build_synthetic_project(Path(tmp) / "project")
        src = Path(tmp) / "unpacked"
        unpack.unpack_pkg(project / "scene.pkg", src)
        scene = scene_mod.load_scene(src)
        canvas = compose.render_scene(scene, src,
                                      work_dir=Path(tmp) / "scratch")
        centre = canvas[16, 32]
        check("the centre pixel is the texture's colour",
              tuple(centre[:3]) == (200, 60, 180), str(centre))


def test_build_and_verify() -> None:
    print("\n=== building and verifying a package ===")
    if not shutil.which("orbis-pub-cmd.exe") and not (
            Path("/mnt/windows2/orbis-pub-cmd.exe").is_file()):
        skip("orbis-pub-cmd.exe is not available", "")
        return

    with tempfile.TemporaryDirectory() as tmp:
        project = build_synthetic_project(Path(tmp) / "project")
        out = Path(tmp) / "synthetic.fpkg"
        cli.convert(project, out, title="Synthetic Test")
        check("the package was written", out.is_file(),
              f"{out.stat().st_size} bytes")

        ok, problems, contents = verify_mod.verify_theme_package(out)
        check("the package is a valid theme", ok, "; ".join(problems))
        check("theme.xml is inside", "Image0/theme.xml" in contents)
        check("the preview is inside", "Image0/texture/preview.png" in contents)
        check("the backgrounds are inside",
              "Image0/texture/background/homescreen.png" in contents)
        check("icon0 is inside", "Sc0/icon0.png" in contents)
        check("pic1 is inside", "Sc0/pic1.png" in contents)


def test_real_projects() -> None:
    print("\n=== the real projects ===")
    if not PROJECTS.is_dir():
        skip("no Wallpaper Engine projects found", str(PROJECTS))
        return

    for project in sorted(PROJECTS.iterdir()):
        if not project.is_dir() or not (project / "scene.pkg").is_file():
            continue
        # Check that the project folder has what the tool expects.
        check(f"{project.name} has a scene.pkg", (project / "scene.pkg").is_file())
        has_meta = (project / "project.json").is_file()
        has_preview = any((project / name).is_file() for name in cli.PREVIEW_NAMES)
        check(f"{project.name} has a project.json", has_meta)
        check(f"{project.name} has a preview", has_preview)


def test_budgets() -> None:
    print("\n=== the console budgets ===")
    check("13 frames fit at 1280x720",
          constants.max_frames_for((1280, 720)) == 13,
          str(constants.max_frames_for((1280, 720))))
    check("24 frames fit at 960x540",
          constants.max_frames_for((960, 540)) == 24,
          str(constants.max_frames_for((960, 540))))
    check("48 frames fit at 640x360",
          constants.max_frames_for((640, 360)) == 48,
          str(constants.max_frames_for((640, 360))))
    check("the weight limit is 6 MiB", constants.RAF_SIZE_LIMIT == 6 * 1024 * 1024)
    check("the frame cap is 48", constants.MAX_FRAME_COUNT == 48)
    check("the theme category is 'ac'",
          constants.PKG_MODE_THEME["categories"] == ["ac"])
    check("the theme volume is pkg_ps4_theme",
          constants.PKG_MODE_THEME["volume_type"] == "pkg_ps4_theme")
    check("the theme tag is 2", constants.PKG_MODE_THEME["iro_tag"] == 2)


if __name__ == "__main__":
    test_scene_graph()
    test_render_does_not_touch_the_source()
    test_render_matches_the_background_colour()
    test_budgets()
    test_build_and_verify()
    test_real_projects()

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S):")
        for fail in FAILS:
            print(f"  - {fail}")
        sys.exit(1)
    print("all tests passed")
