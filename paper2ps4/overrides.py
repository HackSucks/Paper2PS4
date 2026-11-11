"""System icon and sound effect replacements.

A theme may replace the console's own icons and its menu sounds. The user
points at a folder and the files inside are copied into the theme.

The folder may be laid out like the theme itself:

    overrides/
      function_icon/
        power.png          128x128, the top-menu icon
        power_glow.png     152x152, its glow variant (optional)
      content_icon/
        browser.png        512x512
      sound/
        se_decide.wav      the "confirm" sound
        se_cursor.wav      the "move cursor" sound
        se_cancel.wav      the "cancel" sound
      icon0.png            512x512, the icon in the console's menu
      pic1.png             1920x1080, the store shot

or flat, with the file names the console expects:

    overrides/
      power.png           a function icon
      browser.png         a content icon
      se_decide.wav       a sound effect
      icon0.png
      pic1.png

The file names are fixed by the console: they are the ones it looks for.
Sizes are not fixed — an image is scaled and cropped to the size the slot
needs — but the formats are: PNG for the icons, and any audio ffmpeg reads
for the effects (they are encoded to VAG).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import constants
from .media import MediaError, pcm_mono, render_png
from .vag import encode as vag_encode

#: Extensions accepted for an icon.
ICON_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tga")

#: Extensions accepted for a sound effect.
SFX_EXTS = (".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma", ".vag")


@dataclass
class IconOverride:
    """One icon the user wants to replace."""

    name: str
    source: Path
    #: The glow variant, when the user supplied one.
    glow: Path | None = None
    #: Where the icon goes inside the theme.
    path: str = ""
    size: tuple[int, int] = (0, 0)
    glow_size: tuple[int, int] | None = None


@dataclass
class SfxOverride:
    """One sound effect the user wants to replace."""

    slot: str
    source: Path
    path: str = ""
    rate: int = 48000


@dataclass
class Overrides:
    icons: list[IconOverride] = field(default_factory=list)
    sfx: list[SfxOverride] = field(default_factory=list)
    icon0: Path | None = None
    pic1: Path | None = None

    @property
    def empty(self) -> bool:
        return not (self.icons or self.sfx or self.icon0 or self.pic1)


def _find(folder: Path, stem: str, extensions: tuple[str, ...]) -> Path | None:
    """Look for `<stem>.<ext>` in `folder`."""
    for ext in extensions:
        candidate = folder / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


def load(icons_dir: Path | None, sfx_dir: Path | None) -> Overrides:
    """Read the overrides the user provided.

    `icons_dir` and `sfx_dir` may be the same folder; each one is read for
    what it can hold.
    """
    result = Overrides()

    if icons_dir is not None:
        icons_dir = Path(icons_dir)
        if not icons_dir.is_dir():
            raise FileNotFoundError(f"icon folder not found: {icons_dir}")

        # The flat layout: <name>.png next to the folder.
        for name in constants.FUNCTION_ICONS:
            source = _find(icons_dir, name, ICON_EXTS)
            if source is None:
                continue
            result.icons.append(IconOverride(
                name=name,
                source=source,
                glow=_find(icons_dir, f"{name}_glow", ICON_EXTS),
                path=f"{constants.FUNCTION_ICON_DIR}/{name}.png",
                size=constants.FUNCTION_ICON_SIZE,
                glow_size=constants.FUNCTION_ICON_GLOW_SIZE,
            ))

        for name in constants.CONTENT_ICONS:
            source = _find(icons_dir, name, ICON_EXTS)
            if source is None:
                continue
            result.icons.append(IconOverride(
                name=name,
                source=source,
                path=f"{constants.CONTENT_ICON_DIR}/{name}.png",
                size=constants.CONTENT_ICON_SIZE,
            ))

        # The layout that mirrors the theme's own folders.
        function_dir = icons_dir / "function_icon"
        if function_dir.is_dir():
            for name in constants.FUNCTION_ICONS:
                source = _find(function_dir, name, ICON_EXTS)
                if source is None:
                    continue
                result.icons.append(IconOverride(
                    name=name,
                    source=source,
                    glow=_find(function_dir, f"{name}_glow", ICON_EXTS),
                    path=f"{constants.FUNCTION_ICON_DIR}/{name}.png",
                    size=constants.FUNCTION_ICON_SIZE,
                    glow_size=constants.FUNCTION_ICON_GLOW_SIZE,
                ))

        content_dir = icons_dir / "content_icon"
        if content_dir.is_dir():
            for name in constants.CONTENT_ICONS:
                source = _find(content_dir, name, ICON_EXTS)
                if source is None:
                    continue
                result.icons.append(IconOverride(
                    name=name,
                    source=source,
                    path=f"{constants.CONTENT_ICON_DIR}/{name}.png",
                    size=constants.CONTENT_ICON_SIZE,
                ))

        result.icon0 = _find(icons_dir, "icon0", ICON_EXTS)
        result.pic1 = _find(icons_dir, "pic1", ICON_EXTS)

    if sfx_dir is not None:
        sfx_dir = Path(sfx_dir)
        if not sfx_dir.is_dir():
            raise FileNotFoundError(f"sound effect folder not found: {sfx_dir}")

        for slot in constants.SFX_SLOTS:
            source = _find(sfx_dir, slot, SFX_EXTS)
            if source is None:
                continue
            result.sfx.append(SfxOverride(
                slot=slot,
                source=source,
                path=constants.AUDIO_SLOTS[slot]["path"],
                rate=constants.AUDIO_SLOTS[slot]["rate"],
            ))

        sound_dir = sfx_dir / "sound"
        if sound_dir.is_dir():
            for slot in constants.SFX_SLOTS:
                source = _find(sound_dir, slot, SFX_EXTS)
                if source is None:
                    continue
                result.sfx.append(SfxOverride(
                    slot=slot,
                    source=source,
                    path=constants.AUDIO_SLOTS[slot]["path"],
                    rate=constants.AUDIO_SLOTS[slot]["rate"],
                ))

    # The flat layout and the folder layout can both offer the same icon;
    # the folder one wins, and each icon is only taken once.
    result.icons = _dedupe(result.icons)
    result.sfx = _dedupe(result.sfx)
    return result


def _dedupe(items: list) -> list:
    """Keep the last entry for each name (the folder layout comes last)."""
    out: dict = {}
    for item in items:
        key = getattr(item, "name", None) or getattr(item, "slot", None)
        out[key] = item
    return list(out.values())


def apply_icons(content_dir: Path, overrides: Overrides) -> list[str]:
    """Copy the icon replacements into the theme. Returns what was written."""
    written: list[str] = []
    for icon in overrides.icons:
        dest = content_dir / icon.path
        render_png(icon.source, dest, icon.size)
        written.append(icon.path)

        if icon.glow_size is not None:
            glow_dest = content_dir / f"{constants.FUNCTION_ICON_DIR}/{icon.name}_glow.png"
            if icon.glow is not None:
                render_png(icon.glow, glow_dest, icon.glow_size)
            else:
                # The glow is the same artwork, a little larger: the console
                # draws it behind the icon.
                render_png(icon.source, glow_dest, icon.glow_size)
            written.append(f"{constants.FUNCTION_ICON_DIR}/{icon.name}_glow.png")

    if overrides.icon0 is not None:
        dest = content_dir / constants.PKG_FIXED_PATHS["sce_icon"]
        render_png(overrides.icon0, dest, constants.SCE_ICON_SIZE)
        written.append(constants.PKG_FIXED_PATHS["sce_icon"])

    if overrides.pic1 is not None:
        dest = content_dir / constants.PKG_FIXED_PATHS["pic1"]
        render_png(overrides.pic1, dest, constants.PIC1_SIZE)
        written.append(constants.PKG_FIXED_PATHS["pic1"])

    return written


def apply_sfx(content_dir: Path, overrides: Overrides) -> list[str]:
    """Encode the sound effect replacements into the theme."""
    written: list[str] = []
    for effect in overrides.sfx:
        dest = content_dir / effect.path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if effect.source.suffix.lower() == ".vag":
            # Already in the console's format: use it as it is.
            dest.write_bytes(effect.source.read_bytes())
        else:
            pcm = pcm_mono(effect.source, effect.rate,
                            max_seconds=constants.MAX_SFX_SECONDS)
            dest.write_bytes(vag_encode(pcm, effect.rate, effect.slot[:15]))
        written.append(effect.path)

    return written
