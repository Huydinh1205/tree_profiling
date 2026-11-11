"""The app driving the REAL merge_sat.py and tree_info.py of ../processes on a small made-up forest
(test plan C-01 to C-04 at small scale). Only the Docker step is replaced, by a stand-in that hands
over a ready result, so this runs on any computer that has the scripts' libraries.

Skipped when the processes folder or the libraries are not there. Two optional environment variables:
    TREEPROF_SCRIPTS_DIR      folder with the real scripts (default: ../processes next to the app)
    TREEPROF_SCRIPT_PYTHON    interpreter that has the scripts' libraries (default: what the app uses,
                              "py -3.10" on Windows, this Python elsewhere)
"""
import csv
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from treeprof_app import checks, projectfile, saving, steps
from treeprof_app.appstate import AppState
from treeprof_app.runner import RunnerConfig
from tests.real_pipeline.prepare import Prepared, script_python, why_not

REASON = why_not()


@unittest.skipIf(REASON, REASON)
class RealScripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        made = Prepared(cls.tmp)
        cls.job, cls.input = made.job, made.input
        cls.points, cls.planted, cls.pieces = made.points, made.planted, made.pieces
        cfg = RunnerConfig(python=script_python(), scripts_dir=made.scripts, needs_copy=lambda p: True,
                           stop_containers=lambda: None, copy_retry_wait=0,
                           extra_env={"TREEPROF_TEST_SAT_RESULT": str(made.sat)})
        cls.state = AppState(cls.tmp / "home", runner_config=cfg, machine_runner=lambda cmd: 0)
        cls.addClassCleanup(cls.state.shutdown)
        cls.run_id = cls.state.add_run(cls.input, site="Test Site", run_by="Sam", capture_date="2025-02-13")
        end = time.time() + 600
        while time.time() < end and cls.state.store.get(cls.run_id)["status"] not in ("finished", "failed", "cancelled"):
            time.sleep(0.1)
        cls.view = cls.state.run_view(cls.run_id)
        cls.work = Path(cls.view["workdir"])

    def results(self):
        return self.state.open_results(self.state.project_path_for(self.run_id))

    def test_1_the_run_finishes_with_every_step_done(self):
        log = "\n".join(self.state.log_tail(self.run_id, 80))
        self.assertEqual(self.view["status"], "finished", log)
        self.assertEqual(set(self.state.store.steps_done(self.run_id)), set(steps.STEP_ORDER))
        for name in (steps.SAT_RAW, steps.SAT_MERGED, steps.INFO_LAZ, steps.TREES_CSV, steps.CROWNS_SHP):
            self.assertTrue((self.work / name).is_file(), name)

    def test_2_the_app_reads_what_the_scripts_print(self):
        log = "\n".join(self.state.log_tail(self.run_id, 200))
        self.assertIn("merged into", log)                              # merge_sat.py
        self.assertIn("trees measured", log)                           # tree_info.py
        with open(self.work / steps.TREES_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(self.view["trees"], len(rows))
        self.assertTrue(self.planted <= len(rows) <= self.pieces, len(rows))     # some cut crowns were joined
        self.assertLess(len(rows), self.pieces)

    def test_3_results_open_with_summary_and_3d_points(self):
        res = self.results()
        self.assertEqual(res.summary["trees"], len(res.trees))
        self.assertEqual(res.project["input"]["crs"], "GDA2020 / MGA zone 55")       # shown in Run info
        self.assertGreater(res.summary["area_ha"], 0.2)
        self.assertLess(res.summary["area_ha"], 0.6)                   # the plot is 60 m x 60 m = 0.36 ha
        status, tp = self.state.tree_points(res)
        deadline = time.time() + 60
        while status == "preparing" and time.time() < deadline:        # only when the view file is not there yet
            time.sleep(0.1)
            status, tp = self.state.tree_points(res)
        self.assertEqual(status, "ready", tp)
        self.assertEqual(len(tp), len(res.trees))
        tree = res.trees[0]
        pts = tp.tree(int(tree["tree_id"]))
        self.assertGreater(len(pts), 500)
        self.assertAlmostEqual(float(pts[:, 2].max()), float(tree["height_m"]), delta=2.0)

    def test_4_the_input_file_checks_see_the_real_file(self):
        report = self.state.check_file(self.input)
        levels = {c.key: c.level for c in report.checks}
        self.assertEqual((levels["read"], levels["crs"]), (checks.OK, checks.OK), [c.text for c in report.checks])
        self.assertEqual(report.header.points, self.points)
        self.assertEqual(checks.read_crs(self.input), ("GDA2020 / MGA zone 55", False))

    def test_5_save_then_open_the_saved_folder(self):
        res = self.results()
        out = self.state.save(res, self.job / "Project", "Test Site - 13 Feb 25 - Tree Profiling",
                              saving.default_selection(), site="Test Site", run_id=self.run_id)
        folder = Path(out).parent if Path(out).suffix else Path(out)
        names = sorted(p.name for p in folder.iterdir())
        for want in ("Test Site.treeprof", "Test Site_trees.csv", "Test Site_info.laz", "Test Site_crowns.shp",
                     "Test Site_crowns.shx", "Test Site_crowns.dbf", "Test Site_crowns.prj"):
            self.assertIn(want, names)
        self.assertNotIn("Test Site_sat_raw.laz", names)
        project = projectfile.find_in_folder(folder)
        saved = self.state.open_results(project)
        self.assertEqual(len(saved.trees), len(res.trees))
        self.assertEqual(saved.missing_optional, [])
        self.assertIsNotNone(saved.info_laz)
        status, tp = self.state.tree_points(saved)                     # OPEN-4: 3D also works from a saved folder
        deadline = time.time() + 60
        while status == "preparing" and time.time() < deadline:
            time.sleep(0.1)
            status, tp = self.state.tree_points(saved)
        self.assertEqual((status, len(tp)), ("ready", len(res.trees)))
        # the saved crown outlines can be read back and there is one per tree
        code = ("import geopandas, sys; g = geopandas.read_file(sys.argv[1]); "
                "print(len(g), g.crs.to_epsg(), g.geometry.is_valid.all())")
        r = subprocess.run(script_python() + ["-c", code, str(folder / "Test Site_crowns.shp")],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.split(), [str(len(res.trees)), "7855", "True"], r.stderr)


if __name__ == "__main__":
    unittest.main()
