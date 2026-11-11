"""paper2ps4 - Turn a Wallpaper Engine scene.pkg into a PS4 installable FPKG theme.

Pipeline:
    1. Unpack the source scene.pkg (PKGV/PKGM container).
    2. Classify assets: textures (.tex -> PNG via resourcecompiler64.exe),
       video (frames via ffmpeg), audio (largest track -> .at9 via ps4_at9tool.exe),
       shaders (ignored: custom shaders cannot run on a PS4 theme; their
       rendered output is already baked into the textures/frames).
    3. Build PS4 theme assets: theme.xml, param.sfo, icon0.png, pic1.png,
       preview.png, plus either a static background or an animated
       scene/background.raf/ (DXT1a frames, <= 6 MiB and <= 48 frames).
    4. Package with orbis-pub-cmd.exe (sfo_create + gp4 + img_create) -> .fpkg.

Designed to run on Linux (wine for the .exe tools) and Windows (direct).
"""

from __future__ import annotations

__version__ = "0.1.0"
