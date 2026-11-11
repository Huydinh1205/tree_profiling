"""Crown outlines read from the shapefile without a GIS library (RES-3)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import crowns
from tests.helpers import write_crowns_shapefile

SQUARE = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
TRIANGLE = [(10.0, 10.0), (16.0, 10.0), (13.0, 15.0)]


class ReadCrowns(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.shp = self.tmp / "site_crowns.shp"

    def test_outlines_come_back_by_tree_id(self):
        write_crowns_shapefile(self.shp, [(7, SQUARE), (3, TRIANGLE)])
        self.assertEqual(crowns.read_crowns(self.shp), {7: SQUARE, 3: TRIANGLE})

    def test_polygons_with_height_are_read_too(self):                   # tree_info.py writes PolygonZ
        write_crowns_shapefile(self.shp, [(1, SQUARE)], z=123.4)
        self.assertEqual(crowns.read_crowns(self.shp), {1: SQUARE})

    def test_no_trees(self):
        write_crowns_shapefile(self.shp, [])
        self.assertEqual(crowns.read_crowns(self.shp), {})

    def test_a_file_that_is_not_a_shapefile(self):
        for ext in (".shp", ".dbf"):
            self.shp.with_suffix(ext).write_bytes(b"shape" + ext.encode())
        with self.assertRaises(crowns.CrownsError):
            crowns.read_crowns(self.shp)

    def test_missing_table(self):
        write_crowns_shapefile(self.shp, [(1, SQUARE)])
        self.shp.with_suffix(".dbf").unlink()
        with self.assertRaises(crowns.CrownsError):
            crowns.read_crowns(self.shp)

    def test_cut_short(self):
        write_crowns_shapefile(self.shp, [(1, SQUARE), (2, TRIANGLE)])
        data = self.shp.read_bytes()
        self.shp.write_bytes(data[:-20])
        with self.assertRaises(crowns.CrownsError):
            crowns.read_crowns(self.shp)

    def test_rows_and_outlines_must_match(self):
        write_crowns_shapefile(self.shp, [(1, SQUARE), (2, TRIANGLE)])
        other = self.tmp / "other.shp"
        write_crowns_shapefile(other, [(1, SQUARE)])
        shutil.copy(other.with_suffix(".dbf"), self.shp.with_suffix(".dbf"))
        with self.assertRaises(crowns.CrownsError):
            crowns.read_crowns(self.shp)


class Shapes(unittest.TestCase):
    def test_simplify_keeps_the_number_of_points_fixed(self):
        ring = [(float(i), 0.0) for i in range(100)]
        self.assertEqual(crowns.simplify(ring, 4), [(0.0, 0.0), (25.0, 0.0), (50.0, 0.0), (75.0, 0.0)])
        self.assertEqual(crowns.simplify(TRIANGLE, 5), TRIANGLE + [TRIANGLE[-1]] * 2)
        self.assertEqual(crowns.simplify(SQUARE, 4), SQUARE)

    def test_circle(self):
        pts = crowns.circle(10, 20, 2, 8)
        self.assertEqual(len(pts), 8)
        for x, y in pts:
            self.assertAlmostEqual((x - 10) ** 2 + (y - 20) ** 2, 4.0)


if __name__ == "__main__":
    unittest.main()
