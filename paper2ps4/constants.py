"""Shared constants for paper2ps4.

Mostly mirrors the verified PS4 limits from PS4-Ultimate-Theme-Creator/app/config.py
so the packages we build actually install and apply on a real console.
"""

from __future__ import annotations

from pathlib import Path

# --- Resolutions the PS4 requires -----------------------------------------
STATIC_SIZE = (1920, 1080)      # background.dds / homescreen
ANIMATED_SIZE = (1280, 720)     # default animated background resolution
ICON_SIZE = (128, 128)          # function icon
SCE_ICON_SIZE = (512, 512)      # icon0.png (content icon in sce_sys)
PIC1_SIZE = (1920, 1080)        # pic1.png (store shot)
PREVIEW_SIZE = (740, 416)       # list thumbnail

#: Smallest preview image that is still worth using as the thumbnail. The
#: workshop's own previews are often tiny, and blowing those up looks worse
#: than using the rendered background.
PREVIEW_MIN_WIDTH = 480

# Animated background resolutions offered (lower res = more frames fit).
ANIMATED_SIZES = [(1280, 720), (960, 540), (640, 360)]

# --- Hard limit on the background.raf folder ------------------------------
RAF_SIZE_LIMIT = 6 * 1024 * 1024  # 6 MiB (measured on console)
MAX_FRAME_COUNT = 48              # frame-count cap (measured on console)

# --- Animation timing -----------------------------------------------------
DEFAULT_WAIT_SECONDS = 0.1
MIN_WAIT_SECONDS = 0.02
MAX_WAIT_SECONDS = 5.0

# --- Paths inside the theme -----------------------------------------------
RAF_DIR_NAME = "background.raf"
RAF_PARENT = "scene"
THEME_PATHS = {
    "homescreen": "texture/background/homescreen.png",
    "functionscreen": "texture/background/functionscreen.png",
    "preview": "texture/preview.png",
    "theme_xml": "theme.xml",
    "raf_dir": RAF_PARENT + "/" + RAF_DIR_NAME,
}
PKG_FIXED_PATHS = {
    "sce_icon": "sce_sys/icon0.png",
    "pic1": "sce_sys/pic1.png",
}

# --- Packaging ------------------------------------------------------------
# volume_type=pkg_ps4_theme + IRO_TAG=2 makes the console treat the package
# as a theme (not an app). CATEGORY must be 'ac' (additional content).
PKG_MODE_THEME = {
    "volume_type": "pkg_ps4_theme",
    "iro_tag": 2,
    "keystone": False,
    "extra_package_attrs": {},
    "chunk_info": False,
    "categories": ["ac"],
}
PKG_DEFAULTS = {
    "mode": "theme",
    "category": "ac",
    "passcode": "0" * 32,
    "content_prefix": "UP9000",
    "title_id": "CUSA00000",
}

# Audio slots a PS4 theme supports.
AUDIO_SLOTS = {
    "bgm": {
        "path": "sound/bgm_home.at9",
        "codec": "atrac9",
        "rate": 48000,
    },
    "se_decide": {"path": "sound/se_decide.vag"},
    "se_cursor": {"path": "sound/se_cursor.vag"},
    "se_cancel": {"path": "sound/se_cancel.vag"},
}

#: How much audio to keep for the BGM (seconds). Themes loop menu music.
BGM_MAX_SECONDS = 300

#: Loudness the menu music is levelled to, in LUFS. A retail theme's music
#: sits well below a normally mastered song, which would otherwise drown out
#: the rest of the console's sounds.
BGM_TARGET_LUFS = -20.0

#: ATRAC9 bitrate that actually plays on a PS4 theme (measured).
AT9_BITRATE = 144
AT9_SAMPLE_RATE = 48000

# --- Wallpaper Engine asset classification --------------------------------
TEXTURE_EXTS = {".tex"}
SHADER_EXTS = {".vert", ".frag", ".geom", ".comp", ".hlsl", ".fx", ".ps", ".vs"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".m4v", ".gif", ".apng", ".wmv", ".flv"}
AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus", ".wma", ".at9"}

#: Tool names, in preference order.
ORBIS_PUB_CMD_NAMES = ("orbis-pub-cmd.exe",)
AT9_TOOL_NAMES = ("ps4_at9tool.exe", "at9tool.exe")

# Where the Windows helper tools live. On Linux they run under wine.
TOOL_SEARCH_DIRS = (
    Path("/mnt/windows2"),
    Path("/mnt/windows2/ext"),
)


def dds_bytes_for(width: int, height: int) -> int:
    """Exact size of a DXT1 .dds without mipmaps (fixed-rate)."""
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    return 128 + blocks * 8


def max_frames_for(size: tuple[int, int]) -> int:
    """How many frames of that resolution the console accepts."""
    return min(RAF_SIZE_LIMIT // dds_bytes_for(*size), MAX_FRAME_COUNT)


MAX_FRAMES = max_frames_for(ANIMATED_SIZE)
