# paper2ps4

Turn a Wallpaper Engine project into a PS4 installable FPKG theme.

Point it at the project folder Wallpaper Engine downloaded, wait, and you get
a `.fpkg` you can install on a PS4.

```bash
git clone <this repo> && cd paper2ps4
pip install -r requirements.txt

python3 -m paper2ps4 ~/wallpapers/projects/2521462823 -o out/niko.pkg
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
- To animate a scene that is more than a video: `Xvfb` and the bundled
  `linux-wallpaperengine` (both Linux only; `apt install xvfb`). Without
  them the scene is still rendered as a still image, which installs fine.
- The proprietary Sony helpers, which are **not** bundled (they are in the
  "PS4 Fake PKG Tools" download and Sony's ATRAC9 SDK):
  - `orbis-pub-cmd.exe` — packaging (required)
  - `ext/sc.exe`, `ext/di.exe`, `ext/libatrac9.dll` — its dependencies, in an
    `ext/` folder next to it
  - `ps4_at9tool.exe` — menu music to ATRAC9 (optional; without it the theme
    is silent)

### The scene renderer

A PS4 theme cannot run Wallpaper Engine's shaders, puppetry or particles.
Rather than throw the animation away, paper2ps4 renders the real scene with
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) on a
private virtual display and captures the frames for the console's RAF scene.
A copy is bundled in `vendor/linux-wallpaperengine/` (see
[vendor/linux-wallpaperengine/README.md](vendor/linux-wallpaperengine/README.md)); it can also be installed on PATH or
selected with `--wpe DIR`.

On Windows the renderer is not available: a scene that needs it becomes a
still theme (a video wallpaper still animates, because the video is read
directly). Everything else — unpacking, textures, audio, packaging — works
the same on Windows.

### Where the helpers are looked for

Nothing is hardcoded. The tools are searched for in this order:

1. the folders given with `--tools` (it can be given more than once)
2. the folders in the `PAPER2PS4_TOOLS` environment variable
   (`:`-separated on Linux, `;` on Windows)
3. `PATH`

```bash
# the folder holding orbis-pub-cmd.exe and its ext/ subfolder
python3 -m paper2ps4 ~/projects/2521462823 -o out.pkg --tools /opt/ps4-tools

# or once, for the whole session
export PAPER2PS4_TOOLS=/opt/ps4-tools
```

## The output file

`.pkg` and `.fpkg` are the same thing: an installable PS4 package. The
extension you ask for is the one you get, because some installers only
accept one of them.

```bash
python3 -m paper2ps4 ~/projects/2521462823 -o out/theme.pkg    # for the PS4's own installer
python3 -m paper2ps4 ~/projects/2521462823 -o out/theme.fpkg   # for anything that prefers it
```

With no extension, `.pkg` is used.

## What it does

1. **Unpacks** `scene.pkg` (the PKGV/PKGM container).
2. **Renders the scene.**
   - A still scene is composited the way Wallpaper Engine draws it: each
     textured quad is placed, scaled and tinted as the scene says, over the
     cleared background.
   - A scene with effects, particles, puppetry or custom shaders is rendered
     for real with `linux-wallpaperengine` and captured as a frame sequence,
     so the animation survives.
   - A scene that is just a video is read directly: no re-capture needed.
3. **Builds the theme**: `theme.xml`, `param.sfo`, `icon0.png`,
   `preview.png`, the backgrounds, the menu music (ATRAC9) and, for an
   animated scene, `scene/background.raf/`. A theme package has no
   `pic1.png`; the console's checker rejects it.
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
python3 -m paper2ps4 <project> -o out.pkg [options]

  -t, --title TEXT        theme title (default: the title in project.json)
  --title-id ID           title ID (default: CUSA00000)
  --provider TEXT         publisher shown by the console (default: Paper2PS4)
  --theme-color 0-7       PS4 theme colour (default: inferred from the scene)
  --content-id ID         content ID (default: generated from the title)
  --category CAT          package category (default: ac)
  --passcode PASSCODE     32-character passcode (default: 32 zeros)
  --animated              force an animated theme
  --static                force a still theme, even if the scene animates
  --frames N              number of animated frames (default: the console's cap)
  --size WxH              animated resolution (default: 960x540 rendered,
                          1280x720 for a video)
  --wait SECONDS          seconds per animated frame (default: 1/12 s
                          rendered, 0.1 s for a video)
  --audio FILE            use only this audio file as the music
  --no-music              build the theme without music
  --icons DIR             folder with system icon replacements
  --sfx DIR               folder with sound effect replacements
  --tools DIR             folder holding orbis-pub-cmd.exe and ps4_at9tool.exe
  --wpe DIR               folder holding linux-wallpaperengine and its assets
  --keep-work             keep the intermediate working folder
  --verify                check the finished package and list what is inside
```

The theme colours, in the order `--theme-color` numbers them: `0` blue
(default), `1` pink, `2` red, `3` navy, `4` dark gray, `5` gold,
`6` light steel blue, `7` purple. The colour is inferred from the wallpaper's
`schemecolor`, or from the artwork when the project does not declare one.

## Replacing the system icons and sounds

A theme may carry its own icons and menu sounds. Point `--icons` and `--sfx`
at a folder:

```bash
python3 -m paper2ps4 ~/wallpapers/projects/2521462823 -o out/niko.fpkg \
    --icons ~/my-icons --sfx ~/my-sounds
```

The folder can be laid out like the theme itself (`function_icon/`,
`content_icon/`, `sound/`) or flat, with the file names the console expects
(`power.png`, `browser.png`, `se_decide.wav`, …).

**[docs/replacements.md](docs/replacements.md) lists every file, its size and
its format.**

## Design decisions

**Animation.** The PS4 plays an animated background as a sequence of DXT1a
frames (a RAF scene) of at most 6 MiB; 24 frames at 960×540 is the sweet spot.
For a scene that really moves, those frames are captured from
`linux-wallpaperengine` running on a private Xvfb display. Software rendering
is slower than the capture rate, so the grab runs in batches until enough
distinct frames exist (up to 20 seconds) and the duplicates are dropped.
A video-only scene skips all of that and is read directly, which keeps the
picture crisp.

**Multiple audio files.** Wallpaper Engine scenes often ship a whole playlist
of tracks. A PS4 theme has room for a single menu music
(`sound/bgm_home.at9`), so the tracks are joined into one file and that is
what gets encoded to ATRAC9 (144 kbps, 48 kHz, looped). Two limits from the
console's checker shape the file:

- at most **240 seconds** (241 s already fails with "invalid number of
  samples");
- levelled to about **-32 LKFS** (anything above -31.95 is rejected; -26 to
  -30 is a warning).

That measured loudness goes into `param.sfo` as `bgmhome_loud`, which is what
the console uses to balance the theme's music against the system sounds.

**Shaders.** Custom HLSL/GLSL cannot run on a PS4 theme, which plays
pre-rendered frames on a textured plane. Rendering the scene captures what the
shaders, puppetry and particles do instead of dropping the animation.

**The theme colour.** The PS4 has eight built-in theme colours, numbered by
`theme.xml` in a fixed order (blue, pink, red, navy, dark gray, gold, light
steel blue, purple). The wallpaper's own `schemecolor` is mapped onto that
palette; when it is missing, the most vivid part of the rendered artwork is
used instead. This is the colour of the console's UI, not of the background.

**The preview image.** It becomes the theme's thumbnail, which is what the
console shows in the theme list. Workshop previews are often tiny, so a
preview narrower than 480 px falls back to the rendered background.

**The console's limits.** These are measured with `orbis-pub-cmd img_verify`
and are enforced, because going over them makes the theme install but fail to
apply:

| | |
|---|---|
| `scene/background.raf/` weight | ≤ 6 MiB |
| frames per scene | ≤ 48 |
| resolution vs frames | 13 at 1280×720, 24 at 960×540, 48 at 640×360 |
| `volume_type` + `IRO_TAG` | `pkg_ps4_theme` + `2` |
| `CATEGORY` | `ac` |
| menu music | ≤ 240 s, ≤ -31.95 LKFS |
| `icon0.png` | 512×512, 24-bit PNG |
| `sce_sys/pic1.png` | must not exist |

`--verify` runs `img_verify` as well as the built-in checks, so it reports
exactly what the console's own checker sees.

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
- Scene rendering uses [linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine)
  (GPLv3); the bundled copy and its licence are in
  [vendor/linux-wallpaperengine/](vendor/linux-wallpaperengine/).
- The `.tex` format was reverse engineered by the
  [RePKG](https://github.com/notbribe/RePKG) project.
- The PKGV/PKGM container handling comes from `we_mobile_convert.py`.
