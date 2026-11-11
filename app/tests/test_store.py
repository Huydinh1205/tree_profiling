"""Test plan A-12 (spec 8, SYS-5, RUN-9, SAVE-8, NEW-3, NEW-5)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import store as st


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = self.tmp / "state" / "runs.sqlite"
        self.store = st.Store(self.db)
        self.addCleanup(self.store.close)

    def add(self, site="Test Site", run_by="Sam", **kw):
        return self.store.add_run(site=site, run_by=run_by, input_path="//server/share/cloud.las",
                                  workdir=self.tmp / "runs" / site, **kw)

    def to(self, run_id, *statuses):
        for s in statuses:
            self.store.set_status(run_id, s)


class Runs(Base):
    def test_new_run_waits_in_the_queue(self):
        r = self.store.get(self.add(capture_date="2025-02-13"))
        self.assertEqual((r["status"], r["site"], r["run_by"], r["capture_date"], r["max_points"]),
                         ("queued", "Test Site", "Sam", "2025-02-13", 20_000_000))

    def test_site_and_run_by_are_required(self):
        for kw in ({"site": " "}, {"run_by": ""}):
            with self.assertRaises(ValueError):
                self.add(**kw)

    def test_unknown_run(self):
        with self.assertRaises(KeyError):
            self.store.get("nothing")

    def test_queue_is_oldest_first_and_list_is_newest_first(self):
        a, b, c = self.add("A"), self.add("B"), self.add("C")
        self.assertEqual(self.store.next_queued()["id"], a)
        self.to(a, "running")
        self.assertEqual(self.store.next_queued()["id"], b)
        self.assertEqual([r["id"] for r in self.store.list_runs()], [c, b, a])

    def test_empty_queue(self):
        self.assertIsNone(self.store.next_queued())

    def test_update_checks_field_names(self):
        a = self.add()
        self.store.update(a, trees=12, input_points=5000)
        self.assertEqual(self.store.get(a)["trees"], 12)
        with self.assertRaises(ValueError):
            self.store.update(a, status="finished")


class Transitions(Base):                                                         # spec 8
    def test_allowed_paths(self):
        for path in (("running", "finished"), ("running", "failed", "queued", "running", "finished"),
                     ("running", "cancelled", "queued"), ("running", "interrupted", "queued"),
                     ("cancelled", "queued")):
            self.to(self.add(), *path)

    def test_forbidden_moves(self):
        cases = ((), "finished"), ((), "failed"), ((), "interrupted"), (("running", "finished"), "queued"), \
                (("running", "finished"), "running"), (("running", "failed"), "running"), \
                (("running", "failed"), "finished"), (("running",), "queued"), (("running",), "running"), \
                (("running", "cancelled"), "failed")
        for before, bad in cases:
            run_id = self.add()
            self.to(run_id, *before)
            with self.assertRaises(st.InvalidTransition, msg=f"{before} -> {bad}"):
                self.store.set_status(run_id, bad)
            self.assertEqual(self.store.get(run_id)["status"], before[-1] if before else "queued")

    def test_trying_again_forgets_the_last_error(self):
        a = self.add()
        self.to(a, "running")
        self.store.set_status(a, "failed", error_kind="out_of_ram", error_message="too large", finished_at=st.now())
        self.store.set_status(a, "queued")
        r = self.store.get(a)
        self.assertEqual((r["error_kind"], r["error_message"], r["finished_at"]), (None, None, None))

    def test_running_runs_become_interrupted_when_the_app_starts(self):        # SYS-5
        a, b, c = self.add("A"), self.add("B"), self.add("C")
        self.to(a, "running")
        self.to(b, "running", "finished")
        self.store.close()
        reopened = st.Store(self.db)                          # the app was closed and opened again
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.mark_interrupted(), [a])
        self.assertEqual([reopened.get(x)["status"] for x in (a, b, c)], ["interrupted", "finished", "queued"])
        self.assertEqual(reopened.mark_interrupted(), [])

    def test_delete(self):
        a, b = self.add("A"), self.add("B")
        self.store.step_done(a, "copy", 1.0)
        self.store.add_save(a, "D:/out", {})
        self.store.delete_run(a)
        self.assertEqual([r["id"] for r in self.store.list_runs()], [b])
        self.assertEqual((self.store.steps_done(a), self.store.saves(a)), ({}, []))
        self.to(b, "running")
        with self.assertRaises(st.InvalidTransition):
            self.store.delete_run(b)


class StepsAndSaves(Base):
    def test_finished_steps_are_remembered(self):                                # spec 6.3
        a = self.add()
        self.store.step_done(a, "copy", 2.5, {"copied": True})
        self.store.step_done(a, "find_trees", 60.0, {"tiles_total": 3})
        done = self.store.steps_done(a)
        self.assertEqual(sorted(done), ["copy", "find_trees"])
        self.assertEqual(done["find_trees"], {"seconds": 60.0, "info": {"tiles_total": 3}})
        with self.assertRaises(ValueError):
            self.store.step_done(a, "nonsense", 1.0)

    def test_forget_a_step_and_the_later_ones(self):
        a = self.add()
        for s in ("copy", "find_trees", "join", "measure"):
            self.store.step_done(a, s, 1.0)
        self.store.forget_steps(a, "join")
        self.assertEqual(sorted(self.store.steps_done(a)), ["copy", "find_trees"])

    def test_everything_survives_reopening(self):
        a = self.add()
        self.store.step_done(a, "copy", 1.0)
        self.store.close()
        again = st.Store(self.db)
        self.addCleanup(again.close)
        self.assertEqual((again.get(a)["site"], sorted(again.steps_done(a))), ("Test Site", ["copy"]))

    def test_save_history(self):                                                 # SAVE-8
        a = self.add()
        self.assertEqual(self.store.saves(a), [])
        self.store.add_save(a, "//server/share/out", {"trees_csv": "Test Site_trees.csv"})
        self.store.add_save(a, "E:/usb", {"trees_csv": "Test Site_trees.csv", "info_laz": None})
        saves = self.store.saves(a)
        self.assertEqual([s["folder"] for s in saves], ["//server/share/out", "E:/usb"])
        self.assertEqual(saves[1]["files"]["info_laz"], None)

    def test_names_used_before(self):                                            # NEW-5
        self.add(run_by="Sam")
        self.add(run_by="Alex")
        self.add(run_by="Sam")
        self.assertEqual(self.store.known_names(), ["Sam", "Alex"])

    def test_same_file_already_finished(self):                                   # NEW-3
        a = self.add(input_size=100, input_mtime=5.0)
        self.assertIsNone(self.store.finished_run_for("//server/share/cloud.las", 100, 5.0))
        self.to(a, "running", "finished")
        self.assertEqual(self.store.finished_run_for("//server/share/cloud.las", 100, 5.0)["id"], a)
        self.assertIsNone(self.store.finished_run_for("//server/share/cloud.las", 101, 5.0))


if __name__ == "__main__":
    unittest.main()
