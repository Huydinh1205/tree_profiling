"""Crown outlines for the map on the Results screen (RES-3), read from the *_crowns.shp that
tree_info.py writes. Plain Python: the app does not need a GIS library to show them.

Only what tree_info.py writes is supported: polygons (with or without Z), one record per tree, and a
tree_id column in the .dbf next to the .shp. Anything else raises CrownsError and the map falls back
to one circle per tree.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

POLYGON_TYPES = (5, 15, 25)            # Polygon, PolygonZ, PolygonM
ID_FIELD = "tree_id"


class CrownsError(Exception):
    pass


def _dbf_ids(path: Path) -> list:
    """The tree_id of every record of a .dbf, in file order (None for a deleted or empty one)."""
    data = path.read_bytes()
    if len(data) < 33:
        raise CrownsError(f"{path.name} is not a table")
    count, header_len, record_len = struct.unpack_from("<IHH", data, 4)
    offset, start, width = 32, 1, None
    while offset + 32 <= header_len and data[offset] != 0x0D:
        name = data[offset:offset + 11].split(b"\x00")[0].decode("ascii", "replace").strip().lower()
        length = data[offset + 16]
        if name == ID_FIELD:
            width = length
            break
        start += length
        offset += 32
    if width is None:
        raise CrownsError(f"{path.name} has no {ID_FIELD} column")
    ids = []
    for i in range(count):
        rec = data[header_len + i * record_len: header_len + (i + 1) * record_len]
        if len(rec) < start + width or rec[:1] == b"*":
            ids.append(None)
            continue
        try:
            ids.append(int(float(rec[start:start + width].decode("ascii", "replace").strip())))
        except ValueError:
            ids.append(None)
    return ids


def _area(ring) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _shp_rings(path: Path) -> list:
    """The outer ring of every record of a .shp, in file order (None for an empty shape)."""
    data = path.read_bytes()
    if len(data) < 100 or struct.unpack_from(">i", data, 0)[0] != 9994:
        raise CrownsError(f"{path.name} is not a shapefile")
    if struct.unpack_from("<i", data, 32)[0] not in POLYGON_TYPES:
        raise CrownsError(f"{path.name} does not hold polygons")
    rings, pos = [], 100
    while pos + 8 <= len(data):
        words = struct.unpack_from(">i", data, pos + 4)[0]
        body, pos = pos + 8, pos + 8 + words * 2
        if pos > len(data):
            raise CrownsError(f"{path.name} is cut short")
        if struct.unpack_from("<i", data, body)[0] not in POLYGON_TYPES:
            rings.append(None)
            continue
        n_parts, n_points = struct.unpack_from("<ii", data, body + 36)
        parts = list(struct.unpack_from(f"<{n_parts}i", data, body + 44)) + [n_points]
        xy = struct.unpack_from(f"<{n_points * 2}d", data, body + 44 + 4 * n_parts)
        best = None
        for a, b in zip(parts, parts[1:]):
            ring = [(xy[2 * i], xy[2 * i + 1]) for i in range(a, b)]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]                                   # shapefile rings repeat their first point
            if len(ring) >= 3 and (best is None or _area(ring) > _area(best)):
                best = ring
        rings.append(best)
    return rings


def read_crowns(shp_path) -> dict:
    """tree_id -> outline as a list of (x, y), without the repeated closing point."""
    shp = Path(shp_path)
    try:
        ids = _dbf_ids(shp.with_suffix(".dbf"))
        rings = _shp_rings(shp)
    except (OSError, struct.error) as e:
        raise CrownsError(f"{shp.name} cannot be read: {e}") from e
    if len(ids) != len(rings):
        raise CrownsError(f"{shp.name}: {len(rings)} outlines but {len(ids)} table rows")
    return {tid: ring for tid, ring in zip(ids, rings) if tid is not None and ring}


def simplify(ring: list, n: int) -> list:
    """Exactly n points: evenly picked along the outline when it has more, the last one repeated when
    it has fewer (the map draws every outline with the same number of points)."""
    if len(ring) > n:
        return [ring[int(i * len(ring) / n)] for i in range(n)]
    return list(ring) + [ring[-1]] * (n - len(ring))


def circle(x: float, y: float, radius: float, n: int) -> list:
    """Stands in for the outline of a tree that has none."""
    return [(x + radius * math.cos(2 * math.pi * i / n), y + radius * math.sin(2 * math.pi * i / n)) for i in range(n)]
