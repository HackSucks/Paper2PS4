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
