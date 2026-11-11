"""Read the fixed header of a LAS / LAZ file without any library and without reading the points.

Used to check that an output of the pipeline holds exactly the input's points (spec 6.3) and for the
quick facts shown before a run (point count, extent). A LAZ file has the same header as a LAS file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class LasHeaderError(Exception):
    pass


@dataclass(frozen=True)
class LasHeader:
    version: tuple                  # (1, 4)
    points: int
    point_format: int
    compressed: bool                # True for LAZ
    min_xyz: tuple
    max_xyz: tuple

    @property
    def width(self) -> float:
        return self.max_xyz[0] - self.min_xyz[0]

    @property
    def height(self) -> float:
        return self.max_xyz[1] - self.min_xyz[1]

    @property
    def bbox_area_m2(self) -> float:
        return max(self.width, 0.0) * max(self.height, 0.0)

    @property
    def bbox_density(self) -> float:
        """Points per m2 of the bounding box (a low estimate for an irregular flight)."""
        return self.points / self.bbox_area_m2 if self.bbox_area_m2 > 0 else 0.0


def read_header(path) -> LasHeader:
    try:
        with open(Path(path), "rb") as f:
            head = f.read(375)
    except OSError as e:
        raise LasHeaderError(f"Cannot read {path}: {e}") from None
    if len(head) < 227 or head[:4] != b"LASF":
        raise LasHeaderError(f"{Path(path).name} is not a LAS or LAZ file.")
    major, minor = head[24], head[25]
    header_size = struct.unpack_from("<H", head, 94)[0]
    fmt = head[104]
    points = struct.unpack_from("<I", head, 107)[0]
    if (major, minor) >= (1, 4) and header_size >= 375 and len(head) >= 255:
        points64 = struct.unpack_from("<Q", head, 247)[0]
        if points64:
            points = points64
    max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<6d", head, 179)
    return LasHeader((major, minor), int(points), fmt & 0x3F, bool(fmt & 0x80),
                     (min_x, min_y, min_z), (max_x, max_y, max_z))


def point_count(path) -> int:
    return read_header(path).points
