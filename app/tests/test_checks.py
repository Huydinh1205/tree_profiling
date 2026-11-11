"""Checks before a run, settings, 3D view data (test plan A-13, A-15; spec HOME-3, NEW-3, SET-1, 6.7)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import checks, settings, viewdata
from tests.helpers import write_las_stub

try:
    import laspy
    import numpy as np
except ImportError:
    laspy = None

GB = checks.GB


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)


class Machine(Tmp):                                                              # HOME-3
    def levels(self, answers, **kw):
        run = lambda cmd: answers.get(cmd[0] if cmd[0] != "docker" else " ".join(cmd[:2]), 0)
        got = checks.machine_checks(runs_root=self.tmp, run=run, **kw)
        return {c.key: c.level for c in got}, got

    def test_all_good(self):
        levels, got = self.levels({}, warn_free_gb=0)
        self.assertEqual(levels, {"docker": "ok", "gpu": "ok", "model": "ok", "disk": "ok"})

    def test_docker_not_running(self):
        levels, got = self.levels({"docker info": 1}, warn_free_gb=0)
        self.assertEqual((levels["docker"], "model" in levels), ("error", False))
        self.assertIn("Open Docker Desktop", next(c.help for c in got if c.key == "docker"))

    def test_no_graphics_card_and_model_missing(self):
        levels, _ = self.levels({"nvidia-smi": -1, "docker image": 1}, warn_free_gb=0)
        self.assertEqual((levels["gpu"], levels["model"]), ("error", "warning"))

    def test_disk_nearly_full(self):
        levels, _ = self.levels({}, warn_free_gb=10 ** 9)
        self.assertEqual(levels["disk"], "warning")

    def test_shared_drive(self):
        levels, _ = self.levels({}, warn_free_gb=0, shared_root=str(self.tmp))
        self.assertEqual(levels["shared"], "ok")
        levels, _ = self.levels({}, warn_free_gb=0, shared_root=str(self.tmp / "gone"))
        self.assertEqual(levels["shared"], "warning")


class OneFile(Tmp):                                                              # NEW-2, NEW-3, A-13
    def report(self, name="cloud.laz", points=1000, crs=("GDA2020 / MGA zone 54", False), free=10_000 * GB, **kw):
        path = write_las_stub(self.tmp / name, points, laz=name.endswith(".laz"))
        return checks.file_checks(path, runs_root=self.tmp, crs_reader=lambda p: crs, free_reader=lambda p: free, **kw)

    def level(self, rep, key):
        return next((c.level for c in rep.checks if c.key == key), None)

    def test_good_file(self):
        rep = self.report(machine=[])
        self.assertTrue(rep.can_start)
        self.assertEqual([self.level(rep, k) for k in ("read", "crs", "disk", "machine")], ["ok"] * 4)
        self.assertEqual((rep.header.points, rep.crs_name), (1000, "GDA2020 / MGA zone 54"))

    def test_not_a_point_cloud(self):
        path = self.tmp / "notes.laz"
        path.write_text("just text " * 50)
        rep = checks.file_checks(path, runs_root=self.tmp)
        self.assertEqual((rep.can_start, self.level(rep, "read")), (False, "error"))

    def test_cut_file_and_missing_file(self):
        (self.tmp / "cut.las").write_bytes(b"LASF" + b"\0" * 20)
        for name in ("cut.las", "missing.las"):
            self.assertFalse(checks.file_checks(self.tmp / name, runs_root=self.tmp).can_start, name)

    def test_no_points(self):
        self.assertFalse(self.report(points=0).can_start)

    def test_degrees_block_the_run(self):
        rep = self.report(crs=("WGS 84", True))
        self.assertEqual((rep.can_start, self.level(rep, "crs")), (False, "error"))

    def test_no_coordinate_system_is_a_warning(self):
        rep = self.report(crs=(None, None))
        self.assertEqual((rep.can_start, self.level(rep, "crs")), (True, "warning"))

    def test_coordinate_system_cannot_be_checked(self):
        rep = self.report(crs=("?", None))
        self.assertEqual((rep.can_start, self.level(rep, "crs")), (True, "warning"))

    def test_space_needed_rule(self):                                            # spec A2
        self.assertEqual(checks.space_needed(2 * GB, "a.laz"), 22 * GB)
        self.assertEqual(checks.space_needed(2 * GB, "a.LAS"), 14 * GB)

    def test_not_enough_space(self):
        rep = self.report(free=5 * GB)
        self.assertEqual((rep.can_start, self.level(rep, "disk")), (False, "error"))
        self.assertIn("GB needed", next(c.help for c in rep.checks if c.key == "disk"))

    def test_nearly_full_afterwards(self):
        rep = self.report(free=60 * GB, warn_free_gb=100)
        self.assertEqual((rep.can_start, self.level(rep, "disk")), (True, "warning"))

    def test_docker_problem_blocks(self):
        machine = [checks.Check("docker", "error", "Docker is not running", "Open Docker Desktop")]
        rep = self.report(machine=machine)
        self.assertEqual((rep.can_start, self.level(rep, "docker"), self.level(rep, "machine")), (False, "error", None))

    def test_already_finished_is_only_a_hint(self):
        rep = self.report(already_finished=True)
        self.assertEqual((rep.can_start, self.level(rep, "duplicate")), (True, "warning"))

    @unittest.skipIf(laspy is None, "laspy is not installed here")
    def test_real_coordinate_systems(self):
        try:
            from pyproj import CRS
        except ImportError:
            self.skipTest("pyproj is not installed here")
        for epsg, geographic in ((7854, False), (4326, True)):
            hdr = laspy.LasHeader(point_format=7, version="1.4")
            hdr.add_crs(CRS.from_epsg(epsg))
            las = laspy.LasData(hdr)
            las.x, las.y, las.z = np.arange(10.0), np.arange(10.0), np.ones(10)
            las.write(str(self.tmp / f"{epsg}.las"))
            name, geo = checks.read_crs(self.tmp / f"{epsg}.las")
            self.assertEqual(geo, geographic, name)
        las = laspy.LasData(laspy.LasHeader(point_format=7, version="1.4"))
        las.x, las.y, las.z = np.arange(10.0), np.arange(10.0), np.ones(10)
        las.write(str(self.tmp / "none.las"))
        self.assertEqual(checks.read_crs(self.tmp / "none.las"), (None, None))
        self.assertEqual(checks.read_crs(self.tmp / "not_there.las"), ("?", None))


class SettingsFile(Tmp):                                                         # SET-1 to SET-4
    def test_defaults_then_saved_values_come_back(self):
        s = settings.Settings.load(self.tmp)
        self.assertEqual((Path(s.runs_root), s.warn_free_gb, s.shared_root), (self.tmp / "runs", 100, ""))
        self.assertTrue(s.script_python and s.scripts_dir.endswith("processes"))
        s.shared_root, s.warn_free_gb = "//server/share", 50
        s.save()
        again = settings.Settings.load(self.tmp)
        self.assertEqual((again.shared_root, again.warn_free_gb), ("//server/share", 50))
        self.assertEqual([p.name for p in (self.tmp / "state").iterdir()], ["settings.json"])

    def test_damaged_file_gives_defaults(self):
        (self.tmp / "state").mkdir()
        (self.tmp / "state" / "settings.json").write_text("{ broken")
        self.assertEqual(settings.Settings.load(self.tmp).warn_free_gb, 100)

    def test_runs_must_stay_on_a_local_disk(self):
        self.assertIsNone(settings.runs_root_problem("C:\\TreeProfiling\\runs"))
        self.assertIn("OneDrive", settings.runs_root_problem("C:\\Users\\me\\OneDrive - Company\\runs"))
        self.assertIn("network", settings.runs_root_problem("\\\\server\\share\\runs"))


@unittest.skipIf(laspy is None, "laspy is not installed here")
class ViewData(Tmp):                                                             # A-15, spec 6.7
    def make(self, name="x_info.laz", per_tree=(300, 50, 3), ground=500):
        hdr = laspy.LasHeader(point_format=7, version="1.4")
        hdr.add_extra_dim(laspy.ExtraBytesParams(name="TreeID", type=np.int32))
        hdr.scales = [0.001, 0.001, 0.001]
        rng = np.random.default_rng(1)
        xs, ys, zs, ids = [rng.uniform(0, 100, ground)], [rng.uniform(0, 50, ground)], [np.zeros(ground)], [np.zeros(ground)]
        for i, n in enumerate(per_tree, 1):
            xs.append(10 * i + rng.normal(0, 1, n))
            ys.append(10 * i + rng.normal(0, 1, n))
            zs.append(rng.uniform(100, 100 + 5 * i, n))
            ids.append(np.full(n, i))
        las = laspy.LasData(hdr)
        las.x, las.y, las.z = np.concatenate(xs), np.concatenate(ys), np.concatenate(zs)
        las.TreeID = np.concatenate(ids).astype(np.int32)
        path = self.tmp / name
        try:
            las.write(str(path))
        except Exception:
            path = path.with_suffix(".las")                    # no LAZ backend here
            las.write(str(path))
        return path

    def test_points_of_each_tree(self):
        info = viewdata.build(self.make(), self.tmp / "view.npz", chunk=200)    # many small blocks
        self.assertEqual((info["trees"], info["points_kept"]), (3, 353))
        view = viewdata.open_view(self.tmp / "view.npz")
        self.assertEqual((len(view), len(view.tree(1)), len(view.tree(2)), len(view.tree(3))), (3, 300, 50, 3))
        self.assertIsNone(view.tree(99))
        pts = view.tree(2)
        self.assertAlmostEqual(float(pts[:, 2].min()), 0.0, places=3)           # the tree stands on z = 0
        self.assertLess(abs(float(pts[:, 0].mean())), 0.01)
        self.assertLessEqual(float(pts[:, 2].max()), 10.0)
        self.assertEqual(len(viewdata.height_colours(pts[:, 2])), 50)

    def test_big_tree_is_sampled(self):
        info = viewdata.build(self.make(per_tree=(1000,)), self.tmp / "view.npz", max_points=100, chunk=300)
        self.assertEqual(info["points_kept"], 100)
        pts = viewdata.open_view(self.tmp / "view.npz").tree(1)
        self.assertEqual(len(pts), 100)
        self.assertGreater(float(pts[:, 2].max()), 3.0)                         # a sample of the whole tree, not its start

    def test_surveyed_area(self):
        info = viewdata.build(self.make(ground=20000), self.tmp / "view.npz")
        self.assertAlmostEqual(info["area_ha"], 0.5, delta=0.03)                # 100 m x 50 m

    def test_no_part_file_left_and_bad_file(self):
        viewdata.build(self.make(), self.tmp / "out" / "view.npz")
        self.assertEqual([p.name for p in (self.tmp / "out").iterdir()], ["view.npz"])
        self.assertIsNone(viewdata.open_view(self.tmp / "nothing.npz"))
        with self.assertRaises(Exception):
            viewdata.build(write_las_stub(self.tmp / "stub.las", 10), self.tmp / "v.npz")


if __name__ == "__main__":
    unittest.main()
