"""DXT1a (BC1) encoder vectorised with numpy, plus the .dds container.

Borrowed and adapted from PS4-Ultimate-Theme-Creator/app/dxt1.py (MIT).
DXT1 stores each 4x4 pixel block in 8 bytes: two RGB565 colours and 16
2-bit indices. DXT1a is DXT1 using the 3-colours+punchthrough mode on blocks
that contain transparent pixels. The FourCC stays 'DXT1'.
"""

from __future__ import annotations

import struct

import numpy as np

#: Below this alpha a pixel counts as transparent (punchthrough).
ALPHA_THRESHOLD = 128

_POWER_ITERATIONS = 8


def _quantize_565(colors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    c = np.clip(colors, 0.0, 255.0)
    r5 = np.rint(c[..., 0] * (31.0 / 255.0)).astype(np.uint32)
    g6 = np.rint(c[..., 1] * (63.0 / 255.0)).astype(np.uint32)
    b5 = np.rint(c[..., 2] * (31.0 / 255.0)).astype(np.uint32)
    packed = (r5 << 11) | (g6 << 5) | b5
    dequant = np.empty(c.shape, dtype=np.float32)
    dequant[..., 0] = (r5 << 3) | (r5 >> 2)
    dequant[..., 1] = (g6 << 2) | (g6 >> 4)
    dequant[..., 2] = (b5 << 3) | (b5 >> 2)
    return packed.astype(np.uint32), dequant


def _principal_axis(centered: np.ndarray, weights: np.ndarray) -> np.ndarray:
    w = centered * weights[..., None]
    cov = np.einsum("nij,nik->njk", w, w, optimize=True)
    axis = w.max(axis=1) - w.min(axis=1)
    norm = np.linalg.norm(axis, axis=1, keepdims=True)
    axis = np.where(
        norm > 1e-6, axis / np.maximum(norm, 1e-9),
        np.array([1.0, 0.0, 0.0], np.float32),
    )
    for _ in range(_POWER_ITERATIONS):
        axis = np.einsum("nij,nj->ni", cov, axis, optimize=True)
        norm = np.linalg.norm(axis, axis=1, keepdims=True)
        axis = np.where(
            norm > 1e-9, axis / np.maximum(norm, 1e-12),
            np.array([1.0, 0.0, 0.0], np.float32),
        )
    return axis.astype(np.float32)


def compress_dxt1a(rgba: np.ndarray) -> bytes:
    """Compress an RGBA (H, W, 4) uint8 image to raw DXT1a blocks."""
    if rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"expected (H, W, 4) uint8, got {rgba.shape}")

    height, width = rgba.shape[:2]
    pad_h = (-height) % 4
    pad_w = (-width) % 4
    if pad_h or pad_w:
        rgba = np.pad(rgba, ((0, pad_h), (0, pad_w), (0, 0)), mode="edge")
        height, width = rgba.shape[:2]

    bh, bw = height // 4, width // 4
    blocks = (
        rgba.reshape(bh, 4, bw, 4, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(-1, 16, 4)
    )
    n = blocks.shape[0]

    colors = blocks[..., :3].astype(np.float32)
    opaque = blocks[..., 3] >= ALPHA_THRESHOLD
    weights = opaque.astype(np.float32)
    opaque_count = weights.sum(axis=1)

    needs_alpha = ~opaque.all(axis=1)
    all_clear = opaque_count == 0

    safe_count = np.maximum(opaque_count, 1.0)[:, None]
    mean = (colors * weights[..., None]).sum(axis=1) / safe_count

    centered = colors - mean[:, None, :]
    axis = _principal_axis(centered, weights)

    proj = np.einsum("nij,nj->ni", centered, axis, optimize=True)
    big = np.float32(np.inf)
    t_min = np.where(opaque, proj, big).min(axis=1)
    t_max = np.where(opaque, proj, -big).max(axis=1)
    t_min = np.where(all_clear, 0.0, t_min)
    t_max = np.where(all_clear, 0.0, t_max)

    end_a = mean + t_max[:, None] * axis
    end_b = mean + t_min[:, None] * axis

    packed_a, color_a = _quantize_565(end_a)
    packed_b, color_b = _quantize_565(end_b)

    want_greater = ~needs_alpha
    is_greater = packed_a > packed_b
    swap = np.where(want_greater, ~is_greater, is_greater)

    packed0 = np.where(swap, packed_b, packed_a)
    packed1 = np.where(swap, packed_a, packed_b)
    color0 = np.where(swap[:, None], color_b, color_a)
    color1 = np.where(swap[:, None], color_a, color_b)

    four_color = packed0 > packed1

    palette = np.empty((n, 4, 3), dtype=np.float32)
    palette[:, 0] = color0
    palette[:, 1] = color1
    palette[:, 2] = np.where(
        four_color[:, None],
        (2.0 * color0 + color1) / 3.0,
        (color0 + color1) / 2.0,
    )
    palette[:, 3] = np.where(four_color[:, None], (color0 + 2.0 * color1) / 3.0, 0.0)

    diff = colors[:, :, None, :] - palette[:, None, :, :]
    dist = np.einsum("nikc,nikc->nik", diff, diff, optimize=True)
    dist[:, :, 3] = np.where(four_color[:, None], dist[:, :, 3], np.inf)

    indices = np.argmin(dist, axis=2).astype(np.uint32)
    indices = np.where(opaque, indices, np.uint32(3))
    if all_clear.any():
        indices[all_clear] = 3

    shifts = (2 * np.arange(16, dtype=np.uint32))[None, :]
    bits = np.bitwise_or.reduce(indices << shifts, axis=1).astype(np.uint32)

    out = np.empty((n, 8), dtype=np.uint8)
    out[:, 0] = packed0 & 0xFF
    out[:, 1] = (packed0 >> 8) & 0xFF
    out[:, 2] = packed1 & 0xFF
    out[:, 3] = (packed1 >> 8) & 0xFF
    out[:, 4] = bits & 0xFF
    out[:, 5] = (bits >> 8) & 0xFF
    out[:, 6] = (bits >> 16) & 0xFF
    out[:, 7] = (bits >> 24) & 0xFF
    return out.tobytes()


# --- .dds container -------------------------------------------------------

_DDSD_CAPS = 0x1
_DDSD_HEIGHT = 0x2
_DDSD_WIDTH = 0x4
_DDSD_PIXELFORMAT = 0x1000
_DDSD_LINEARSIZE = 0x80000
_DDPF_FOURCC = 0x4
_DDSCAPS_TEXTURE = 0x1000


def dds_header(width: int, height: int, linear_size: int) -> bytes:
    """128-byte DDS header for a DXT1 texture without mipmaps."""
    flags = (_DDSD_CAPS | _DDSD_HEIGHT | _DDSD_WIDTH |
             _DDSD_PIXELFORMAT | _DDSD_LINEARSIZE)
    return b"DDS " + struct.pack(
        "<7I44xIII20xIIII4x",
        124,            # dwSize
        flags,
        height,
        width,
        linear_size,    # dwPitchOrLinearSize
        0,              # dwDepth
        0,              # dwMipMapCount
        32,             # DDS_PIXELFORMAT.dwSize
        _DDPF_FOURCC,
        *struct.unpack("<I", b"DXT1"),
        _DDSCAPS_TEXTURE,
        0, 0, 0,
    )


def encode_dds(rgba: np.ndarray) -> bytes:
    """RGBA image -> complete .dds file (DXT1a, no mipmaps)."""
    height, width = rgba.shape[:2]
    payload = compress_dxt1a(rgba)
    return dds_header(width, height, len(payload)) + payload
