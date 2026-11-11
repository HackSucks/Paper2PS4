"""Render a Wallpaper Engine scene into a flat image.

Wallpaper Engine draws a scene as a stack of textured quads over a cleared
background. Reproducing it means walking the objects in order and compositing
each one's texture at the position the scene gives it:

  * the projection size is the canvas (1 scene unit = 1 pixel)
  * an object's quad is `size` scaled by `scale`
  * the quad is anchored on `origin` according to `alignment`
  * `alpha`, `color` (tint) and `brightness` modulate it

Shaders, particles and the effect layers are not reproduced: they need a
real renderer. What comes out is the scene's geometry and colour, which is
what the console's background shows.

The scene's preview image (preview.jpg / preview.gif next to the .pkg) is
the author's own render and is used as the theme's thumbnail.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import tex as tex_mod
from .scene import Scene, SceneObject, _parse_vec, _parse_vec3, _read_json


class ComposeError(Exception):
    """The scene could not be rendered."""


@dataclass
class RenderOptions:
    #: Cap on the rendering resolution. The console shows 1920x1080, so
    #: anything above that is wasted work.
    max_width: int = 3840
    max_height: int = 2160
    #: Blend overlays onto the background.
    composite_overlays: bool = True


def _alignment_offset(alignment: str | None, width: float, height: float) -> tuple[float, float]:
    """Where the origin sits inside the quad, for the given alignment."""
    alignment = (alignment or "center").strip().lower()
    if alignment in ("topleft", "top_left", "lefttop"):
        return 0.0, 0.0
    if alignment == "top":
        return width / 2.0, 0.0
    if alignment in ("topright", "top_right", "righttop"):
        return width, 0.0
    if alignment in ("left", "centerleft", "leftcenter"):
        return 0.0, height / 2.0
    if alignment in ("right", "centerright", "rightcenter"):
        return width, height / 2.0
    if alignment in ("bottomleft", "bottom_left", "leftbottom"):
        return 0.0, height
    if alignment == "bottom":
        return width / 2.0, height
    if alignment in ("bottomright", "bottom_right", "rightbottom"):
        return width, height
    return width / 2.0, height / 2.0


def _resize_bilinear(img: np.ndarray, width: int, height: int) -> np.ndarray:
    """Resize an RGBA image with a bilinear filter (no external tools)."""
    src_h, src_w = img.shape[:2]
    if src_w == width and src_h == height:
        return img

    # Sample coordinates at the centre of each destination pixel.
    xs = np.clip((np.arange(width) + 0.5) * (src_w / width) - 0.5, 0, src_w - 1)
    ys = np.clip((np.arange(height) + 0.5) * (src_h / height) - 0.5, 0, src_h - 1)

    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, src_w - 1)
    y1 = np.minimum(y0 + 1, src_h - 1)
    wx = (xs - x0).astype(np.float32)[None, :, None]
    wy = (ys - y0).astype(np.float32)[:, None, None]

    src = img.astype(np.float32)
    # Gather the four neighbouring pixels: (height, width, 4).
    p00 = src[y0[:, None], x0[None, :]]
    p01 = src[y0[:, None], x1[None, :]]
    p10 = src[y1[:, None], x0[None, :]]
    p11 = src[y1[:, None], x1[None, :]]

    top = p00 * (1.0 - wx) + p01 * wx
    bottom = p10 * (1.0 - wx) + p11 * wx
    out = top * (1.0 - wy) + bottom * wy
    return np.clip(out, 0, 255).astype(np.uint8)


def _blend_alpha_over(dst: np.ndarray, src: np.ndarray, alpha: float) -> None:
    """Alpha-blend `src` onto `dst` in place (both RGBA, premultiplied by
    the source's own alpha)."""
    src_a = (src[..., 3:4].astype(np.float32) / 255.0) * alpha
    dst_a = dst[..., 3:4].astype(np.float32) / 255.0

    out_a = src_a + dst_a * (1.0 - src_a)
    safe = np.maximum(out_a, 1e-6)

    out_rgb = (src[..., :3].astype(np.float32) * src_a
               + dst[..., :3].astype(np.float32) * dst_a * (1.0 - src_a)) / safe
    dst[..., :3] = np.clip(out_rgb, 0, 255).astype(np.uint8)
    dst[..., 3:4] = np.clip(out_a * 255.0, 0, 255).astype(np.uint8)


def load_texture(path: Path, cache: dict, work_dir: Path | None = None) -> np.ndarray | None:
    """Decode a texture into RGBA (cached)."""
    key = str(path)
    if key in cache:
        return cache[key]

    pixels: np.ndarray | None = None
    try:
        t = tex_mod.parse_tex(path)
        if t.is_gif:
            frames = tex_mod.decode_gif_frames(t)
            pixels = frames[0] if frames else None
        elif t.is_video:
            pixels = None
        elif t.is_raw_format:
            pixels = tex_mod.decode_image(t, t.images[0])
        else:
            # An embedded PNG/JPEG: decode it with ffmpeg into RGBA.
            from .media import FFMPEG, require_tools, _run
            require_tools()
            payload, ext = tex_mod.extract_encoded(t, t.images[0])
            # The scratch file goes to the work folder, never next to the
            # source: the source folder holds Wallpaper Engine's own files.
            if work_dir is not None:
                work_dir.mkdir(parents=True, exist_ok=True)
                tmp = work_dir / (path.stem + ext)
            else:
                tmp = Path(tempfile.mkdtemp(prefix="paper2ps4-")) / (path.stem + ext)
            tmp.write_bytes(payload)
            raw = _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(tmp),
                        "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_stdout=True)
            tmp.unlink(missing_ok=True)
            if work_dir is None:
                tmp.parent.rmdir(missing_ok=True)
            height = t.image_height or t.texture_height
            width = t.image_width or t.texture_width
            if len(raw) >= width * height * 4:
                pixels = np.frombuffer(raw[:width * height * 4],
                                       dtype=np.uint8).reshape(height, width, 4)
    except Exception:
        pixels = None

    cache[key] = pixels
    return pixels


def render_scene(scene: Scene, src_dir: Path, options: RenderOptions | None = None,
                 work_dir: Path | None = None) -> np.ndarray:
    """Render the scene into an RGBA array."""
    options = options or RenderOptions()

    width = scene.width or 1920
    height = scene.height or 1080

    # Cap the working resolution.
    scale = min(1.0, options.max_width / width, options.max_height / height)
    canvas_w = max(1, int(round(width * scale)))
    canvas_h = max(1, int(round(height * scale)))

    # The cleared background.
    general = _read_json(src_dir / "scene.json").get("general") or {}
    clear = general.get("clearcolor")
    clear_rgb = _parse_vec3(clear, (0.0, 0.0, 0.0))
    canvas = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    canvas[..., 0] = int(np.clip(clear_rgb[0], 0, 1) * 255)
    canvas[..., 1] = int(np.clip(clear_rgb[1], 0, 1) * 255)
    canvas[..., 2] = int(np.clip(clear_rgb[2], 0, 1) * 255)
    canvas[..., 3] = 255

    cache: dict = {}
    for obj in scene.objects:
        if obj.texture is None or not obj.visible:
            continue
        if not options.composite_overlays and not obj.is_background:
            continue

        pixels = load_texture(obj.texture, cache, work_dir)
        if pixels is None:
            continue

        layer = _render_object(obj, pixels, scale, canvas_w, canvas_h)
        if layer is not None:
            _blend_alpha_over(canvas, layer, 1.0)

    return canvas


def _render_object(obj: SceneObject, pixels: np.ndarray, scale: float,
                    canvas_w: int, canvas_h: int) -> np.ndarray | None:
    """Place one object's texture on a canvas-sized transparent layer."""
    # The quad's size, in scene units, then scaled onto the canvas.
    quad_w = obj.base_width * obj.base_height_scale_x
    quad_h = obj.base_height * obj.base_height_scale_y
    if quad_w <= 0 or quad_h <= 0:
        return None

    dest_w = max(1, int(round(quad_w * scale)))
    dest_h = max(1, int(round(quad_h * scale)))
    layer_img = _resize_bilinear(pixels, dest_w, dest_h)

    # Position: the origin anchors the quad according to the alignment.
    origin_x, origin_y = obj.origin
    anchor_x, anchor_y = _alignment_offset(obj.alignment, dest_w, dest_h)
    left = int(round(origin_x * scale - anchor_x))
    top = int(round(origin_y * scale - anchor_y))

    # Clip to the canvas.
    src_left = max(0, -left)
    src_top = max(0, -top)
    dst_left = max(0, left)
    dst_top = max(0, top)
    copy_w = min(dest_w - src_left, canvas_w - dst_left)
    copy_h = min(dest_h - src_top, canvas_h - dst_top)
    if copy_w <= 0 or copy_h <= 0:
        return None

    layer = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    layer[dst_top:dst_top + copy_h, dst_left:dst_left + copy_w] = \
        layer_img[src_top:src_top + copy_h, src_left:src_left + copy_w]

    # Modulate: tint, brightness, alpha.
    if obj.tint is not None:
        tint = np.array(obj.tint, dtype=np.float32).reshape(1, 1, 3)
        layer[..., :3] = np.clip(layer[..., :3].astype(np.float32) * tint,
                                 0, 255).astype(np.uint8)
    if obj.brightness is not None and obj.brightness != 1.0:
        layer[..., :3] = np.clip(layer[..., :3].astype(np.float32) * obj.brightness,
                                 0, 255).astype(np.uint8)
    if obj.alpha is not None and obj.alpha != 1.0:
        layer[..., 3] = np.clip(layer[..., 3].astype(np.float32) * obj.alpha,
                                0, 255).astype(np.uint8)

    return layer


def write_render(canvas: np.ndarray, out_png: Path) -> Path:
    return tex_mod.write_png(Path(out_png), canvas)
