"""Writes a small polygon shapefile (.shp, .shx, .dbf) the way tree_info.py's crown outlines look,
without any GIS library. Used by the stand-in tree_info.py and by the tests of the crown map."""
import struct
from pathlib import Path


def write_crowns(shp_path, crowns, z=None):
    """crowns: list of (tree_id, [(x, y), ...]); z: write PolygonZ with this height when given."""
    shp_path = Path(shp_path)
    shape_type = 15 if z is not None else 5
    records, offsets = [], []
    pos = 100
    for number, (tree_id, ring) in enumerate(crowns, 1):
        pts = list(ring) + [ring[0]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        body = struct.pack("<i4dii", shape_type, min(xs), min(ys), max(xs), max(ys), 1, len(pts)) + struct.pack("<i", 0)
        body += b"".join(struct.pack("<2d", x, y) for x, y in pts)
        if z is not None:
            body += struct.pack("<2d", z, z) + struct.pack(f"<{len(pts)}d", *([z] * len(pts)))
        records.append(struct.pack(">ii", number, len(body) // 2) + body)
        offsets.append((pos // 2, len(body) // 2))
        pos += 8 + len(body)
    allx = [p[0] for _, r in crowns for p in r] or [0.0]
    ally = [p[1] for _, r in crowns for p in r] or [0.0]

    def header(length_bytes):
        h = bytearray(100)
        struct.pack_into(">i", h, 0, 9994)
        struct.pack_into(">i", h, 24, length_bytes // 2)
        struct.pack_into("<ii", h, 28, 1000, shape_type)
        struct.pack_into("<4d", h, 36, min(allx), min(ally), max(allx), max(ally))
        return bytes(h)

    shp_path.write_bytes(header(pos) + b"".join(records))
    shp_path.with_suffix(".shx").write_bytes(header(100 + 8 * len(crowns)) +
                                             b"".join(struct.pack(">ii", o, n) for o, n in offsets))
    fields = [("tree_uid", "C", 24), ("tree_id", "N", 10)]
    record_len = 1 + sum(f[2] for f in fields)
    head = bytearray(32)
    head[0] = 3
    struct.pack_into("<IHH", head, 4, len(crowns), 32 + 32 * len(fields) + 1, record_len)
    out = bytes(head)
    for name, kind, length in fields:
        d = bytearray(32)
        d[0:len(name)] = name.encode("ascii")
        d[11] = ord(kind)
        d[16] = length
        out += bytes(d)
    out += b"\x0d"
    for tree_id, _ in crowns:
        out += b" " + f"TREE{tree_id:05d}".ljust(24).encode("ascii") + str(tree_id).rjust(10).encode("ascii")
    shp_path.with_suffix(".dbf").write_bytes(out + b"\x1a")
