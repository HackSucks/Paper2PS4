"""theme.xml generator -- the descriptor the PS4 reads when applying a theme.

Borrowed and adapted from PS4-Ultimate-Theme-Creator/app/theme_xml.py (MIT).

format-version and themecolor are mandatory; without theme.xml the console
crashes when applying the theme.
"""

from __future__ import annotations

import re

FORMAT_VERSION = "4.0"

DEFAULTS = {
    "themecolor": 0,
    "fontcolor": "#FFFFFFFF",
    "fontshadowcolor": "#FF000000",
    "focuscolor": "#FF1E9BFF",
    "homescreen_dimmer": "#00FFFFFF",
    "functionscreen_dimmer": "#00FFFFFF",
    "titlename_dimmer": "#00FFFFFF",
    "homebgm_enable": False,
}

ARGB_RE = re.compile(r"^#[0-9A-Fa-f]{8}$")


#: The eight built-in PS4 theme colours, in the order theme.xml numbers them.
#: Index 0 is the console's default blue. This order is fixed by the console:
#: an index that is off by one applies the wrong colour, not a missing one.
THEME_COLOR_NAMES = (
    "blue", "pink", "red", "navy", "dark gray", "gold",
    "light steel blue", "purple",
)

# Approximate representatives of the console's colours, used only to map a
# wallpaper's own colour onto one of the eight. The console draws the real
# palette; these are close enough to pick the right index.
THEME_COLOR_SWATCHES = (
    (0.04, 0.20, 0.53),   # 0 blue (default)
    (0.90, 0.13, 0.55),   # 1 pink
    (0.82, 0.15, 0.18),   # 2 red
    (0.10, 0.16, 0.30),   # 3 navy
    (0.29, 0.29, 0.30),   # 4 dark gray
    (0.79, 0.62, 0.16),   # 5 gold
    (0.69, 0.77, 0.87),   # 6 light steel blue
    (0.40, 0.20, 0.58),   # 7 purple
)


def themecolor_name(index: int) -> str:
    """Human-readable name of a theme colour index."""
    return THEME_COLOR_NAMES[index] if 0 <= index < len(THEME_COLOR_NAMES) else "?"


def nearest_themecolor(rgb) -> int:
    """The PS4 theme colour closest to an (r, g, b) triple in 0..1."""
    return min(
        range(len(THEME_COLOR_SWATCHES)),
        key=lambda index: sum(
            (rgb[channel] - THEME_COLOR_SWATCHES[index][channel]) ** 2
            for channel in range(3)
        ),
    )


def _parse_rgb(value) -> tuple[float, float, float] | None:
    """Parse a Wallpaper Engine colour ("0.1 0.2 0.3", "#RRGGBB", list)."""
    if isinstance(value, dict):
        value = value.get("value")

    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()
        if value.startswith("#"):
            value = value[-6:]
            if len(value) != 6:
                return None
            try:
                rgb = tuple(int(value[i:i + 2], 16) / 255
                            for i in (0, 2, 4))
            except ValueError:
                return None
        else:
            try:
                rgb = tuple(float(part) for part in value.split())
            except ValueError:
                return None
    elif isinstance(value, (tuple, list)) and len(value) >= 3:
        try:
            rgb = tuple(float(part) for part in value[:3])
        except (TypeError, ValueError):
            return None
    else:
        return None

    if len(rgb) < 3:
        return None

    if any(component > 1 for component in rgb):
        rgb = tuple(component / 255 for component in rgb)

    if any(component < 0 for component in rgb):
        return None

    return rgb[0], rgb[1], rgb[2]


def infer_themecolor(value) -> int | None:
    """Choose the PS4 theme colour nearest to a Wallpaper Engine colour.

    Returns None when the project carries no usable colour (missing, all
    black, or unparsable), so the caller can fall back to the artwork.
    """
    rgb = _parse_rgb(value)
    if rgb is None:
        return None
    # A schemecolor of pure black is Wallpaper Engine's "not set" value.
    if max(rgb) < 0.02:
        return None
    return nearest_themecolor(rgb)


def infer_themecolor_from_image(image) -> int | None:
    """Pick a theme colour from the artwork itself.

    Uses the most saturated quarter of the pixels (weighted by brightness) so
    a mostly dark scene still selects its accent light rather than its
    background, then maps that colour onto the console's palette.
    """
    try:
        import numpy as np

        from .media import image_to_rgba

        sample = image_to_rgba(image, (64, 64))[..., :3].astype(np.float32)
    except Exception:
        return None

    if sample.size == 0:
        return None
    sample /= 255.0

    highest = sample.max(axis=2)
    lowest = sample.min(axis=2)
    saturation = np.where(highest > 0, (highest - lowest) / np.maximum(highest, 1e-6), 0.0)
    visible = sample[highest > 0.05]
    if visible.size == 0:
        return None

    visible_saturation = saturation[highest > 0.05]
    # Keep the vivid part of the image: the average of everything is usually
    # a muddy grey that matches no theme colour at all.
    cutoff = np.quantile(visible_saturation, 0.75)
    chosen = visible[visible_saturation >= cutoff]
    if chosen.size == 0:
        chosen = visible

    rgb = tuple(float(component) for component in chosen.mean(axis=0))
    if max(rgb) < 0.02:
        return None
    return nearest_themecolor(rgb)


class ThemeXmlError(ValueError):
    """Invalid theme.xml setting."""


def sanitize_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "-", (value or "").strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:64] or "custom-theme"


def _check_argb(value: str, field: str) -> str:
    if not ARGB_RE.match(value or ""):
        raise ThemeXmlError(
            f"{field}: expected an 8-digit ARGB colour like #FF1E9BFF, got '{value}'."
        )
    return value.upper()


def build_theme_xml(*, label: str, themecolor: int = DEFAULTS["themecolor"],
                    fontcolor: str = DEFAULTS["fontcolor"],
                    fontshadowcolor: str = DEFAULTS["fontshadowcolor"],
                    focuscolor: str = DEFAULTS["focuscolor"],
                    homescreen_dimmer: str = DEFAULTS["homescreen_dimmer"],
                    functionscreen_dimmer: str = DEFAULTS["functionscreen_dimmer"],
                    titlename_dimmer: str = DEFAULTS["titlename_dimmer"],
                    homebgm_enable: bool = DEFAULTS["homebgm_enable"]) -> str:
    try:
        color = int(themecolor)
    except (TypeError, ValueError) as exc:
        raise ThemeXmlError(f"themecolor must be a number 0-7, got '{themecolor}'.") from exc
    if not 0 <= color <= 7:
        raise ThemeXmlError(f"themecolor must be between 0 and 7, got {color}.")

    rows = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<theme>",
        f"    <format-version>{FORMAT_VERSION}</format-version>",
        f"    <themecolor>{color}</themecolor>",
        f"    <label>{sanitize_label(label)}</label>",
        f'    <fontcolor>{_check_argb(fontcolor, "fontcolor")}</fontcolor>',
        f'    <fontshadowcolor>{_check_argb(fontshadowcolor, "fontshadowcolor")}</fontshadowcolor>',
        f'    <focuscolor>{_check_argb(focuscolor, "focuscolor")}</focuscolor>',
        f'    <homescreen-dimmer>{_check_argb(homescreen_dimmer, "homescreen-dimmer")}</homescreen-dimmer>',
        f'    <functionscreen-dimmer>{_check_argb(functionscreen_dimmer, "functionscreen-dimmer")}</functionscreen-dimmer>',
        f'    <titlename-dimmer>{_check_argb(titlename_dimmer, "titlename-dimmer")}</titlename-dimmer>',
        f'    <homebgm-enable>{"True" if homebgm_enable else "False"}</homebgm-enable>',
        "</theme>",
        "",
    ]
    return "\n".join(rows)
