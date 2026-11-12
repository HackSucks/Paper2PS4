# Replacements

A theme can replace the console's own icons and its menu sounds. Point
`paper2ps4` at a folder and the files inside are copied into the theme.

```bash
python3 -m paper2ps4 ~/wallpapers/projects/2521462823 -o out/niko.fpkg \
    --icons ~/my-icons --sfx ~/my-sounds
```

`--icons` and `--sfx` may point at the same folder; each one reads only what
it can hold. A folder with nothing in it changes nothing: the console keeps
its own icons and sounds.

## The folder

Two layouts work. The first mirrors the theme's own folders and is the
clearer one:

```
overrides/
  function_icon/
    power.png          128x128
    power_glow.png     152x152   (optional)
  content_icon/
    browser.png        512x512
  sound/
    se_decide.wav      any format ffmpeg reads
    se_cursor.wav
    se_cancel.wav
  icon0.png            512x512
```

The second is flat, with the file names the console expects:

```
overrides/
  power.png            a function icon
  browser.png          a content icon
  se_decide.wav        a sound effect
  icon0.png
```

## The files

### Function icons

The top-menu icons. The file name is fixed by the console: it is the one it
looks for, so they cannot be renamed.

| File | Size | Format | Goes to |
|---|---|---|---|
| `community.png` | 128×128 | PNG with alpha | `texture/function_icon/community.png` |
| `event.png` | 128×128 | PNG with alpha | `texture/function_icon/event.png` |
| `friend.png` | 128×128 | PNG with alpha | `texture/function_icon/friend.png` |
| `message.png` | 128×128 | PNG with alpha | `texture/function_icon/message.png` |
| `notification.png` | 128×128 | PNG with alpha | `texture/function_icon/notification.png` |
| `party.png` | 128×128 | PNG with alpha | `texture/function_icon/party.png` |
| `power.png` | 128×128 | PNG with alpha | `texture/function_icon/power.png` |
| `setting.png` | 128×128 | PNG with alpha | `texture/function_icon/setting.png` |
| `trophy.png` | 128×128 | PNG with alpha | `texture/function_icon/trophy.png` |

Each one also has a **glow** variant, 152×152, drawn behind the icon. Give it
as `<name>_glow.png` next to the icon, or leave it out and the icon itself
is scaled up to make the glow.

### Content icons

The icons of the content on the console's home screen.

| File | Size | Format | Goes to |
|---|---|---|---|
| `browser.png` | 512×512 | PNG with alpha | `texture/content_icon/browser.png` |
| `disc.png` | 512×512 | PNG with alpha | `texture/content_icon/disc.png` |
| `discoverlay.png` | 512×512 | PNG with alpha | `texture/content_icon/discoverlay.png` |
| `folder.png` | 512×512 | PNG with alpha | `texture/content_icon/folder.png` |
| `gallery.png` | 512×512 | PNG with alpha | `texture/content_icon/gallery.png` |
| `library.png` | 512×512 | PNG with alpha | `texture/content_icon/library.png` |
| `livefromps.png` | 512×512 | PNG with alpha | `texture/content_icon/livefromps.png` |
| `shareplay.png` | 512×512 | PNG with alpha | `texture/content_icon/shareplay.png` |
| `tvvideo.png` | 512×512 | PNG with alpha | `texture/content_icon/tvvideo.png` |
| `usbmusic.png` | 512×512 | PNG with alpha | `texture/content_icon/usbmusic.png` |

### The theme's own icon

| File | Size | Format | Goes to |
|---|---|---|---|
| `icon0.png` | 512×512 | 24-bit PNG | `sce_sys/icon0.png` — the icon in the console's menu |

Without it the theme's own background is used. It is written as a 24-bit PNG
(no alpha) because the console's checker rejects a 32-bit `icon0.png`.

There is no `pic1.png` in a theme package: `img_verify` rejects it ("must
not exist"), so a `pic1.png` in the folder is ignored.

### Sound effects

The three sounds the console makes when you move around the menu. They are
Sony ADPCM (`.vag`), which is converted here.

| File | Sound | Goes to |
|---|---|---|
| `se_decide` | confirm | `sound/se_decide.vag` |
| `se_cursor` | move the cursor | `sound/se_cursor.vag` |
| `se_cancel` | cancel | `sound/se_cancel.vag` |

Any format ffmpeg reads works (`.wav`, `.mp3`, `.ogg`, `.flac`, …). A `.vag`
file is used as it is. The audio is mixed down to mono at 48 kHz and cut at
5 seconds, which is all a system effect ever lasts.

The menu **music** is not here: it comes from the wallpaper's own audio, or
you can leave the theme silent with `--no-music`.

## Notes

- **Sizes are not fixed, formats are.** An icon is scaled and centre-cropped
  to the size its slot needs, so a 512×512 picture works for a 128×128
  function icon. Give the exact size when you can and it stays crisp.
- **PNG with alpha.** The icons are drawn over the console's own background,
  so the transparency is used.
- **The file names are the console's**, not yours. A `power.png` replaces the
  power icon; anything else in the folder is ignored.
