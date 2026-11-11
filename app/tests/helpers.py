"""Small helpers shared by the tests. No point cloud library is needed: the tests build LAS headers
by hand and use tiny stand-in files."""
import importlib.util
import struct
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_shp", Path(__file__).parent / "fake_pipeline" / "_shp.py")
_shp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shp)
write_crowns_shapefile = _shp.write_crowns          # (shp path, [(tree_id, [(x, y), ...])], z=None)


def las_header_bytes(points, version=(1, 4), fmt=7, laz=False, bbox=(0.0, 0.0, 0.0, 100.0, 50.0, 30.0)):
    """Bytes of a LAS header (no points) saying the file holds `points` points."""
    size = 375 if version >= (1, 4) else 227
    h = bytearray(size)
    h[0:4] = b"LASF"
    h[24], h[25] = version
    struct.pack_into("<H", h, 94, size)
    h[104] = fmt | (0x80 if laz else 0)
    min_x, min_y, min_z, max_x, max_y, max_z = bbox
    struct.pack_into("<6d", h, 179, max_x, min_x, max_y, min_y, max_z, min_z)
    if version >= (1, 4):
        struct.pack_into("<Q", h, 247, points)            # the old 32-bit count stays 0, as for formats 6+
    else:
        struct.pack_into("<I", h, 107, points)
    return bytes(h)


def write_las_stub(path, points, **kw):
    Path(path).write_bytes(las_header_bytes(points, **kw))
    return Path(path)


def make_workdir(folder, points=1000, with_prj=True):
    """A run's working folder as the three scripts leave it (tiny files)."""
    w = Path(folder)
    (w / "log").mkdir(parents=True, exist_ok=True)
    write_las_stub(w / "sat_result.laz", points, laz=True)
    write_las_stub(w / "sat_result_merged.laz", points, laz=True)
    write_las_stub(w / "sat_result_merged_info.laz", points, laz=True)
    (w / "sat_result_merged_trees.csv").write_text(TREES_CSV, encoding="utf-8")
    for ext in (".shp", ".shx", ".dbf"):
        (w / f"sat_result_merged_crowns{ext}").write_bytes(b"shape" + ext.encode())
    if with_prj:
        (w / "sat_result_merged_crowns.prj").write_text("PROJCS[...]", encoding="utf-8")
    return w


def folder_snapshot(folder):
    """name -> bytes of every file under a folder, to prove it was not changed."""
    folder = Path(folder)
    return {str(p.relative_to(folder)): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


TREES_CSV = (
    "tree_uid,tree_id,top_x,top_y,top_z,height_m,crown_diam_m,crown_width_max_m,crown_area_m2,crown_volume_m3,"
    "foliage_volume_m3,crown_base_m,crown_depth_m,canopy_density_pct,nn_spacing_m,n_points,h_p25_m,h_p50_m,"
    "h_p75_m,height_sd_m,point_density_m2,qc_flag,species_code,species_prob,foliage_condition,thermal_anomaly,"
    "confidence,koala_value\n"
    "TEST_SITE_TREE00001,1,500010.5,6250020.25,131.2,10.0,4.0,4.5,12.6,60.0,20.0,3.0,7.0,33.3,5.0,800,5.0,6.5,"
    "8.0,2.0,63.5,ok,,,,,,\n"
    "TEST_SITE_TREE00002,2,500030.0,6250040.0,142.0,20.0,6.0,7.0,28.3,300.0,90.0,6.0,14.0,30.0,5.0,2500,11.0,"
    "14.0,17.0,3.5,88.3,ok,,,,,,\n"
    "TEST_SITE_TREE00003,3,500001.0,6250001.0,151.0,30.0,8.0,9.5,50.3,900.0,250.0,9.0,21.0,27.8,12.0,6000,17.0,"
    "21.0,26.0,5.0,119.3,edge,,,,,,\n"
    "TEST_SITE_TREE00004,4,500050.0,6250060.0,124.0,,1.0,1.2,0.8,,,,,,9.0,12,,,,,15.0,few_points,,,,,,\n"
)
