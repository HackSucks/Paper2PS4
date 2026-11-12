"""paper2ps4 - Turn a Wallpaper Engine scene.pkg into a PS4 installable FPKG theme.

Pipeline:
    1. Unpack the source scene.pkg (PKGV/PKGM container).
    2. Render the scene. A still scene is composited the way Wallpaper Engine
       draws it; a scene that animates (effects, particles, puppetry or custom
       shaders) is rendered with linux-wallpaperengine and captured as a frame
       sequence; a video wallpaper is read directly.
    3. Build PS4 theme assets: theme.xml, param.sfo, icon0.png, preview.png,
       plus either a static background or an animated scene/background.raf/
       (DXT1a frames, <= 6 MiB and <= 48 frames). Textures are decoded
       natively (.tex), and the scene's audio tracks are joined into one
       menu music (.at9 via ps4_at9tool.exe).
    4. Package with orbis-pub-cmd.exe (sfo_create + gp4 + img_create) -> .fpkg.

Designed to run on Linux (wine for the .exe tools) and Windows (direct).
"""

from __future__ import annotations

__version__ = "0.1.0"
