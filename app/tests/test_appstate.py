"""The app's working parts without any screen: queue worker, results, save, storage (test plan A-20,
A-21, A-27, A-28, A-29; spec HOME, SAVE, OPEN, STO, NEW-6)."""
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from treeprof_app import projectfile, saving, store as st
from treeprof_app.appstate import AppState
from treeprof_app.runner import RunnerConfig
from tests.helpers import write_las_stub

FAKE = Path(__file__).parent / "fake_pipeline"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.job = self.tmp / "net" / "TEST-26001"
        folder = self.job / "Project" / "Test Site - 13 Feb 25 - LiDAR"
        folder.mkdir(parents=True)
        self.input = write_las_stub(folder / "cloud.las", 5000)
        self.cfg = RunnerConfig(python=sys.executable, scripts_dir=FAKE, needs_copy=lambda p: True,
                                stop_containers=lambda: None, copy_retry_wait=0,
                                extra_env={"FAKE_PIPELINE": json.dumps({"tiles": 2})},
                                prepare_view=lambda run: {"area_ha": 2.0})
        self.state = self.new_state()

    def new_state(self):
        state = AppState(self.tmp / "home", runner_config=self.cfg, machine_runner=lambda cmd: 0)
        self.addCleanup(state.shutdown)
        return state

    def fake(self, **conf):
        self.cfg.extra_env["FAKE_PIPELINE"] = json.dumps(conf)

    def add(self, site="Test Site", run_by="Sam"):
        return self.state.add_run(self.input, site=site, run_by=run_by, capture_date="2025-02-13")

    def wait(self, run_id, *statuses, seconds=30):
        end = time.time() + seconds
        while time.time() < end:
            if self.state.store.get(run_id)["status"] in statuses:
                return self.state.run_view(run_id)
            time.sleep(0.02)
        self.fail(f"run is still {self.state.store.get(run_id)['status']}, expected {statuses}")

    def finished(self, **kw):
        return self.wait(self.add(**kw), "finished")


class Queue(Base):
    def test_a_run_is_picked_up_and_finished(self):
        run = self.finished()
        self.assertEqual((run["trees"], run["saved"], run["local_exists"]), (4, False, True))
        self.assertEqual(Path(run["workdir"]).parent, self.tmp / "home" / "runs")
        self.assertTrue(Path(run["workdir"]).name.startswith("Test_Site_"))
        self.assertEqual(run["live"].steps, {s: "done" for s in ("copy", "find_trees", "join", "measure", "prepare_view")})
        self.assertIn("Done in", "\n".join(self.state.log_tail(run["id"])))

    def test_two_runs_get_their_own_folders(self):
        a, b = self.add(), self.add()
        self.wait(b, "finished")
        self.assertNotEqual(self.state.store.get(a)["workdir"], self.state.store.get(b)["workdir"])
        self.assertEqual([r["status"] for r in self.state.runs()], ["finished", "finished"])

    def test_take_a_waiting_run_out_of_the_queue(self):
        self.fake(tiles=3, tile_seconds=0.3)
        a, b = self.add("Site A"), self.add("Site B")
        self.state.cancel(b)
        self.assertEqual(self.state.store.get(b)["status"], "cancelled")
        self.wait(a, "finished")

    def test_cancel_then_resume(self):
        self.fake(tiles=4, tile_seconds=0.4)
        a = self.add()
        self.wait(a, "running")
        end = time.time() + 20
        while time.time() < end and not (self.state.live.get(a) and self.state.live[a].steps["find_trees"] == "running"):
            time.sleep(0.02)
        self.state.cancel(a)
        self.wait(a, "cancelled")
        self.fake(tiles=4)
        self.state.resume(a)
        self.assertEqual(self.wait(a, "finished")["trees"], 4)

    def test_failed_run_then_try_again_with_smaller_tiles(self):
        self.fake(tiles=2, fail={"find_trees": "oom"})
        a = self.add()
        run = self.wait(a, "failed")
        self.assertEqual((run["error_kind"], run["live"].failure["kind"]), ("out_of_ram", "out_of_ram"))
        self.fake(tiles=2)
        self.assertEqual(self.state.retry_smaller(a), 10_000_000)
        self.wait(a, "finished")

    def test_a_run_cut_off_by_a_restart_is_marked(self):                        # SYS-5
        a = self.add()
        self.wait(a, "finished")
        b = self.state.store.add_run(site="B", run_by="Sam", input_path=self.input, workdir=self.tmp / "home" / "runs" / "B")
        self.state.store.set_status(b, "cancelled")            # keep the worker away from it
        self.state.store._db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (b,))
        self.state.store._db.commit()
        self.state.shutdown()
        again = self.new_state()
        self.assertEqual(again.interrupted, [b])
        self.assertEqual(again.store.get(b)["status"], "interrupted")

    def test_time_estimate_comes_from_finished_runs(self):                      # NEW-6
        self.assertIsNone(self.state.estimate_minutes(5000))
        self.finished()
        est = self.state.estimate_minutes(10_000)
        self.assertGreater(est, 0)
        self.assertAlmostEqual(self.state.estimate_minutes(20_000), est * 2)


class ResultsAndSave(Base):
    def setUp(self):
        super().setUp()
        self.run = self.finished()
        self.res = self.state.open_results(self.state.project_path_for(self.run["id"]))

    def save(self, name="Out", **kw):
        return self.state.save(self.res, self.job / "Project", name, saving.default_selection(), site="Test Site",
                               run_id=self.run["id"], **kw)

    def test_open_results(self):
        self.assertEqual((len(self.res.trees), self.res.summary["trees"], self.res.summary["trees_per_ha"]), (4, 4, 2.0))
        self.assertEqual(self.res.by_id[4]["height_m"], 40.0)
        self.assertEqual(self.res.missing_optional, [])
        self.assertIsNotNone(self.res.info_laz)

    def test_suggested_save_place_and_name(self):                               # SAVE-1, SAVE-2
        start, name = self.state.suggested_save(self.state.run_view(self.run["id"]))
        self.assertEqual(Path(start), self.job / "Project")
        self.assertEqual(name, "Test Site \u2013 13 Feb 25 \u2013 Tree Profiling")
        (self.job / "Project" / name).mkdir()
        self.assertEqual(self.state.suggested_save(self.state.run_view(self.run["id"]))[1], name + " v2")

    def test_save_then_reopen_from_the_saved_folder(self):                      # A-27
        path = self.save()
        self.assertEqual(path, self.job / "Project" / "Out" / "Test Site.treeprof")
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()),
                         ["Test Site.treeprof", "Test Site_crowns.dbf", "Test Site_crowns.prj", "Test Site_crowns.shp",
                          "Test Site_crowns.shx", "Test Site_info.laz", "Test Site_trees.csv"])
        view = self.state.run_view(self.run["id"])
        self.assertEqual((view["saved"], view["saved_folder"]), (True, str(path.parent)))
        again = self.state.open_results(path)
        self.assertEqual((again.summary, len(again.trees)), (self.res.summary, 4))

    def test_same_name_asks_then_keeps_both_or_replaces(self):                  # SAVE-5
        self.save()
        with self.assertRaises(saving.SaveError) as cm:
            self.save()
        self.assertEqual(cm.exception.code, "conflict")
        both = self.save(on_conflict="keep_both")
        self.assertEqual(both.name, "Test Site (2).treeprof")
        self.assertEqual(self.save(on_conflict="replace").name, "Test Site.treeprof")
        self.assertEqual(len(self.state.store.saves(self.run["id"])), 3)

    def test_save_a_copy_of_saved_results(self):                                # OPEN-8
        saved = self.state.open_results(self.save())
        copy = self.state.save(saved, self.tmp, "Copy", set(), site="Test Site")
        self.assertEqual(len(self.state.open_results(copy).trees), 4)
        self.assertTrue((copy.parent / "Test Site_info.laz").is_file())

    def test_delete_local_copy_of_a_saved_run(self):                            # A-29, STO-4
        path = self.save()
        self.state.delete_local_copy(self.run["id"])
        view = self.state.run_view(self.run["id"])
        self.assertEqual((view["local_exists"], view["status"]), (False, "finished"))
        self.assertEqual(self.state.project_path_for(self.run["id"]), path)
        shutil.rmtree(path.parent)
        self.assertIsNone(self.state.project_path_for(self.run["id"]))       # "Not found"

    def test_unsaved_run_needs_the_site_name_typed(self):                       # STO-3
        for typed in (None, "", "Other Site"):
            with self.assertRaises(ValueError):
                self.state.delete_local_copy(self.run["id"], typed)
        self.assertTrue(Path(self.run["workdir"]).is_dir())
        self.state.delete_local_copy(self.run["id"], " test site ")
        self.assertFalse(Path(self.run["workdir"]).exists())
        self.assertEqual(self.state.runs(), [])

    def test_storage_list_and_tiles(self):                                      # STO-3, STO-5
        info = self.state.storage()
        self.assertEqual(len(info["rows"]), 1)
        self.assertGreater(info["rows"][0]["size"], 0)
        self.assertEqual(info["used"], info["rows"][0]["size"])
        self.assertGreater(self.state.delete_tiles(self.run["id"]), 0)
        self.assertEqual(list(Path(self.run["workdir"]).glob("sat_result_tiles_*")), [])
        self.assertEqual(len(self.state.open_results(self.state.project_path_for(self.run["id"])).trees), 4)

    def test_3d_data_is_reported_missing_not_broken(self):                      # OPEN-5
        status, reason = self.state.tree_points(self.res)                      # the stand-in file has no points
        end = time.time() + 20
        while status == "preparing" and time.time() < end:
            time.sleep(0.05)
            status, reason = self.state.tree_points(self.res)
        self.assertEqual(status, "unavailable")
        self.assertTrue(reason)

    def test_damaged_project_file(self):                                        # OPEN-7
        bad = self.tmp / "bad.treeprof"
        bad.write_text("{ not json")
        with self.assertRaises(projectfile.ProjectFileError):
            self.state.open_results(bad)

    def test_opened_files_get_a_token(self):
        token = self.state.register_opened("D:/x/Test Site.treeprof")
        self.assertEqual(self.state.opened[token], "D:/x/Test Site.treeprof")
        self.assertEqual(self.state.register_opened("D:/x/Test Site.treeprof"), token)


class OpenedRecently(Base):                                                       # HOME-5
    def saved(self, name="Out"):
        run_id = self.add()
        self.wait(run_id, "finished")
        res = self.state.open_results(self.state.project_path_for(run_id))
        return run_id, self.state.save(res, self.job / "Project", name, saving.default_selection(), site="Test Site",
                                       run_id=run_id)

    def test_results_of_a_run_on_home_are_not_listed_twice(self):
        _, project = self.saved()
        self.assertFalse(self.state.remember_opened(self.state.open_results(project)))
        self.assertEqual(self.state.recent(), [])

    def test_results_from_elsewhere_are_listed_newest_first_and_kept_after_a_restart(self):
        run_id, project = self.saved()
        other = self.tmp / "from a colleague"
        shutil.copytree(project.parent, other)
        self.state.delete_run(run_id)                               # the run is not on this computer (any more)
        self.assertTrue(self.state.remember_opened(self.state.open_results(project)))
        self.assertTrue(self.state.remember_opened(self.state.open_results(other / project.name)))
        self.assertTrue(self.state.remember_opened(self.state.open_results(project)))     # opened again: moves up, once
        got = self.state.recent()
        self.assertEqual([r["path"] for r in got], [str(project), str(other / project.name)])
        self.assertEqual((got[0]["site"], got[0]["capture_date"], got[0]["trees"], got[0]["exists"]),
                         ("Test Site", "2025-02-13", 4, True))
        self.state.shutdown()
        again = self.new_state()
        self.assertEqual([r["path"] for r in again.recent()], [str(project), str(other / project.name)])
        shutil.rmtree(other)
        self.assertEqual([r["exists"] for r in again.recent()], [True, False])       # shown as Not found
        again.forget_opened(str(other / project.name))
        self.assertEqual([r["path"] for r in again.recent()], [str(project)])

    def test_the_list_does_not_grow_for_ever(self):
        run_id, project = self.saved()
        self.state.delete_run(run_id)
        res = self.state.open_results(project)
        for i in range(self.state.RECENT_KEPT + 5):
            res.project_path = project.with_name(f"copy {i}.treeprof")
            self.state.remember_opened(res)
        got = self.state.recent()
        self.assertEqual(len(got), self.state.RECENT_KEPT)
        self.assertTrue(got[0]["path"].endswith(f"copy {self.state.RECENT_KEPT + 4}.treeprof"))


class Checks(Base):
    def test_machine_is_asked_once_a_minute(self):
        asked = []
        self.state._machine_runner = lambda cmd: asked.append(cmd) or 0
        first = self.state.machine()
        self.state.machine()
        n = len(asked)
        self.assertGreater(n, 0)
        self.assertEqual([c.level for c in first if c.key in ("docker", "gpu", "model")], ["ok", "ok", "ok"])
        self.state.machine(refresh=True)
        self.assertEqual(len(asked), 2 * n)

    def test_file_report(self):
        rep = self.state.check_file(self.input)
        self.assertEqual((rep.header.points, rep.name), (5000, "cloud.las"))
        self.assertEqual(next(c for c in rep.checks if c.key == "read").level, "ok")
        self.assertFalse(any(c.key == "duplicate" for c in rep.checks))
        self.finished()
        self.assertTrue(any(c.key == "duplicate" for c in self.state.check_file(self.input).checks))


if __name__ == "__main__":
    unittest.main()
