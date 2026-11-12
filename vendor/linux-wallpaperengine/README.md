# linux-wallpaperengine (bundled)

This folder holds a copy of
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine), the
renderer `paper2ps4` uses to turn an animated Wallpaper Engine scene into the
frame sequence a PS4 theme can play.

It is GPLv3; the full text is in [LICENSE](LICENSE).

## What is here

| File | What it is |
|---|---|
| `linux-wallpaperengine` | the engine binary (RUNPATH `$ORIGIN:$ORIGIN/lib:$ORIGIN/lib64`) |
| `liblinux-wallpaperengine-lib.so` | the engine's own code, as a shared library |
| `libkissfft-float.so.131.1.0` | FFT library the engine links against |
| `libkissfft-float.so.131` | symlink to the above (the SONAME it asks for) |
| `libcef.so` | a **stub**, see below |
| `assets.tar.xz` | the engine's shader/model assets, unpacked on first use |

## The stub `libcef.so`

The real `libcef.so` (Chromium Embedded Framework) is 220 MB. The engine only
uses it for **web** wallpapers, which a PS4 theme cannot use anyway, so the
bundled copy replaces it with a 29 KB library that exports the 124 `cef_*`
symbols the engine links against and returns 0 from all of them.

That is enough for scene and video wallpapers. A web wallpaper would fail
inside the stub; `paper2ps4` does not route those here.

## The assets

`assets.tar.xz` is the engine's `assets/` folder (shaders, particle
definitions, the common models) compressed to about 34 MB. It is unpacked once
to `assets/` next to these files the first time the engine is used, which
takes a moment and needs write access to this folder. On a read-only install
the unpacked copy goes to `~/.cache/paper2ps4/wpe-assets` instead.

`assets/` itself is not tracked: it is derived from the archive.

## Using a different copy

The folder is found automatically. To use another build instead, either put
`linux-wallpaperengine` on `PATH`, pass `--wpe DIR`, or set `PAPER2PS4_WPE`.

The engine needs a Linux display. `paper2ps4` starts a private `Xvfb` for it,
so nothing appears on screen and the desktop session is untouched. On Windows
the renderer is not available and the scene falls back to a still image.

## Building it yourself

Follow the upstream instructions. The build used here is upstream commit
`b016d7d1` (2026-06-09), built on Linux x86-64. `libkissfft-float.so.131` and
the CEF distribution come from the same build; only `libcef.so` was replaced
with the stub described above.
