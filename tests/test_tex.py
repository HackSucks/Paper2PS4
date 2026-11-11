"""Tests for the Wallpaper Engine .tex decoder.

The decoder is checked against the real files of the Wallpaper Engine
projects on this machine, and the DXT output is compared with ffmpeg's own
BC1/BC3 decoders, which are independent implementations.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper2ps4 import tex as T  # noqa: E402
from paper2ps4.dxt1 import dds_header  # noqa: E402

PROJECTS = Path.home() / "wallpapers" / "projects" / "myprojects"

FAILS: list[str] = []


def check(label: str, condition: bool, extra: str = "") -> None:
    print(("  OK  " if condition else " FAIL ") + label + (f"   {extra}" if extra else ""))
    if not condition:
        FAILS.append(label)


def skip(label: str, why: str) -> None:
    print(f"  SKIP  {label}   {why}")


# ---------------------------------------------------------------------------
# Building synthetic .tex files
# ---------------------------------------------------------------------------

def build_tex(path: Path, *, tex_format: int, width: int, height: int,
              payload: bytes, lz4: bool = True, image_width: int | None = None,
              image_height: int | None = None, flags: int = 0,
              image_format: int = -1) -> Path:
    """Write a minimal but valid .tex file."""
    if lz4:
        import lz4.block as fast
        body = fast.compress(payload, store_size=False)
        lz4_flag = 1
    else:
        body = payload
        lz4_flag = 0

    out = bytearray()
    out += b"TEXV0005\x00"
    out += b"TEXI0001\x00"
    out += struct.pack("<7I", tex_format, flags, width, height,
                       image_width or width, image_height or height, 0)
    out += b"TEXB0003\x00"
    out += struct.pack("<i", 1)          # image count
    out += struct.pack("<i", image_format)
    out += struct.pack("<i", 1)          # mipmap count
    out += struct.pack("<ii", width, height)
    out += struct.pack("<ii", lz4_flag, len(payload))
    out += struct.pack("<i", len(body))
    out += body

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def random_dxt_payload(width: int, height: int, dxt1: bool) -> bytes:
    """Random block data: every index and every endpoint gets exercised."""
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    size = blocks * (8 if dxt1 else 16)
    rng = np.random.default_rng(1234)
    return rng.integers(0, 256, size=size, dtype=np.uint8).tobytes()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_lz4() -> None:
    print("\n=== LZ4 ===")
    payload = bytes(range(256)) * 40
    try:
        import lz4.block as fast
        compressed = fast.compress(payload, store_size=False)
    except ImportError:
        skip("the lz4 module is not installed", "")
        return
    check("the C decompressor round-trips",
          T.lz4_decompress(compressed, len(payload)) == payload)
    check("the pure-Python decompressor matches",
          T._lz4_decompress_fallback(compressed, len(payload)) == payload)


def test_tex_structure() -> None:
    print("\n=== .tex structure ===")
    files = sorted({p.resolve() for p in PROJECTS.rglob("*.tex")})
    if not files:
        skip("no .tex files found", str(PROJECTS))
        return
    print(f"  ({len(files)} files)")

    for path in files:
        try:
            t = T.parse_tex(path)
        except Exception as exc:  # noqa: BLE001
            check(f"{path.name} parses", False, str(exc))
            continue
        check(f"{path.name} parses", True,
              f"fmt={t.tex_format} {t.image_width}x{t.image_height}")


def test_decode_matches_wallpaper_engine() -> None:
    """Our decode of niko.tex must equal Wallpaper Engine's own PNG."""
    print("\n=== against Wallpaper Engine's own PNG ===")
    niko_png = None
    for path in sorted(PROJECTS.rglob("niko.tex")):
        candidate = path.with_suffix(".png")
        if candidate.is_file():
            niko_png = candidate
            break
    if niko_png is None:
        skip("niko.png not found", "")
        return

    t = T.parse_tex(niko_png.with_suffix(".tex"))
    payload, ext = T.extract_encoded(t, t.images[0])
    check("the embedded image is a PNG", ext == ".png", ext)
    check("byte-for-byte equal to Wallpaper Engine's PNG",
          payload == niko_png.read_bytes(),
          f"{len(payload)} vs {niko_png.stat().st_size} bytes")


def reference_dxt1(payload: bytes, width: int, height: int) -> np.ndarray:
    """A straightforward implementation of the DXT1 spec, for cross-checking.

    Written from the specification rather than from the decoder under test:
    the endpoints are expanded from RGB565 by bit replication and the two
    interpolated colours are (2*c0+c1)/3 and (c0+2*c1)/3.
    """
    out = np.zeros((height, width, 4), dtype=np.uint8)
    blocks = ((width + 3) // 4) * ((height + 3) // 4)
    for block in range(blocks):
        base = block * 8
        c0 = struct.unpack_from("<H", payload, base)[0]
        c1 = struct.unpack_from("<H", payload, base + 2)[0]

        def expand(v: int) -> tuple[int, int, int]:
            r5 = (v >> 11) & 0x1F
            g6 = (v >> 5) & 0x3F
            b5 = v & 0x1F
            return ((r5 << 3) | (r5 >> 2),
                    (g6 << 2) | (g6 >> 4),
                    (b5 << 3) | (b5 >> 2))

        e0 = expand(c0)
        e1 = expand(c1)
        if c0 > c1:
            palette = [e0, e1,
                       tuple((2 * a + b) // 3 for a, b in zip(e0, e1)),
                       tuple((a + 2 * b) // 3 for a, b in zip(e0, e1))]
        else:
            palette = [e0, e1,
                       tuple((a + b) // 2 for a, b in zip(e0, e1)),
                       (0, 0, 0)]

        bits = struct.unpack_from("<I", payload, base + 4)[0]
        for py in range(4):
            for px in range(4):
                pixel = py * 4 + px
                index = (bits >> (2 * pixel)) & 3
                x = (block % ((width + 3) // 4)) * 4 + px
                y = (block // ((width + 3) // 4)) * 4 + py
                if x >= width or y >= height:
                    continue
                r, g, b = palette[index]
                alpha = 0 if (c0 <= c1 and index == 3) else 255
                out[y, x] = (r, g, b, alpha)
    return out


def test_dxt_against_ffmpeg() -> None:
    """Our DXT decoder must agree with ffmpeg's BC1/BC3 decoders."""
    print("\n=== DXT against ffmpeg ===")
    if not shutil_which("ffmpeg"):
        skip("ffmpeg is not installed", "")
        return

    cases = [
        (T.TEX_FORMAT_DXT1, b"DXT1", "DXT1"),
        (T.TEX_FORMAT_DXT3, b"DXT3", "DXT3"),
        (T.TEX_FORMAT_DXT5, b"DXT5", "DXT5"),
    ]
    width, height = 64, 32

    with tempfile.TemporaryDirectory() as tmp:
        for tex_format, fourcc, label in cases:
            dxt1 = tex_format == T.TEX_FORMAT_DXT1
            payload = random_dxt_payload(width, height, dxt1)
            tex_path = build_tex(Path(tmp) / f"{label}.tex", tex_format=tex_format,
                                 width=width, height=height, payload=payload)

            t = T.parse_tex(tex_path)
            decoded = T.decode_mipmap(t.images[0].base, t.tex_format)
            check(f"{label} payload decompresses", len(decoded) == len(payload))

            dds = dds_header(width, height, len(decoded))
            dds = dds[:84] + fourcc + dds[88:]
            dds_path = Path(tmp) / f"{label}.dds"
            dds_path.write_bytes(dds + decoded)

            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(dds_path),
                 "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                capture_output=True)
            if proc.returncode != 0 or len(proc.stdout) < width * height * 4:
                skip(f"ffmpeg could not decode {label}", "")
                continue
            ref = np.frombuffer(proc.stdout[:width * height * 4],
                                dtype=np.uint8).reshape(height, width, 4)

            mine = T.decode_dxt(decoded, width, height, tex_format)
            diff = np.abs(ref.astype(int) - mine.astype(int))
            # ffmpeg expands RGB565 to 8 bits by rounding to nearest, while
            # the specification (and the GPU that renders the wallpaper)
            # replicate the high bits. The two differ by one in some
            # channels, so the comparison allows that much.
            check(f"{label} matches ffmpeg within one",
                  diff.max() <= 1, f"max diff {diff.max()}")

            if dxt1:
                spec = reference_dxt1(decoded, width, height)
                exact = np.abs(spec.astype(int) - mine.astype(int)).max()
                check(f"{label} matches the specification exactly",
                      exact == 0, f"max diff {exact}")


def test_raw_formats() -> None:
    print("\n=== raw pixel formats ===")
    width, height = 8, 4
    with tempfile.TemporaryDirectory() as tmp:
        # RGBA8888
        rgba = np.random.default_rng(7).integers(0, 256, (height, width, 4),
                                                  dtype=np.uint8).tobytes()
        path = build_tex(Path(tmp) / "rgba.tex", tex_format=T.TEX_FORMAT_RGBA8888,
                         width=width, height=height, payload=rgba)
        t = T.parse_tex(path)
        got = T.decode_image(t, t.images[0])
        check("RGBA8888 round-trips",
              got.tobytes() == rgba)

        # R8
        gray = np.random.default_rng(8).integers(0, 256, (height, width),
                                                 dtype=np.uint8).tobytes()
        path = build_tex(Path(tmp) / "r8.tex", tex_format=T.TEX_FORMAT_R8,
                         width=width, height=height, payload=gray)
        t = T.parse_tex(path)
        got = T.decode_image(t, t.images[0])
        check("R8 expands to grey", np.array_equal(got[..., 0], got[..., 1])
              and np.array_equal(got[..., 1], got[..., 2])
              and np.array_equal(got[..., 0].ravel(),
                                 np.frombuffer(gray, dtype=np.uint8)))

        # RG88
        rg = np.random.default_rng(9).integers(0, 256, (height, width, 2),
                                                dtype=np.uint8).tobytes()
        path = build_tex(Path(tmp) / "rg88.tex", tex_format=T.TEX_FORMAT_RG88,
                         width=width, height=height, payload=rg)
        t = T.parse_tex(path)
        got = T.decode_image(t, t.images[0])
        check("RG88 keeps both channels",
              got[..., :2].tobytes() == rg)


def build_gif_tex(path: Path, frames: list[tuple[int, int]], sheet_w: int,
                  sheet_h: int) -> Path:
    """A GIF texture: one sheet image and a frame table that crops it."""
    out = bytearray()
    out += b"TEXV0005\x00"
    out += b"TEXI0001\x00"
    # The IsGif flag is 4.
    out += struct.pack("<7I", T.TEX_FORMAT_RGBA8888, T.FLAG_IS_GIF,
                       sheet_w, sheet_h, sheet_w, sheet_h, 0)
    out += b"TEXB0003\x00"
    out += struct.pack("<i", len(frames))
    out += struct.pack("<i", -1)
    for width, height in frames:
        payload = bytes(width * height * 4)
        out += struct.pack("<i", 1)          # mipmap count
        out += struct.pack("<ii", width, height)
        out += struct.pack("<ii", 0, len(payload))
        out += struct.pack("<i", len(payload))
        out += payload
    # Frame info.
    out += b"TEXS0003\x00"
    out += struct.pack("<i", len(frames))
    out += struct.pack("<ii", sheet_w, sheet_h)
    for index, (width, height) in enumerate(frames):
        out += struct.pack("<i", index)       # image id
        out += struct.pack("<f", 0.1)         # frame time
        out += struct.pack("<ffff", 0.0, 0.0, float(width), float(height))
        out += struct.pack("<ff", float(width), float(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def test_gif_frames() -> None:
    print("\n=== GIF textures ===")
    found = False
    for path in sorted(PROJECTS.rglob("*.tex")):
        t = T.parse_tex(path)
        if not t.is_gif:
            continue
        found = True
        frames = T.decode_gif_frames(t)
        check(f"{path.name} yields frames", len(frames) > 0, f"{len(frames)} frames")
        check(f"{path.name} frames are RGBA",
              all(f.shape[2] == 4 for f in frames))
    if not found:
        print("  (no GIF textures in the projects, using a synthetic one)")

    with tempfile.TemporaryDirectory() as tmp:
        path = build_gif_tex(Path(tmp) / "sprite.tex",
                             [(8, 8), (8, 8), (4, 4)], 16, 16)
        t = T.parse_tex(path)
        check("the GIF flag is read", t.is_gif)
        check("the frame table is read", len(t.frames) == 3, str(len(t.frames)))
        frames = T.decode_gif_frames(t)
        check("every frame comes out", len(frames) == 3, str(len(frames)))
        check("the frames have the right size",
              frames[0].shape == (8, 8, 4) and frames[2].shape == (4, 4, 4),
              str([f.shape for f in frames]))


def test_write_png() -> None:
    print("\n=== PNG output ===")
    img = np.zeros((4, 6, 4), dtype=np.uint8)
    img[..., 0] = 255
    img[..., 3] = 255
    with tempfile.TemporaryDirectory() as tmp:
        path = T.write_png(Path(tmp) / "out.png", img)
        data = path.read_bytes()
        check("a PNG signature is written", data[:8] == b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        check("the dimensions are right", (width, height) == (6, 4),
              f"{width}x{height}")
        # The IDAT must inflate back to the pixels.
        idat = b""
        pos = 8
        while pos < len(data):
            length = struct.unpack(">I", data[pos:pos + 4])[0]
            tag = data[pos + 4:pos + 8]
            if tag == b"IDAT":
                idat += data[pos + 8:pos + 8 + length]
            pos += 12 + length
        raw = zlib.decompress(idat)
        check("the pixels survive the round trip",
              raw == b"\x00" + b"\xff\x00\x00\xff" * 6 + (b"\x00" + b"\xff\x00\x00\xff" * 6) * 3)


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


if __name__ == "__main__":
    test_lz4()
    test_tex_structure()
    test_decode_matches_wallpaper_engine()
    test_dxt_against_ffmpeg()
    test_raw_formats()
    test_gif_frames()
    test_write_png()

    print()
    if FAILS:
        print(f"{len(FAILS)} FAILURE(S):")
        for fail in FAILS:
            print(f"  - {fail}")
        sys.exit(1)
    print("all tests passed")
