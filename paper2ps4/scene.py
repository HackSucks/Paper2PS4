"""Wallpaper Engine scene resolution.

A scene is a graph of JSON files:

    scene.json  "objects" -> "image": models/<name>.json
    models/     "material": materials/<name>.json  (+ width/height)
    materials/  "passes" -> "textures": [<texture name>]

The texture name resolves to materials/<name>.tex.

To pick the background we walk that graph and score the objects: the one that
covers the screen (biggest area, not transparent, visible) and sits at the
bottom of the z-order wins. Anything else is an overlay (a sun, a character)
and would make a poor background.

Shaders are ignored on purpose: a PS4 theme cannot run custom HLSL/GLSL, and
whatever a shader did to an object is already baked into its texture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: A scene's default canvas. Wallpaper Engine renders 1920x1080 by default.
DEFAULT_SCENE_WIDTH = 1920
DEFAULT_SCENE_HEIGHT = 1080


@dataclass
class SceneObject:
    """One object of the scene, with its texture and transform resolved."""

    name: str
    texture: Path | None
    width: int
    height: int
    visible: bool
    index: int
    is_background: bool = False
    is_solid: bool = False
    shader: str | None = None
    passes: list[dict] = field(default_factory=list)
    #: The quad's size before the object's scale is applied.
    base_width: float = 0.0
    base_height: float = 0.0
    base_height_scale_x: float = 1.0
    base_height_scale_y: float = 1.0
    #: Where the object sits, in scene units (1 unit = 1 pixel).
    origin: tuple[float, float] = (0.0, 0.0)
    alignment: str = "center"
    alpha: float = 1.0
    tint: tuple[float, float, float] | None = None
    brightness: float = 1.0


@dataclass
class Scene:
    objects: list[SceneObject] = field(default_factory=list)
    width: int = DEFAULT_SCENE_WIDTH
    height: int = DEFAULT_SCENE_HEIGHT

    @property
    def backgrounds(self) -> list[SceneObject]:
        return [o for o in self.objects if o.is_background]

    @property
    def animated(self) -> list[SceneObject]:
        """Objects whose texture is a GIF or a video."""
        return [o for o in self.objects
                if o.texture and o.texture.suffix.lower() == ".tex"]


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def _as_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        # Wallpaper Engine allows a user property: {"user": "x", "value": true}
        return _as_bool(value.get("value"), default)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "")
    return default


def _parse_vec(value, default=(0.0, 0.0)) -> tuple[float, float]:
    if isinstance(value, str):
        parts = value.split()
        try:
            return float(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            return default
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return default
    return default


def _parse_vec3(value, default=(0.0, 0.0, 0.0)) -> tuple[float, float, float]:
    """Parse a colour vector: "r g b" or a three-element list."""
    if isinstance(value, str):
        parts = value.split()
        try:
            return float(parts[0]), float(parts[1]), float(parts[2])
        except (IndexError, ValueError):
            return default
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return float(value[0]), float(value[1]), float(value[2])
        except (TypeError, ValueError):
            return default
    return default


def load_scene(src_dir: Path) -> Scene:
    """Parse scene.json and resolve every object's texture."""
    scene_json = src_dir / "scene.json"
    if not scene_json.is_file():
        return Scene()

    data = _read_json(scene_json)
    general = data.get("general") or {}
    projection = general.get("orthogonalprojection") or {}
    scene = Scene(
        width=int(projection.get("width") or DEFAULT_SCENE_WIDTH),
        height=int(projection.get("height") or DEFAULT_SCENE_HEIGHT),
    )

    for index, obj in enumerate(data.get("objects") or []):
        if not isinstance(obj, dict):
            continue

        model_path = src_dir / str(obj.get("image") or "")
        model = _read_json(model_path) if model_path.is_file() else {}

        material_path = src_dir / str(model.get("material") or "")
        material = _read_json(material_path) if material_path.is_file() else {}

        # A model's size overrides the object's when present, but the object's
        # own scale still applies on top of it: the rendered size is what
        # decides how much of the screen the object covers.
        base_w, base_h = _parse_vec(obj.get("size"), (0.0, 0.0))
        if not base_w or not base_h:
            # "autosize" models take the texture's own size.
            base_w = float(model.get("width") or 0)
            base_h = float(model.get("height") or 0)
        if not base_w or not base_h:
            base_w = float(scene.width)
            base_h = float(scene.height)

        scale_w, scale_h = _parse_vec(obj.get("scale"), (1.0, 1.0))
        rendered_w = int(round(base_w * abs(scale_w))) or int(base_w)
        rendered_h = int(round(base_h * abs(scale_h))) or int(base_h)

        origin_x, origin_y = _parse_vec(obj.get("origin"), (0.0, 0.0))
        origin_z = 0.0
        if isinstance(obj.get("origin"), str):
            parts = obj["origin"].split()
            if len(parts) > 2:
                try:
                    origin_z = float(parts[2])
                except ValueError:
                    origin_z = 0.0

        tint: tuple[float, float, float] | None = None
        color = obj.get("color")
        if isinstance(color, dict):
            color = color.get("value")
        if color is not None:
            rgb = _parse_vec3(color, (1.0, 1.0, 1.0))
            if rgb != (1.0, 1.0, 1.0):
                tint = rgb

        brightness = 1.0
        raw_brightness = obj.get("brightness")
        if isinstance(raw_brightness, dict):
            raw_brightness = raw_brightness.get("value")
        if isinstance(raw_brightness, (int, float)):
            brightness = float(raw_brightness)

        alpha = 1.0
        raw_alpha = obj.get("alpha")
        if isinstance(raw_alpha, dict):
            raw_alpha = raw_alpha.get("value")
        if isinstance(raw_alpha, (int, float)):
            alpha = float(raw_alpha)

        texture: Path | None = None
        shader: str | None = None
        passes = material.get("passes") or []
        for entry in passes:
            if not isinstance(entry, dict):
                continue
            shader = shader or entry.get("shader")
            names = entry.get("textures") or []
            if not names:
                continue
            name = str(names[0])
            # The texture lives next to the material file.
            candidate = material_path.parent / f"{name}.tex"
            if not candidate.is_file():
                candidate = src_dir / "materials" / f"{name}.tex"
            if candidate.is_file():
                texture = candidate
                break

        scene.objects.append(SceneObject(
            name=str(obj.get("name") or f"object{index}"),
            texture=texture,
            width=rendered_w,
            height=rendered_h,
            visible=_as_bool(obj.get("visible"), True),
            index=index,
            is_solid=_as_bool(obj.get("solid"), False),
            shader=shader,
            passes=passes,
            base_width=base_w,
            base_height=base_h,
            base_height_scale_x=abs(scale_w),
            base_height_scale_y=abs(scale_h),
            origin=(origin_x, origin_y),
            alignment=str(obj.get("alignment") or "center"),
            alpha=alpha,
            tint=tint,
            brightness=brightness,
        ))

    _mark_backgrounds(scene)
    return scene


def _mark_backgrounds(scene: Scene) -> None:
    """Flag the object that acts as the wallpaper background.

    The background is the bottom-most visible object that covers the whole
    scene. Overlays (a sun, a character) sit on top of it and cover less, so
    they lose. When nothing covers the scene completely, the largest object
    available is used.
    """
    if not scene.objects:
        return

    scene_area = float(scene.width * scene.height)
    candidates: list[tuple[float, int, SceneObject]] = []

    for obj in scene.objects:
        if not obj.visible or not obj.texture:
            continue
        coverage = (obj.width * obj.height) / scene_area if scene_area else 0.0
        candidates.append((coverage, obj.index, obj))

    if not candidates:
        return

    # Objects that cover the whole scene are the real backdrop. Among them
    # the first one wins: Wallpaper Engine draws in order, so the earliest
    # full-screen object is the one everything else is drawn on top of.
    full = [c for c in candidates if c[0] >= 0.98]
    if full:
        full.sort(key=lambda c: c[1])
        chosen = full[0][2]
    else:
        # Nothing covers the scene: take the biggest one.
        chosen = max(candidates, key=lambda c: (c[0], -c[1]))[2]

    chosen.is_background = True


def background_texture(scene: Scene) -> Path | None:
    """The texture to use as the theme's static background, if any."""
    for obj in scene.objects:
        if obj.is_background and obj.texture:
            return obj.texture
    return None


def all_textures(scene: Scene) -> list[Path]:
    """Every distinct texture referenced by the scene, in z-order."""
    out: list[Path] = []
    seen: set[Path] = set()
    for obj in scene.objects:
        if obj.texture and obj.texture not in seen:
            seen.add(obj.texture)
            out.append(obj.texture)
    return out
