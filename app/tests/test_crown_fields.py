"""The crown shapefile written by ../processes/tree_info.py carries every measured column of _trees.csv
(shortened names, at most 10 characters), with the same values, and the app still reads it.
Runs the real merge_sat.py and tree_info.py on the made-up forest; skipped like test_real_scripts.py."""
import csv
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.real_pipeline.prepare import Prepared, script_python, why_not
from treeprof_app import crowns

REASON = why_not()

# (shapefile name, _trees.csv column): what tree_info.SHP_FIELDS must hold
EXPECTED = [
    ("tree_uid", "tree_uid"), ("tree_id", "tree_id"), ("height_m", "height_m"), ("crown_d_m", "crown_diam_m"),
    ("area_m2", "crown_area_m2"), ("volume_m3", "crown_volume_m3"), ("qc_flag", "qc_flag"),
    ("crown_wmax", "crown_width_max_m"), ("foliage_v", "foliage_volume_m3"), ("crown_base", "crown_base_m"),
    ("crown_dep", "crown_depth_m"), ("canopy_pct", "canopy_density_pct"), ("nn_dist_m", "nn_spacing_m"),
    ("n_points", "n_points"), ("h_p25_m", "h_p25_m"), ("h_p50_m", "h_p50_m"), ("h_p75_m", "h_p75_m"),
    ("height_sd", "height_sd_m"), ("pts_m2", "point_density_m2"),
    ("top_x", "top_x"), ("top_y", "top_y"), ("top_z", "top_z"),
]


def read_dbf(path: Path):
    """Field names and records of a .dbf, without any GIS library."""
    data = path.read_bytes()
    n_rec, head_len, rec_len = struct.unpack_from("<IHH", data, 4)
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, chr(data[pos + 11]), data[pos + 16]))
        pos += 32
    records = []
    for i in range(n_rec):
        rec = data[head_len + i * rec_len: head_len + (i + 1) * rec_len]
        if rec[:1] == b"*":
            continue
        values, at = {}, 1
        for name, _, width in fields:
            values[name] = rec[at:at + width].decode("utf-8", "replace").strip()
            at += width
        records.append(values)
    return [f[0] for f in fields], records


@unittest.skipIf(REASON, REASON)
class CrownShapefileFields(unittest.TestCase):
    def test_every_measured_column_in_the_shapefile(self):
        with tempfile.TemporaryDirectory() as tmp:
            made = Prepared(Path(tmp))
            work = Path(tmp) / "work"
            work.mkdir()
            sat = work / "sat_result.laz"
            sat.write_bytes(made.sat.read_bytes())
            for args in (["merge_sat.py", str(sat)], ["tree_info.py", str(work / "sat_result_merged.laz"), "--site", "Test Site"]):
                r = subprocess.run(script_python() + [str(made.scripts / args[0])] + args[1:],
                                   capture_output=True, text=True, timeout=600, cwd=str(made.scripts))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("crown outlines skipped", r.stdout, r.stdout)

            shp = work / "sat_result_merged_crowns.shp"
            names, records = read_dbf(shp.with_suffix(".dbf"))
            self.assertEqual(names, [n for n, _ in EXPECTED])
            self.assertTrue(all(len(n) <= 10 for n in names))

            with open(work / "sat_result_merged_trees.csv", newline="") as fh:
                trees = {row["tree_id"]: row for row in csv.DictReader(fh)}
            self.assertGreater(len(records), 10)
            for rec in records:
                row = trees[rec["tree_id"]]
                for name, col in EXPECTED:
                    if name in ("tree_uid", "qc_flag"):
                        self.assertEqual(rec[name], row[col], (name, rec))
                    elif row[col] == "":
                        self.assertIn(rec[name], ("", "nan", "NaN"), (name, rec))
                    else:
                        self.assertAlmostEqual(float(rec[name]), float(row[col]), places=2, msg=(name, rec))

            outlines = crowns.read_crowns(shp)                # the app's crown map still reads it
            self.assertEqual(len(outlines), len(records))


if __name__ == "__main__":
    unittest.main()
