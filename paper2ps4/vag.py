"""VAG (PS-ADPCM) encoder for theme sound effects.

Borrowed and adapted from PS4-Ultimate-Theme-Creator/app/vag.py (MIT).

Each 16-byte block holds 28 samples compressed to 4 bits with a per-block
predictor and shift. Starts with a "VAGp" header (48 bytes, big-endian) and a
silence block.
"""

from __future__ import annotations

import struct

import numpy as np

MAGIC = b"VAGp"
VERSION = 0x20
HEADER_SIZE = 48
SAMPLES_PER_BLOCK = 28
BLOCK_SIZE = 16

FILTERS = np.array(
    [[0, 0], [60, 0], [115, -52], [98, -55], [122, -60]], dtype=np.float64
) / 64.0

FLAG_END = 0x01
FLAG_NONE = 0x00


def _encode_block(samples: np.ndarray, hist: list[float]) -> tuple[bytes, list[float]]:
    best = None
    for predictor, (c0, c1) in enumerate(FILTERS):
        h0, h1 = hist
        residuals = np.empty(len(samples))
        p0, p1 = h0, h1
        for i, s in enumerate(samples):
            residuals[i] = s - (p0 * c0 + p1 * c1)
            p1, p0 = p0, s

        peak = np.abs(residuals).max()
        shift = 12
        while shift > 0 and 7.0 * (1 << (12 - shift)) < peak:
            shift -= 1
        scale = 1 << (12 - shift)

        nibbles = np.empty(len(samples), dtype=np.int32)
        d0, d1 = h0, h1
        error = 0.0
        for i, s in enumerate(samples):
            predicted = d0 * c0 + d1 * c1
            q = int(np.clip(round((s - predicted) / scale), -8, 7))
            nibbles[i] = q
            decoded = predicted + q * scale
            decoded = float(np.clip(decoded, -32768, 32767))
            error += (s - decoded) ** 2
            d1, d0 = d0, decoded

        if best is None or error < best[0]:
            best = (error, predictor, shift, nibbles, [d0, d1])

    _err, predictor, shift, nibbles, new_hist = best
    out = bytearray(BLOCK_SIZE)
    out[0] = (predictor << 4) | shift
    out[1] = FLAG_NONE
    for i in range(0, SAMPLES_PER_BLOCK, 2):
        low = int(nibbles[i]) & 0x0F
        high = int(nibbles[i + 1]) & 0x0F
        out[2 + i // 2] = (high << 4) | low
    return bytes(out), new_hist


def encode(pcm: np.ndarray, sample_rate: int, name: str = "se") -> bytes:
    """PCM mono 16-bit -> complete .vag file."""
    if pcm.ndim != 1:
        raise ValueError("VAG is mono: pass a single channel")

    samples = pcm.astype(np.float64)
    remainder = len(samples) % SAMPLES_PER_BLOCK
    if remainder:
        samples = np.pad(samples, (0, SAMPLES_PER_BLOCK - remainder))

    blocks = bytearray()
    blocks += bytes(BLOCK_SIZE)  # leading silence block

    hist = [0.0, 0.0]
    for start in range(0, len(samples), SAMPLES_PER_BLOCK):
        block, hist = _encode_block(samples[start:start + SAMPLES_PER_BLOCK], hist)
        blocks += block

    if len(blocks) > BLOCK_SIZE:
        blocks[-BLOCK_SIZE + 1] = FLAG_END

    header = (
        MAGIC
        + struct.pack(">I", VERSION)
        + struct.pack(">I", 0)
        + struct.pack(">I", len(blocks))
        + struct.pack(">I", sample_rate)
        + bytes(12)
        + name.encode("ascii", "replace")[:16].ljust(16, b"\x00")
    )
    assert len(header) == HEADER_SIZE, len(header)
    return header + bytes(blocks)
