"""Test plan A-11 (spec 6.1, 6.2, 6.3) and the LAS header reader it relies on."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import lasheader, steps
from tests.helpers import las_header_bytes, make_workdir, write_las_stub

try:
    import laspy
    import numpy as np
except ImportError:                                   # the app's own environment has laspy; plain Python may not
    laspy = None


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)


class LasHeader(Tmp):
    def test_las_12(self):
        h = lasheader.read_header(write_las_stub(self.tmp / "a.las", 123456, version=(1, 2), fmt=3))
        self.assertEqual((h.version, h.points, h.point_format, h.compressed), ((1, 2), 123456, 3, False))

    def test_las_14_more_than_4_billion_points(self):
        h = lasheader.read_header(write_las_stub(self.tmp / "a.las", 5_000_000_000))
        self.assertEqual((h.version, h.points), ((1, 4), 5_000_000_000))

    def test_laz(self):
        h = lasheader.read_header(write_las_stub(self.tmp / "a.laz", 1000, laz=True))
        self.assertEqual((h.points, h.point_format, h.compressed), (1000, 7, True))

    def test_extent_and_density(self):
        h = lasheader.read_header(write_las_stub(self.tmp / "a.las", 500000, bbox=(10, 20, 0, 110, 70, 30)))
        self.assertEqual((h.width, h.height, h.bbox_area_m2, h.bbox_density), (100.0, 50.0, 5000.0, 100.0))

    def test_not_a_las_file(self):
        (self.tmp / "a.laz").write_text("this is a text file, not a point cloud " * 20)
        with self.assertRaises(lasheader.LasHeaderError):
            lasheader.read_header(self.tmp / "a.laz")

    def test_cut_file(self):
        (self.tmp / "a.las").write_bytes(las_header_bytes(1000)[:100])
        with self.assertRaises(lasheader.LasHeaderError):
            lasheader.read_header(self.tmp / "a.las")

    def test_missing_file(self):
        with self.assertRaises(lasheader.LasHeaderError):
            lasheader.read_header(self.tmp / "nothing.las")

    @unittest.skipIf(laspy is None, "laspy is not installed here")
    def test_agrees_with_laspy(self):
        for name, fmt, version in (("a.las", 3, "1.2"), ("b.las", 7, "1.4"), ("c.laz", 7, "1.4")):
            hdr = laspy.LasHeader(point_format=fmt, version=version)
            las = laspy.LasData(hdr)
            las.x, las.y, las.z = np.arange(777.0), np.arange(777.0) * 2, np.ones(777)
            try:
                las.write(str(self.tmp / name))
            except Exception:                         # no LAZ backend installed
                continue
            h = lasheader.read_header(self.tmp / name)
            self.assertEqual((h.points, h.point_format, h.compressed), (777, fmt, name.endswith(".laz")))
            self.assertAlmostEqual(h.max_xyz[1], 1552.0, places=2)


class Commands(unittest.TestCase):                                               # spec 6.1
    W = Path("C:/runs/site_1")

    def test_find_trees(self):
        cmd = steps.command("find_trees", python="python", workdir=self.W, input_path="C:/in/cloud.las")
        self.assertEqual(cmd, ["python", "sat_docker.py", "C:/in/cloud.las", "--out", str(self.W / "sat_result.laz"),
                               "--log", str(self.W / "log" / "sat_docker.log")])

    def test_max_points_only_when_changed(self):
        cmd = steps.command("find_trees", python="python", workdir=self.W, input_path="x.las", max_points=10_000_000)
        self.assertEqual(cmd[-2:], ["--max-points", "10000000"])

    def test_join(self):
        self.assertEqual(steps.command("join", python=["py", "-3.10"], workdir=self.W),
                         ["py", "-3.10", "merge_sat.py", str(self.W / "sat_result.laz")])

    def test_measure(self):
        self.assertEqual(steps.command("measure", python="python", workdir=self.W, site="Test Site"),
                         ["python", "tree_info.py", str(self.W / "sat_result_merged.laz"), "--site", "Test Site"])

    def test_other_steps_have_no_script(self):
        for step in ("copy", "prepare_view", "nonsense"):
            with self.assertRaises(ValueError):
                steps.command(step, python="python", workdir=self.W)

    def test_environment(self):                                                  # spec 6.2
        env = steps.child_env({"PATH": "x"})
        self.assertEqual(env, {"PATH": "x", "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})


class StepDone(Tmp):                                                             # A-11
    def setUp(self):
        super().setUp()
        self.w = make_workdir(self.tmp / "run", points=1000)

    def test_all_good(self):
        for step in steps.SCRIPT_STEPS:
            self.assertEqual(steps.check_step_done(step, self.w, 0, 1000), steps.StepCheck(True), step)

    def test_exit_code(self):
        c = steps.check_step_done("join", self.w, 1, 1000)
        self.assertFalse(c.done)
        self.assertIn("exit code 1", c.reason)

    def test_missing_file(self):
        (self.w / "sat_result_merged_trees.csv").unlink()
        c = steps.check_step_done("measure", self.w, 0, 1000)
        self.assertEqual((c.done, c.reason), (False, "sat_result_merged_trees.csv is missing"))

    def test_missing_shapefile_part(self):
        (self.w / "sat_result_merged_crowns.dbf").unlink()
        self.assertFalse(steps.check_step_done("measure", self.w, 0, 1000).done)

    def test_empty_file(self):
        (self.w / "sat_result_merged_trees.csv").write_bytes(b"")
        self.assertFalse(steps.check_step_done("measure", self.w, 0, 1000).done)

    def test_point_count_differs(self):                      # e.g. merge_sat.py stopped while writing
        write_las_stub(self.w / "sat_result_merged.laz", 600, laz=True)
        c = steps.check_step_done("join", self.w, 0, 1000)
        self.assertFalse(c.done)
        self.assertIn("600", c.reason)

    def test_half_written_file(self):
        (self.w / "sat_result.laz").write_bytes(b"LASF" + b"\0" * 50)
        self.assertFalse(steps.check_step_done("find_trees", self.w, 0, 1000).done)

    def test_resume_point(self):
        self.assertEqual(steps.next_step([]), "copy")
        self.assertEqual(steps.next_step(["copy", "find_trees"]), "join")
        self.assertIsNone(steps.next_step(steps.STEP_ORDER))

    def test_labels(self):
        self.assertEqual([steps.STEP_LABELS[s] for s in steps.STEP_ORDER],
                         ["Copy file", "Find trees", "Join split crowns", "Measure trees", "Prepare view"])


if __name__ == "__main__":
    unittest.main()
