"""Wallpaper Engine .tex decoder.

A .tex is Wallpaper Engine's GPU texture container. The format was reverse
engineered by the RePKG project (https://github.com/notbribe/RePKG); this is a
native Python implementation of the same layout:

    "TEXV0005\\0"                 container magic
    "TEXI0001\\0"                 info magic
    header (7 x uint32)           format, flags, tex w/h, image w/h, unk
    "TEXB000X\\0"                 image container magic
    int32                         image count
    [int32]                       image format (TEXB0003/0004 only)
    images[]
      int32                       mipmap count
      mipmaps[]
        int32 width
        int32 height
        int32 lz4 flag
        int32 decompressed size
        int32 byte count
        bytes[byte count]
    "TEXS000X\\0"                 frame info (only when the IsGif flag is set)
      int32                       frame count
      [int32 gif width, gif height]   (TEXS0003 only)
      frames[]

Mipmap payloads are LZ4-compressed (raw LZ4 block format) or stored raw.
Pixel formats: RGBA8888, R8, RG88, DXT1, DXT3, DXT5.

The decoder returns the base mipmap of every image as RGBA bytes. For GIF
textures each image is one animation frame.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# --- format constants -----------------------------------------------------

TEX_FORMAT_RGBA8888 = 0
TEX_FORMAT_DXT5 = 4
TEX_FORMAT_DXT3 = 6
TEX_FORMAT_DXT1 = 7
TEX_FORMAT_RG88 = 8
TEX_FORMAT_R8 = 9

FLAG_NO_INTERPOLATION = 1
FLAG_CLAMP_UVS = 2
FLAG_IS_GIF = 4
FLAG_IS_VIDEO_TEXTURE = 32

#: FreeImage formats we can hand straight to ffmpeg.
FIF_BMP = 0
FIF_JPEG = 2
FIF_PNG = 13
FIF_TARGA = 17
FIF_DDS = 24
FIF_GIF = 25
FIF_MP4 = 35

_FIF_EXTENSIONS = {
    FIF_BMP: ".bmp",
    FIF_JPEG: ".jpg",
    FIF_PNG: ".png",
    FIF_TARGA: ".tga",
    FIF_DDS: ".dds",
    FIF_GIF: ".gif",
    FIF_MP4: ".mp4",
}

#: Sanity limits so a corrupt file cannot allocate gigabytes.
MAX_IMAGES = 4096
MAX_MIPMAPS = 64
MAX_MIPMAP_BYTES = 512 * 1024 * 1024
MAX_FRAME_COUNT = 100_000


class TexError(Exception):
    """Malformed .tex file."""


# --- LZ4 block decompression ----------------------------------------------

def _lz4_decompress_fallback(src: bytes, out_size: int) -> bytes:
    """Pure-Python LZ4 block decompressor (used when the lz4 module is absent).

    LZ4 block format: a sequence of sequences, each made of a token byte
    (high nibble = literal length, low nibble = match length - 4), then the
    literals, then a 2-byte little-endian offset and the match.
    """
    dst = bytearray()
    sp = 0
    n = len(src)
    while sp < n:
        token = src[sp]
        sp += 1
        lit_len = token >> 4
        if lit_len == 15:
            while True:
                b = src[sp]
                sp += 1
                lit_len += b
                if b != 255:
                    break
        dst += src[sp:sp + lit_len]
        sp += lit_len
        if sp >= n:
            break
        offset = src[sp] | (src[sp + 1] << 8)
        sp += 2
        match_len = (token & 0x0F) + 4
        if (token & 0x0F) == 15:
            while True:
                b = src[sp]
                sp += 1
                match_len += b
                if b != 255:
                    break
        start = len(dst) - offset
        if start < 0:
            raise TexError("LZ4 block: match offset points before the output")
        # Copy byte by byte: overlapping matches are legal and must repeat.
        for i in range(match_len):
            dst.append(dst[start + i])
    if len(dst) != out_size:
        raise TexError(
            f"LZ4 block decompressed to {len(dst)} bytes, expected {out_size}")
    return bytes(dst)


def lz4_decompress(src: bytes, out_size: int) -> bytes:
    """Decompress a raw LZ4 block."""
    try:
        import lz4.block as _lz4_block
    except ImportError:
        return _lz4_decompress_fallback(src, out_size)
    try:
        out = _lz4_block.decompress(src, uncompressed_size=out_size)
    except Exception:
        # The C extension refuses some blocks the pure-Python one accepts.
        return _lz4_decompress_fallback(src, out_size)
    if len(out) != out_size:
        raise TexError(
            f"LZ4 block decompressed to {len(out)} bytes, expected {out_size}")
    return out


# --- DXT decoding ----------------------------------------------------------

def _unpack_565(packed: np.ndarray) -> np.ndarray:
    """uint16 RGB565 -> uint8 RGB (N, 3)."""
    r5 = (packed >> 11) & 0x1F
    g6 = (packed >> 5) & 0x3F
    b5 = packed & 0x1F
    return np.stack([
        (r5 << 3) | (r5 >> 2),
        (g6 << 2) | (g6 >> 4),
        (b5 << 3) | (b5 >> 2),
    ], axis=-1).astype(np.uint8)


def _decode_dxt_color(color: np.ndarray, is_dxt1: bool) -> np.ndarray:
    """Decode the 8-byte colour half of a DXT block into RGBA (N, 16, 4)."""
    n = color.shape[0]
    c0 = color[:, 0].astype(np.uint16) | (color[:, 1].astype(np.uint16) << 8)
    c1 = color[:, 2].astype(np.uint16) | (color[:, 3].astype(np.uint16) << 8)
    rgb0 = _unpack_565(c0)
    rgb1 = _unpack_565(c1)

    # DXT1 uses the 3-colour + punchthrough mode when c0 <= c1.
    # DXT3/DXT5 always use the 4-colour mode.
    four_color = (c0 > c1) if is_dxt1 else np.ones(n, dtype=bool)

    palette = np.zeros((n, 4, 4), dtype=np.uint8)
    palette[:, 0, :3] = rgb0
    palette[:, 1, :3] = rgb1
    palette[:, 0, 3] = 255
    palette[:, 1, 3] = 255

    c0f = rgb0.astype(np.int32)
    c1f = rgb1.astype(np.int32)
    mid = np.where(
        four_color[:, None],
        (2 * c0f + c1f) // 3,
        (c0f + c1f) // 2,
    )
    palette[:, 2, :3] = mid.astype(np.uint8)
    palette[:, 2, 3] = 255
    if is_dxt1:
        hi = np.where(
            four_color[:, None],
            (c0f + 2 * c1f) // 3,
            0,
        )
        palette[:, 3, :3] = hi.astype(np.uint8)
        palette[:, 3, 3] = np.where(four_color, 255, 0).astype(np.uint8)
    else:
        hi = (c0f + 2 * c1f) // 3
        palette[:, 3, :3] = hi.astype(np.uint8)
        palette[:, 3, 3] = 255
    # 16 2-bit indices, stored as 4 bytes, low bits first.
    idx = np.empty((n, 16), dtype=np.uint8)
    for i in range(4):
        packed = color[:, 4 + i]
        idx[:, 0 + i * 4] = packed & 0x03
        idx[:, 1 + i * 4] = (packed >> 2) & 0x03
        idx[:, 2 + i * 4] = (packed >> 4) & 0x03
        idx[:, 3 + i * 4] = (packed >> 6) & 0x03

    return np.take_along_axis(palette, idx[:, :, None], axis=1)


def _decode_dxt3_alpha(block: np.ndarray) -> np.ndarray:
    """DXT3: 8 bytes of 4-bit alpha values (16 pixels)."""
    n = block.shape[0]
    out = np.empty((n, 16), dtype=np.uint8)
    for i in range(8):
        quant = block[:, i]
        lo = quant & 0x0F
        hi = quant & 0xF0
        out[:, 2 * i] = lo | (lo << 4)
        out[:, 2 * i + 1] = (hi | (hi >> 4)) & 0xFF
    return out


def _decode_dxt5_alpha(block: np.ndarray) -> np.ndarray:
    """DXT5: two alpha endpoints plus 16 3-bit indices."""
    n = block.shape[0]
    a0 = block[:, 0].astype(np.int32)
    a1 = block[:, 1].astype(np.int32)

    codes = np.zeros((n, 8), dtype=np.uint8)
    codes[:, 0] = a0
    codes[:, 1] = a1
    # alpha0 > alpha1 selects the 8-value codebook, otherwise the 6-value one
    # (which ends with a fully transparent and a fully opaque entry).
    seven = a0 > a1

    # 8-value codebook: codes[2..7] = ((7-i)*a0 + i*a1) / 7, i = 1..6
    interp7 = np.empty((n, 8), dtype=np.uint8)
    for i in range(1, 7):
        interp7[:, 1 + i] = (((7 - i) * a0 + i * a1) // 7).astype(np.uint8)
    # 6-value codebook: codes[2..5] = ((5-i)*a0 + i*a1) / 5, i = 1..4
    interp5 = np.empty((n, 8), dtype=np.uint8)
    for i in range(1, 5):
        interp5[:, 1 + i] = (((5 - i) * a0 + i * a1) // 5).astype(np.uint8)
    interp5[:, 6] = 0
    interp5[:, 7] = 255

    codes = np.where(seven[:, None], interp7, interp5)
    # The two endpoints are the same for both codebooks.
    codes[:, 0] = a0
    codes[:, 1] = a1

    b = block[:, 2:8].astype(np.uint32)
    v0 = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
    v1 = b[:, 3] | (b[:, 4] << 8) | (b[:, 5] << 16)
    idx = np.empty((n, 16), dtype=np.uint8)
    for j in range(8):
        idx[:, j] = (v0 >> (3 * j)) & 0x07
        idx[:, 8 + j] = (v1 >> (3 * j)) & 0x07

    return np.take_along_axis(codes, idx, axis=1)


def decode_dxt(data: bytes, width: int, height: int, tex_format: int) -> np.ndarray:
    """Decode a DXT1/DXT3/DXT5 texture into an RGBA array (H, W, 4)."""
    is_dxt1 = tex_format == TEX_FORMAT_DXT1
    bytes_per_block = 8 if is_dxt1 else 16

    bw = (width + 3) // 4
    bh = (height + 3) // 4
    count = bw * bh
    needed = count * bytes_per_block
    if len(data) < needed:
        raise TexError(
            f"DXT payload too small: {len(data)} bytes, need {needed}")

    raw = np.frombuffer(data, dtype=np.uint8, count=needed, offset=0)
    blocks = raw.reshape(count, bytes_per_block)

    if is_dxt1:
        rgba_blocks = _decode_dxt_color(blocks, is_dxt1=True)
    else:
        color = _decode_dxt_color(blocks[:, 8:16], is_dxt1=False)
        if tex_format == TEX_FORMAT_DXT3:
            alpha = _decode_dxt3_alpha(blocks[:, 0:8])
        else:
            alpha = _decode_dxt5_alpha(blocks[:, 0:8])
        color[:, :, 3] = alpha
        rgba_blocks = color

    # Blocks are stored row by row, 4x4 pixels each.
    pixels = rgba_blocks.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4)
    pixels = pixels.reshape(bh * 4, bw * 4, 4)
    return pixels[:height, :width]


def decode_raw(data: bytes, width: int, height: int, tex_format: int) -> np.ndarray:
    """Decode an uncompressed texture into an RGBA array (H, W, 4)."""
    if tex_format == TEX_FORMAT_RGBA8888:
        expected = width * height * 4
        if len(data) < expected:
            raise TexError(f"RGBA payload too small: {len(data)} < {expected}")
        arr = np.frombuffer(data, dtype=np.uint8, count=expected, offset=0)
        return arr.reshape(height, width, 4)

    if tex_format == TEX_FORMAT_R8:
        expected = width * height
        if len(data) < expected:
            raise TexError(f"R8 payload too small: {len(data)} < {expected}")
        arr = np.frombuffer(data, dtype=np.uint8, count=expected, offset=0)
        arr = arr.reshape(height, width)
        out = np.empty((height, width, 4), dtype=np.uint8)
        out[..., 0] = arr
        out[..., 1] = arr
        out[..., 2] = arr
        out[..., 3] = 255
        return out

    if tex_format == TEX_FORMAT_RG88:
        expected = width * height * 2
        if len(data) < expected:
            raise TexError(f"RG88 payload too small: {len(data)} < {expected}")
        arr = np.frombuffer(data, dtype=np.uint8, count=expected, offset=0)
        arr = arr.reshape(height, width, 2)
        out = np.empty((height, width, 4), dtype=np.uint8)
        out[..., 0] = arr[..., 0]
        out[..., 1] = arr[..., 1]
        out[..., 2] = 0
        out[..., 3] = 255
        return out

    raise TexError(f"Unsupported uncompressed texture format: {tex_format}")


# --- PNG output ------------------------------------------------------------

def write_png(path: Path, rgba: np.ndarray) -> Path:
    """Write an RGBA array (H, W, 4) as a PNG."""
    height, width = rgba.shape[:2]
    arr = np.ascontiguousarray(rgba, dtype=np.uint8)
    stride = width * 4
    # One filter byte (0 = None) in front of every scanline.
    raw = np.empty((height, stride + 1), dtype=np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = arr.reshape(height, stride)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
           + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


# --- structures ------------------------------------------------------------

@dataclass
class Mipmap:
    width: int
    height: int
    data: bytes
    is_lz4: bool
    decompressed_size: int


@dataclass
class TexImage:
    mipmaps: list[Mipmap] = field(default_factory=list)

    @property
    def base(self) -> Mipmap:
        return self.mipmaps[0]


@dataclass
class TexFrame:
    image_id: int
    frame_time: float
    x: float
    y: float
    width: float
    width_y: float
    height_x: float
    height: float


@dataclass
class Tex:
    magic: str
    info_magic: str
    tex_format: int
    flags: int
    texture_width: int
    texture_height: int
    image_width: int
    image_height: int
    image_format: int
    images: list[TexImage] = field(default_factory=list)
    frames: list[TexFrame] = field(default_factory=list)
    gif_width: int = 0
    gif_height: int = 0

    @property
    def is_gif(self) -> bool:
        return bool(self.flags & FLAG_IS_GIF)

    @property
    def is_video(self) -> bool:
        return bool(self.flags & FLAG_IS_VIDEO_TEXTURE)

    @property
    def is_raw_format(self) -> bool:
        """True when the payload is uncompressed pixels (not an encoded image)."""
        return self.image_format == -1


# --- parser ----------------------------------------------------------------

class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def nstring(self, max_length: int = -1) -> str:
        end = self.data.index(b"\x00", self.pos)
        if max_length >= 0 and end - self.pos > max_length:
            end = self.pos + max_length
        out = self.data[self.pos:end].decode("ascii", "replace")
        self.pos = end + 1
        return out

    def int32(self) -> int:
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def uint32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def float32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def bytes(self, count: int) -> bytes:
        if self.pos + count > len(self.data):
            raise TexError("mipmap byte count exceeds the file length")
        out = self.data[self.pos:self.pos + count]
        self.pos += count
        return out


def parse_tex(path: Path) -> Tex:
    """Parse a .tex file into its structure (without decompressing mipmaps)."""
    data = Path(path).read_bytes()
    r = _Reader(data)

    magic = r.nstring(16)
    if magic != "TEXV0005":
        raise TexError(f"unexpected container magic {magic!r} (expected TEXV0005)")
    info_magic = r.nstring(16)
    if info_magic != "TEXI0001":
        raise TexError(f"unexpected info magic {info_magic!r} (expected TEXI0001)")

    tex = Tex(
        magic=magic,
        info_magic=info_magic,
        tex_format=r.int32(),
        flags=r.uint32(),
        texture_width=r.int32(),
        texture_height=r.int32(),
        image_width=r.int32(),
        image_height=r.int32(),
        image_format=-1,
    )
    r.uint32()  # unknown int

    container = r.nstring(16)
    image_count = r.int32()
    if image_count < 0 or image_count > MAX_IMAGES:
        raise TexError(f"implausible image count: {image_count}")

    if container in ("TEXB0003", "TEXB0004"):
        tex.image_format = r.int32()
    elif container not in ("TEXB0001", "TEXB0002"):
        raise TexError(f"unknown image container {container!r}")
    if container == "TEXB0004":
        is_video = r.int32()
        if tex.image_format == -1 and is_video == 1:
            tex.image_format = FIF_MP4
    version = int(container[4:]) if container[4:].isdigit() else 0

    for _ in range(image_count):
        mipmap_count = r.int32()
        if mipmap_count < 0 or mipmap_count > MAX_MIPMAPS:
            raise TexError(f"implausible mipmap count: {mipmap_count}")
        image = TexImage()
        for _ in range(mipmap_count):
            width = r.int32()
            height = r.int32()
            if version == 1:
                is_lz4 = False
                decompressed = 0
            else:
                is_lz4 = r.int32() == 1
                decompressed = r.int32()
            byte_count = r.int32()
            if byte_count < 0 or byte_count > MAX_MIPMAP_BYTES:
                raise TexError(f"implausible mipmap byte count: {byte_count}")
            payload = r.bytes(byte_count)
            image.mipmaps.append(Mipmap(width, height, payload, is_lz4, decompressed))
        tex.images.append(image)

    # Frame info (only present for GIF textures).
    if tex.is_gif and r.pos < len(data):
        frame_magic = r.nstring(16)
        frame_count = r.int32()
        if frame_count < 0 or frame_count > MAX_FRAME_COUNT:
            raise TexError(f"implausible frame count: {frame_count}")
        if frame_magic == "TEXS0003":
            tex.gif_width = r.int32()
            tex.gif_height = r.int32()
        elif frame_magic not in ("TEXS0001", "TEXS0002"):
            raise TexError(f"unknown frame info container {frame_magic!r}")
        for _ in range(frame_count):
            if frame_magic == "TEXS0001":
                frame = TexFrame(
                    image_id=r.int32(), frame_time=r.float32(),
                    x=float(r.int32()), y=float(r.int32()),
                    width=float(r.int32()), width_y=float(r.int32()),
                    height_x=float(r.int32()), height=float(r.int32()),
                )
            else:
                frame = TexFrame(
                    image_id=r.int32(), frame_time=r.float32(),
                    x=r.float32(), y=r.float32(),
                    width=r.float32(), width_y=r.float32(),
                    height_x=r.float32(), height=r.float32(),
                )
            tex.frames.append(frame)
        if tex.gif_width == 0 or tex.gif_height == 0:
            if tex.frames:
                tex.gif_width = int(tex.frames[0].width)
                tex.gif_height = int(tex.frames[0].height)

    return tex


def decode_mipmap(mipmap: Mipmap, tex_format: int) -> bytes:
    """Return the mipmap payload decompressed (LZ4) but not pixel-decoded."""
    if mipmap.is_lz4:
        return lz4_decompress(mipmap.data, mipmap.decompressed_size)
    return mipmap.data


def decode_image(tex: Tex, image: TexImage) -> np.ndarray:
    """Decode the base mipmap of an image into an RGBA array (H, W, 4).

    The texture may be bigger than the visible image (Wallpaper Engine pads to
    a multiple of 16 and keeps the real size in the header), so the result is
    cropped to image_width x image_height.
    """
    if not tex.is_raw_format:
        raise TexError(
            f"texture stores an encoded image (format {tex.image_format}); "
            "use extract_encoded() instead"
        )
    mip = image.base
    payload = decode_mipmap(mip, tex.tex_format)

    if tex.tex_format in (TEX_FORMAT_DXT1, TEX_FORMAT_DXT3, TEX_FORMAT_DXT5):
        pixels = decode_dxt(payload, mip.width, mip.height, tex.tex_format)
    else:
        pixels = decode_raw(payload, mip.width, mip.height, tex.tex_format)

    # Crop the padding away.
    if tex.image_width and tex.image_height:
        if pixels.shape[1] >= tex.image_width and pixels.shape[0] >= tex.image_height:
            pixels = pixels[:tex.image_height, :tex.image_width]
    return pixels


def extract_encoded(tex: Tex, image: TexImage) -> tuple[bytes, str]:
    """Return the raw bytes of a texture that stores an encoded image.

    Wallpaper Engine sometimes stores the texture as a whole PNG/JPEG/BMP
    instead of raw pixels. The bytes are returned with the extension that
    matches the format, ready to be written to disk.
    """
    payload = decode_mipmap(image.base, tex.tex_format)
    return payload, image_format_extension(tex.image_format)


def extract_video(tex: Tex, image: TexImage) -> bytes:
    """Return the raw MP4 bytes of a video texture."""
    payload = decode_mipmap(image.base, tex.tex_format)
    if payload[4:12] not in (b"ftypisom", b"ftypmsnv", b"ftypmp42"):
        raise TexError("video texture does not contain an MP4 stream")
    return payload


def tex_to_png(tex: Tex, out_path: Path) -> Path:
    """Write the base mipmap of the first image to `out_path` as a PNG.

    Handles both raw pixel textures and textures that embed an encoded image.
    """
    out_path = Path(out_path)
    if not tex.is_raw_format:
        payload, ext = extract_encoded(tex, tex.images[0])
        if ext == ".png":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(payload)
            return out_path
        # Other formats (BMP/JPEG/TGA) are converted by ffmpeg.
        from .media import FFMPEG, require_tools, _run
        require_tools()
        tmp = out_path.with_suffix(ext)
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(payload)
        _run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
              "-i", str(tmp), "-frames:v", "1", str(out_path)])
        tmp.unlink(missing_ok=True)
        return out_path

    pixels = decode_image(tex, tex.images[0])
    return write_png(out_path, pixels)


def decode_gif_frames(tex: Tex) -> list[np.ndarray]:
    """Decode a GIF texture into its animation frames (RGBA arrays).

    Each frame is a region of one of the sheet images; the region may be
    rotated 90 degrees, which the signed width/height encode.
    """
    if not tex.frames:
        return [decode_image(tex, tex.images[0])] if tex.images else []

    sheets = [decode_image(tex, img) for img in tex.images]
    out: list[np.ndarray] = []
    for frame in tex.frames:
        sheet = sheets[frame.image_id]
        x = int(min(frame.x, frame.x + frame.width))
        y = int(min(frame.y, frame.y + frame.height))
        w = int(abs(frame.width))
        h = int(abs(frame.height))
        if w == 0:
            w = int(abs(frame.height_x))
        if h == 0:
            h = int(abs(frame.width_y))
        if w == 0 or h == 0:
            continue
        x = max(0, min(x, sheet.shape[1] - 1))
        y = max(0, min(y, sheet.shape[0] - 1))
        patch = sheet[y:y + h, x:x + w]
        if patch.size == 0:
            continue
        # A negative width or height means the region is rotated.
        if frame.width < 0 or frame.height < 0:
            patch = np.rot90(patch, 1 if frame.width < 0 else -1)
        out.append(np.ascontiguousarray(patch))
    return out


def image_format_extension(image_format: int) -> str:
    """File extension for an encoded-image texture format."""
    return _FIF_EXTENSIONS.get(image_format, ".png")
