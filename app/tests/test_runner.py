"""Test plan A-20 to A-25 (spec 6.1 to 6.8, RUN-5, RUN-8, RUN-9, SYS-5, STO-1), with the stand-in
scripts of tests/fake_pipeline in place of the real pipeline."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from treeprof_app import projectfile, saving, steps, store as st
from treeprof_app.runner import Runner, RunnerConfig
from tests.helpers import write_las_stub

FAKE = Path(__file__).parent / "fake_pipeline"
ALL_STEPS = list(steps.STEP_ORDER)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        net = self.tmp / "net" / "TEST-26001" / "Project" / "Test Site - 13 Feb 25 - LiDAR"
        net.mkdir(parents=True)
        self.input = write_las_stub(net / "cloud.las", 5000)
        self.input_bytes = self.input.read_bytes()
        self.db = self.tmp / "state" / "runs.sqlite"
        self.store = st.Store(self.db)
        self.addCleanup(self.store.close)
        self.events, self.stops, self.workdirs = [], [], {}
        self.cfg = RunnerConfig(python=sys.executable, scripts_dir=FAKE, needs_copy=lambda p: True,
                                stop_containers=lambda: self.stops.append(1), copy_retry_wait=0, machine="test-pc")
        self.runner = Runner(self.store, self.cfg, self.events.append)
        self.fake(tiles=3)

    def fake(self, **conf):
        self.cfg.extra_env["FAKE_PIPELINE"] = json.dumps(conf)

    def add(self, site="Test Site"):
        workdir = self.tmp / "runs" / f"{site}_{len(self.store.list_runs()) + 1}"
        run_id = self.store.add_run(site=site, run_by="Sam", input_path=self.input, workdir=workdir,
                                    capture_date="2025-02-13", input_size=len(self.input_bytes))
        self.workdirs[run_id] = workdir
        return run_id

    def workdir(self, run_id):
        return self.workdirs[run_id]

    def calls(self, run_id, full=False):
        p = self.workdir(run_id) / "fake_calls.txt"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        return lines if full else [x.split()[0] for x in lines]

    def of_type(self, kind, **match):
        return [e for e in self.events if e["type"] == kind and all(e.get(k) == v for k, v in match.items())]


class WholeRun(Base):                                                            # A-20
    def test_run_from_start_to_end(self):
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "finished")
        run = self.store.get(run_id)
        self.assertEqual((run["status"], run["trees"], run["input_points"], run["error_kind"]),
                         ("finished", 4, 5000, None))
        self.assertTrue(run["sha256"] and run["started_at"] and run["finished_at"])
        self.assertEqual(sorted(self.store.steps_done(run_id)), sorted(ALL_STEPS))
        self.assertEqual(self.calls(run_id), ["find_trees", "join", "measure"])
        self.assertEqual([e["step"] for e in self.of_type("step", state="running")], ALL_STEPS)
        self.assertEqual([e["label"] for e in self.of_type("step", state="running")],
                         ["Copy file", "Find trees", "Join split crowns", "Measure trees", "Prepare view"])
        w = self.workdir(run_id)
        for name in ("find_trees.log", "join.log", "measure.log", "sat_docker.log"):
            self.assertTrue((w / "log" / name).is_file(), name)
        self.assertIn("merged into 4 trees", (w / "log" / "join.log").read_text(encoding="utf-8"))

    def test_progress_reaches_the_screen(self):                                  # RUN-2, RUN-3
        self.runner.run(self.add())
        labels = [e["label"] for e in self.of_type("progress", step="find_trees")]
        for expected in ("Tile 1 of 3", "Tile 2 of 3", "Tile 3 of 3", "Putting the tiles together", "Done"):
            self.assertIn(expected, labels)
        self.assertEqual(self.of_type("progress", step="find_trees")[-1]["fraction"], 1.0)
        copy = self.of_type("progress", step="copy")
        self.assertEqual((copy[-1]["done"], copy[-1]["total"]), (len(self.input_bytes), len(self.input_bytes)))
        self.assertTrue(self.of_type("log", step="join"))

    def test_project_file(self):                                                 # spec 7.1
        run_id = self.add()
        self.runner.run(run_id)
        path = Path(self.store.get(run_id)["project_path"])
        self.assertEqual(path.name, "Test Site.treeprof")
        data = projectfile.read_project(path)
        self.assertEqual(projectfile.check_openable(path, data), [])
        self.assertEqual((data["site_name"], data["run_by"], data["capture_date"], data["run_id"], data["machine"]),
                         ("Test Site", "Sam", "2025-02-13", run_id, "test-pc"))
        self.assertEqual((data["summary"]["trees"], data["summary"]["height_max_m"]), (4, 40.0))
        self.assertEqual((data["input"]["points"], data["input"]["path"], data["input"]["ground_source"]),
                         (5000, str(self.input), "file"))
        p = data["pipeline"]
        self.assertEqual((p["tiles"], p["trees_before_merge"], p["trees_after_merge"], p["max_points"]),
                         ({"count": 3, "thinned": 0}, 6, 4, 20_000_000))
        self.assertEqual(sorted(p["step_seconds"]), sorted(ALL_STEPS))

    def test_the_copy_is_temporary_and_the_original_is_untouched(self):          # STO-1
        run_id = self.add()
        self.runner.run(run_id)
        self.assertFalse((self.workdir(run_id) / "input").exists())
        self.assertEqual(self.input.read_bytes(), self.input_bytes)

    def test_local_file_is_used_where_it_is(self):                               # spec 6.8
        self.cfg.needs_copy = lambda p: False
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "finished")
        run = self.store.get(run_id)
        self.assertEqual((run["local_input"], run["sha256"]), (str(self.input), None))
        self.assertEqual(self.store.steps_done(run_id)["copy"]["info"], {"copied": False})
        self.assertEqual(self.input.read_bytes(), self.input_bytes)

    def test_file_without_tiles(self):
        self.fake(tiles=0)
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "finished")
        fractions = [e["fraction"] for e in self.of_type("progress", step="find_trees")]
        self.assertEqual((fractions[0], fractions[-1]), (None, 1.0))          # indeterminate until it is done

    def test_then_save_and_reopen(self):                                         # A-27
        run_id = self.add()
        self.runner.run(run_id)
        run = self.store.get(run_id)
        dest = self.tmp / "shared" / "Test Site - Tree Profiling"
        plan = saving.make_plan(run["workdir"], dest, run["site"])
        saved = saving.execute(plan, projectfile.read_project(run["project_path"]))
        self.store.add_save(run_id, dest, plan.files)
        data = projectfile.read_project(saved)
        self.assertEqual(projectfile.check_openable(saved, data), [])
        self.assertEqual(data["files"]["trees_csv"], "Test Site_trees.csv")
        self.assertEqual(data["summary"], projectfile.read_project(run["project_path"])["summary"])
        self.assertEqual(self.store.saves(run_id)[0]["folder"], str(dest))

    def test_view_data_hook(self):                                               # spec 6.7
        self.cfg.prepare_view = lambda run: {"area_ha": 2.0}
        run_id = self.add()
        self.runner.run(run_id)
        data = projectfile.read_project(self.store.get(run_id)["project_path"])
        self.assertEqual((data["summary"]["area_ha"], data["summary"]["trees_per_ha"]), (2.0, 2.0))

    def test_view_data_failure_does_not_fail_the_run(self):                      # spec 6.7
        def broken(run):
            raise RuntimeError("no memory")
        self.cfg.prepare_view = broken
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "finished")
        info = self.store.steps_done(run_id)["prepare_view"]["info"]
        self.assertEqual((info["available"], "no memory" in info["reason"]), (False, True))

    def test_a_display_problem_does_not_stop_the_run(self):
        def broken(event):
            raise RuntimeError("window closed")
        self.runner.on_event = broken
        self.assertEqual(self.runner.run(self.add()), "finished")


class Queue(Base):                                                               # A-21, RUN-9
    def test_one_after_another(self):
        a, b = self.add("Site A"), self.add("Site B")
        self.assertEqual(self.runner.run_queue(), [(a, "finished"), (b, "finished")])
        running = [e["run_id"] for e in self.of_type("status", status="running")]
        ended = [e["run_id"] for e in self.of_type("status", status="finished")]
        self.assertEqual((running, ended), ([a, b], [a, b]))
        order = [(e["run_id"], e["status"]) for e in self.of_type("status")]
        self.assertEqual(order, [(a, "running"), (a, "finished"), (b, "running"), (b, "finished")])

    def test_a_failed_run_does_not_block_the_next(self):
        a = self.add("Site A")
        self.fake(tiles=2, fail={"join": "error"})
        self.runner.run(a)
        b = self.add("Site B")
        self.runner.resume(a)
        self.assertEqual(self.runner.run_queue(), [(a, "failed"), (b, "failed")])
        c = self.add("Site C")
        self.fake(tiles=2)
        self.assertEqual(self.runner.run_queue(), [(c, "finished")])


class Failures(Base):                                                            # A-22, A-25, RUN-8
    def check(self, step, mode, kind, calls_after_retry, actions=None):
        self.fake(tiles=3, fail={step: mode})
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "failed")
        run = self.store.get(run_id)
        self.assertEqual((run["status"], run["error_kind"]), ("failed", kind))
        self.assertTrue(run["error_message"])
        failure = self.of_type("failure")[-1]
        self.assertEqual((failure["kind"], failure["message"]), (kind, run["error_message"]))
        if actions:
            self.assertEqual(failure["actions"], actions)
        self.assertNotIn(step, self.store.steps_done(run_id))
        self.fake(tiles=3)                                    # the cause is fixed
        self.runner.resume(run_id)
        self.assertEqual(self.runner.run(run_id), "finished")
        self.assertEqual(self.calls(run_id), calls_after_retry)
        self.assertEqual(self.store.get(run_id)["error_kind"], None)
        return run_id

    def test_out_of_memory(self):
        self.check("find_trees", "oom", "out_of_ram", ["find_trees", "find_trees", "join", "measure"],
                   ["retry_smaller_tiles", "show_details"])

    def test_crash_on_a_tile_keeps_the_finished_tiles(self):
        run_id = self.check("find_trees", "crash", "crash", ["find_trees", "find_trees", "join", "measure"])
        self.assertEqual(self.store.steps_done(run_id)["find_trees"]["info"]["tiles_skipped"], [1])

    def test_docker_not_running(self):
        self.check("find_trees", "docker", "docker_not_running", ["find_trees", "find_trees", "join", "measure"],
                   ["retry"])

    def test_join_fails_and_finding_trees_is_not_run_again(self):               # A-25
        self.check("join", "error", "unknown", ["find_trees", "join", "join", "measure"])

    def test_measure_fails_and_earlier_steps_are_not_run_again(self):
        run_id = self.check("measure", "error", "unknown", ["find_trees", "join", "measure", "measure"])
        self.assertIn("Measure trees", self.of_type("failure")[0]["message"])
        self.assertTrue(self.store.get(run_id)["project_path"])

    def test_half_written_output_is_not_trusted(self):                          # spec 6.3
        self.fake(tiles=2, fail={"join": "partial"})
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "failed")
        run = self.store.get(run_id)
        self.assertEqual(run["error_kind"], "unknown")
        self.assertIn("4,999 points", run["error_detail"])
        self.assertNotIn("join", self.store.steps_done(run_id))

    def test_try_again_with_smaller_tiles(self):                                 # RUN-8
        self.fake(tiles=2, fail={"find_trees": "oom"})
        run_id = self.add()
        self.runner.run(run_id)
        self.assertEqual(self.runner.retry_with_smaller_tiles(run_id), 10_000_000)
        self.fake(tiles=2)
        self.assertEqual(self.runner.run(run_id), "finished")
        first, second = self.calls(run_id, full=True)[:2]
        self.assertNotIn("--max-points", first)
        self.assertIn("--max-points 10000000", second)

    def test_no_trees_is_a_finished_run(self):                                   # RES-10
        self.fake(tiles=2, fail={"measure": "no_trees"})
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "finished")
        run = self.store.get(run_id)
        self.assertEqual((run["trees"], run["project_path"], run["error_kind"]), (0, None, None))
        self.assertEqual(self.of_type("failure"), [])

    def test_missing_input(self):
        run_id = self.add()
        self.input.unlink()
        self.assertEqual(self.runner.run(run_id), "failed")
        self.assertEqual(self.store.get(run_id)["error_kind"], "copy_failed")
        self.assertEqual(self.calls(run_id), [])

    def test_input_is_not_a_point_cloud(self):
        self.input.write_text("not a point cloud " * 30)
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "failed")
        self.assertEqual(self.store.get(run_id)["error_kind"], "unreadable_input")

    def test_scripts_cannot_be_started(self):
        self.cfg.python = str(self.tmp / "no-such-python")
        run_id = self.add()
        self.assertEqual(self.runner.run(run_id), "failed")
        self.assertIn("Cannot start", self.store.get(run_id)["error_detail"])


class Cancel(Base):                                                              # A-23, RUN-5, spec 6.6
    def wait_for(self, test, seconds=20):
        end = time.time() + seconds
        while time.time() < end:
            if test():
                return True
            time.sleep(0.02)
        return False

    def test_cancel_while_finding_trees_then_resume(self):
        self.fake(tiles=4, tile_seconds=0.5)
        run_id = self.add()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.runner.run(run_id)))
        worker.start()
        self.assertTrue(self.wait_for(lambda: self.of_type("progress", step="find_trees", tile=2)))
        pid = int((self.workdir(run_id) / "fake_pid.txt").read_text())
        self.runner.cancel()
        worker.join(30)
        self.assertFalse(worker.is_alive())
        self.assertEqual((result, self.store.get(run_id)["status"]), (["cancelled"], "cancelled"))
        self.assertEqual(self.stops, [1])                     # the containers were told to stop
        if os.name != "nt":
            self.assertFalse(alive(pid), "the script must be gone")
        self.assertNotIn("find_trees", self.store.steps_done(run_id))
        self.assertFalse((self.workdir(run_id) / "sat_result.laz").exists())

        self.fake(tiles=4)
        self.runner.resume(run_id)
        self.assertEqual(self.runner.run(run_id), "finished")
        self.assertEqual(self.store.steps_done(run_id)["find_trees"]["info"]["tiles_skipped"], [1])
        self.assertEqual(self.calls(run_id), ["find_trees", "find_trees", "join", "measure"])
        self.assertEqual(self.store.get(run_id)["trees"], 4)

    def test_cancel_during_the_copy_leaves_nothing(self):
        self.cfg.copy_chunk = 32
        run_id = self.add()

        def on_event(event):
            self.events.append(event)
            if event["type"] == "progress" and event.get("step") == "copy":
                self.runner.cancel()

        self.runner.on_event = on_event
        self.assertEqual(self.runner.run(run_id), "cancelled")
        inputs = self.workdir(run_id) / "input"
        self.assertEqual(list(inputs.iterdir()) if inputs.exists() else [], [])
        self.assertEqual(self.calls(run_id), [])
        self.runner.on_event = self.events.append
        self.runner.resume(run_id)
        self.assertEqual(self.runner.run(run_id), "finished")

    def test_take_a_waiting_run_out_of_the_queue(self):                         # RUN-9
        a, b = self.add("Site A"), self.add("Site B")
        self.store.set_status(b, "cancelled")
        self.assertEqual(self.runner.run_queue(), [(a, "finished")])
        self.assertEqual(self.store.get(b)["status"], "cancelled")


class Interrupted(Base):                                                         # A-24, SYS-5
    def test_app_stopped_half_way_then_resume(self):
        self.fake(tiles=2, fail={"join": "hang"})
        run_id = self.add()
        worker = threading.Thread(target=lambda: self.runner.run(run_id))
        worker.start()
        end = time.time() + 20
        while time.time() < end and self.calls(run_id) != ["find_trees", "join"]:
            time.sleep(0.02)
        self.assertEqual(self.calls(run_id), ["find_trees", "join"])
        # the app dies: the database still says "running" (what a power cut leaves behind)
        self.runner.cancel()
        worker.join(30)
        self.store.close()
        db = sqlite3.connect(str(self.db))
        db.execute("UPDATE runs SET status = 'running' WHERE id = ?", (run_id,))
        db.commit()
        db.close()

        store = st.Store(self.db)                             # the app starts again
        self.addCleanup(store.close)
        self.assertEqual(store.mark_interrupted(), [run_id])
        self.assertEqual(store.get(run_id)["status"], "interrupted")
        self.assertEqual(sorted(store.steps_done(run_id)), ["copy", "find_trees"])
        runner = Runner(store, self.cfg)
        self.fake(tiles=2)
        runner.resume(run_id)
        self.assertEqual(runner.run(run_id), "finished")
        self.assertEqual(self.calls(run_id), ["find_trees", "join", "join", "measure"])   # find_trees only once
        self.assertEqual(store.get(run_id)["trees"], 4)


if __name__ == "__main__":
    unittest.main()
