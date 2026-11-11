"""Reading and writing the PS4's XDM (.mdx) model format.

Borrowed and adapted from PS4-Ultimate-Theme-Creator/app/mdx.py (MIT).

A RAF scene uses one shared textured plane; what changes per frame is the
material/actor alpha. The format is a tree of records with properties.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

MAGIC = b"XDM.00.1MSP\x00" + b"\x00" * 4

TAG_ROOT = 0x0002
TAG_MODEL = 0x0010
TAG_NODE = 0x0011
TAG_GEOMETRY = 0x0012
TAG_MESH = 0x0013
TAG_ARRAYS = 0x0014
TAG_MATERIAL = 0x0016

PROP_PARENT = 0x8440
PROP_TRANSLATION = 0x8443
PROP_ROTATION = 0x8444
PROP_SCALE = 0x844B
PROP_GEOMETRY_REF = 0x847F
PROP_MATERIAL_REF = 0x84C0
PROP_ARRAYS_REF = 0x84C1
PROP_INDICES = 0x84E0

_MIN_NAME_FIELD = 8


class MdxError(ValueError):
    """Malformed .mdx file."""


# --- half floats ----------------------------------------------------------

def f2h(value: float) -> int:
    """float -> half (round-to-even, IEEE 754)."""
    packed = struct.unpack("<I", struct.pack("<f", value))[0]
    sign = (packed >> 16) & 0x8000
    exp = (packed >> 23) & 0xFF
    mant = packed & 0x7FFFFF
    if exp == 0xFF:
        return sign | 0x7C00 | (0x200 if mant else 0)
    e = exp - 127 + 15
    if e >= 0x1F:
        return sign | 0x7C00
    if e <= 0:
        if e < -10:
            return sign
        mant |= 0x800000
        shift = 14 - e
        half = mant >> shift
        if (mant >> (shift - 1)) & 1:
            half += 1
        return sign | half
    half = sign | (e << 10) | (mant >> 13)
    if (mant >> 12) & 1:
        half += 1
    return half & 0xFFFF


def h2f(u: int) -> float:
    sign = (u >> 15) & 1
    exp = (u >> 10) & 0x1F
    mant = u & 0x3FF
    if exp == 0:
        value = (mant / 1024.0) * 2 ** -14
    elif exp == 31:
        value = float("inf") if mant == 0 else float("nan")
    else:
        value = (1 + mant / 1024.0) * 2 ** (exp - 15)
    return -value if sign else value


# --- tree ----------------------------------------------------------------

@dataclass
class Prop:
    pid: int
    data: bytes

    @property
    def size(self) -> int:
        return 4 + len(self.data)

    def pack(self) -> bytes:
        return struct.pack("<HH", self.pid, self.size) + self.data


@dataclass
class Record:
    tag: int
    name: str = ""
    children: list = field(default_factory=list)
    payload: bytes = b""  # only TAG_ARRAYS

    @property
    def name_field(self) -> int:
        if not self.name:
            return 0
        return max(_MIN_NAME_FIELD, ((len(self.name) + 1 + 3) // 4) * 4)

    @property
    def header_size(self) -> int:
        return 0x10 + self.name_field

    def pack(self) -> bytes:
        body = self.payload + b"".join(c.pack() for c in self.children)
        hdr = self.header_size
        total = hdr + len(body)
        if self.tag == TAG_ARRAYS:
            a, b = hdr + 12, total
        else:
            a, b = hdr, hdr
        out = struct.pack("<HHIII", self.tag, hdr, a, b, total)
        if self.name_field:
            out += self.name.encode("ascii").ljust(self.name_field, b"\x00")
        return out + body


def _parse_body(data: bytes, start: int, end: int) -> list:
    out: list = []
    off = start
    while off + 4 <= end:
        first = struct.unpack_from("<H", data, off)[0]
        if first & 0x8000:
            pid, size = struct.unpack_from("<HH", data, off)
            if size < 4 or off + size > end:
                raise MdxError(f"property {pid:#06x} has an invalid size at {off:#x}")
            out.append(Prop(pid, data[off + 4:off + size]))
            off += size
            continue
        if off + 16 > end:
            raise MdxError(f"truncated record at {off:#x}")
        tag, hdr = struct.unpack_from("<HH", data, off)
        _a, _b, total = struct.unpack_from("<III", data, off + 4)
        if hdr < 0x10 or total < hdr or off + total > end:
            raise MdxError(f"record {tag:#06x} has an invalid header at {off:#x}")
        name = ""
        if hdr > 0x10:
            name = data[off + 0x10:off + hdr].split(b"\x00")[0].decode("ascii", "replace")
        rec = Record(tag, name)
        if tag == TAG_ARRAYS:
            rec.payload = data[off + hdr:off + total]
        else:
            rec.children = _parse_body(data, off + hdr, off + total)
        out.append(rec)
        off += total
    return out


def parse(data: bytes) -> list:
    if data[:len(MAGIC)] != MAGIC:
        raise MdxError("not an .mdx (missing the XDM.00.1 header)")
    return _parse_body(data, len(MAGIC), len(data))


def build(records: list) -> bytes:
    return MAGIC + b"".join(r.pack() for r in records)


# --- building a plane -----------------------------------------------------

def vertex(x: float, y: float, z: float, u: float, v: float,
           normal: bytes = b"\x00\x81\x00\x00") -> bytes:
    return (
        struct.pack("<HHH", f2h(x), f2h(y), f2h(z))
        + normal
        + struct.pack("<HH", f2h(u), f2h(v))
    )


def quad_arrays(half_w: float, half_h: float, depth: float,
                uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)) -> Record:
    u0, v0, u1, v1 = uv
    verts = b"".join([
        vertex(-half_w, depth, +half_h, u0, v0),
        vertex(-half_w, depth, -half_h, u0, v1),
        vertex(+half_w, depth, -half_h, u1, v1),
        vertex(+half_w, depth, +half_h, u1, v0),
    ])
    payload = struct.pack("<HHII", 0x0023, 0x0490, 14, 4) + verts
    return Record(TAG_ARRAYS, "arrays-0", payload=payload)


_QUAD_INDICES = struct.pack("<II", 3, 6) + struct.pack(
    "<8H", 1, 0, 0, 1, 2, 0, 2, 3
)


def quad_model(*, node_name: str = "plane", half_w: float = 1.0,
               half_h: float = 0.5625, depth: float = 2.146484,
               uv: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
               scale: float = 100.0) -> bytes:
    """Generate an .mdx with a single textured plane."""
    root2 = math.sqrt(0.5)
    rot = struct.pack("<4f", -root2, 0.0, 0.0, root2)
    scl = struct.pack("<3f", scale, scale, scale)

    radius = math.hypot(half_w * scale, half_h * scale)
    center = (0.0, 0.0, -depth * scale)

    node = Record(TAG_NODE, node_name, [
        Prop(PROP_PARENT, bytes([0x00, 0x10, 0x11, 0x00])),
        Prop(PROP_ROTATION, rot),
        Prop(PROP_SCALE, scl),
        Prop(PROP_GEOMETRY_REF, bytes([0x00, 0x10, 0x12, 0x00])),
    ])
    mesh = Record(TAG_MESH, "mesh-0", [
        Prop(PROP_MATERIAL_REF, bytes([0x00, 0x20, 0x16, 0x00])),
        Prop(PROP_ARRAYS_REF, bytes([0x00, 0x10, 0x14, 0x00])),
        Prop(PROP_INDICES, _QUAD_INDICES),
    ])
    geometry = Record(TAG_GEOMETRY, node_name, [mesh, quad_arrays(half_w, half_h, depth, uv)])

    model = Record(TAG_MODEL, "model-0", [
        Prop(0x8083, struct.pack("<4f", center[0], center[1], center[2], radius)),
        Prop(0x8084, struct.pack("<I4f", 1, 0.0, depth, 0.0, 6.0)),
        Record(TAG_NODE, "RootNode"),
        node,
        geometry,
        Record(TAG_MATERIAL, "material-0"),
    ])
    return build([Record(TAG_ROOT, "", [model])])
