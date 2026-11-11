# paper2ps4

Turn a Wallpaper Engine project into a PS4 installable FPKG theme.

Point it at the project folder Wallpaper Engine downloaded, wait, and you get
a `.fpkg` you can install on a PS4.

```bash
git clone <this repo> && cd paper2ps4
pip install -r requirements.txt

python3 -m paper2ps4 ~/wallpapers/projects/2521462823 -o out/niko.fpkg
```

That is the whole thing. The project folder holds:

```
2521462823/
  project.json     the title and the wallpaper's properties
  preview.jpg      the author's preview image
  scene.pkg        the scene itself
```

and the result is an installable theme.

## Requirements

- Python 3.10+ with `numpy` (`pip install -r requirements.txt`)
- `ffmpeg` and `ffprobe` on PATH
- wine (Linux only) for the Windows helper tools
- The proprietary Sony helpers, which are **not** bundled (they are in the
  "PS4 Fake PKG Tools" download and Sony's ATRAC9 SDK):
  - `orbis-pub-cmd.exe` — packaging (required)
  - `ext/sc.exe`, `ext/di.exe`, `ext/libatrac9.dll` — its dependencies
  - `ps4_at9tool.exe` — menu music to ATRAC9 (optional; without it the theme
    is silent)

They are looked for in `/mnt/windows2` and on PATH.

## What it does

1. **Unpacks** `scene.pkg` (the PKGV/PKGM container).
2. **Renders the scene.** The scene's objects are composited the way
   Wallpaper Engine draws them: each textured quad is placed, scaled and
   tinted as the scene says, over the cleared background. The result is the
   wallpaper's own artwork.
3. **Builds the theme**: `theme.xml`, `param.sfo`, `icon0.png`, `pic1.png`,
   `preview.png`, the backgrounds (DXT1a), the menu music (ATRAC9) and,
   for a video wallpaper, the animated `scene/background.raf/`.
4. **Packages** with `orbis-pub-cmd.exe` into a `.fpkg`.

## The .tex decoder

Wallpaper Engine's textures are `.tex` files: a chunked container with
LZ4-compressed mipmaps, in RGBA8888, R8, RG88, DXT1, DXT3 or DXT5, plus
GIF sprite sheets and embedded MP4s.

This project decodes them natively (`paper2ps4/tex.py`), so no extra tool is
needed and the result is the same on Linux and Windows. The format was
reverse engineered by the RePKG project; the decoder here is an independent
implementation, checked against ffmpeg's BC1/BC3 decoders and against
Wallpaper Engine's own exported PNGs.

## Options

```
python3 -m paper2ps4 <project> -o out.fpkg [options]

  -t, --title TEXT        theme title (default: the title in project.json)
  --title-id ID           title ID (default: CUSA00000)
  --content-id ID         content ID (default: generated from the title)
  --category CAT          package category (default: ac)
  --passcode PASSCODE     32-character passcode (default: 32 zeros)
  --animated              force an animated theme
  --frames N              number of animated frames (default: the console's cap)
  --size WxH              animated resolution (default: 1280x720)
  --wait SECONDS          seconds per animated frame (default: 0.1)
  --audio FILE            use only this audio file as the music
  --no-music              build the theme without music
  --keep-work             keep the intermediate working folder
  --verify                check the finished package and list what is inside
```

## Design decisions

**Multiple audio files.** Wallpaper Engine scenes often ship a whole playlist
of tracks. A PS4 theme has room for a single menu music
(`sound/bgm_home.at9`), so the tracks are joined into one file and that is
what gets encoded to ATRAC9 (144 kbps, 48 kHz, looped). The music is also
levelled down first: a normally mastered song sits well above a retail
theme's and would drown out the rest of the console.

**Shaders.** Custom HLSL/GLSL cannot run on a PS4 theme, which uses
pre-rendered DXT1a frames on a textured plane. The shader sources are
ignored; their artwork is already baked into the textures. What is missing is
the animation of the shader effects, not the picture.

**The preview image.** It becomes the theme's thumbnail, which is what the
console shows in the theme list. Workshop previews are often tiny, so a
preview smaller than 740 px falls back to the rendered background.

**The console's limits.** These are measured on a real console and are
enforced, because going over them makes the theme install but fail to apply:

| | |
|---|---|
| `scene/background.raf/` weight | ≤ 6 MiB |
| frames per scene | ≤ 48 |
| resolution vs frames | 13 at 1280×720, 24 at 960×540, 48 at 640×360 |
| `volume_type` + `IRO_TAG` | `pkg_ps4_theme` + `2` |
| `CATEGORY` | `ac` |

## Tests

```bash
python3 tests/test_tex.py      # the .tex decoder
python3 tests/test_theme.py    # the scene renderer and the packaging
```

The texture tests check the decoder against ffmpeg's independent BC1/BC3
decoders and against Wallpaper Engine's own PNGs, when the projects are on
this machine.

## Credits

- The DXT1 encoder, the `.mdx` model writer, the RAF scene generator, the
  `theme.xml` writer, the VAG encoder and the packaging code are adapted from
  [PS4-Ultimate-Theme-Creator](https://github.com/Imxnxl/PS4-Ultimate-Theme-Creator)
  (MIT).
- The `.tex` format was reverse engineered by the
  [RePKG](https://github.com/notbribe/RePKG) project.
- The PKGV/PKGM container handling comes from `we_mobile_convert.py`.
