"""What the screens show, worked out without a window (wording, table, crown map geometry and colours)."""
import datetime as dt
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import checks, present, steps, store as st
from treeprof_app.appstate import LiveRun


def tree(i, x=0.0, y=0.0, height=10.0, diam=4.0, qc="ok"):
    return {"tree_id": i, "tree_uid": f"TEST_SITE_TREE{i:05d}", "top_x": x, "top_y": y, "height_m": height,
            "crown_diam_m": diam, "crown_area_m2": 12.0, "qc_flag": qc}


class Words(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual([present.fmt_value(v) for v in (None, "", 1234, 12.345, "ok")], ["–", "–", "1,234", "12.3", "ok"])
        self.assertEqual([present.fmt_duration(s) for s in (None, 5, 61, 3725)], ["", "5 s", "1 min", "1 h 02 min"])
        self.assertEqual([present.fmt_bytes(n) for n in (None, 10, 2048, 5 * 1024 ** 3)], ["", "10 bytes", "2.0 KB", "5.0 GB"])

    def test_dates(self):
        self.assertEqual(present.fmt_capture("2025-02-13"), "13 Feb 25")
        self.assertEqual(present.fmt_capture("not a date"), "not a date")
        self.assertEqual(present.fmt_when("2026-11-12T09:05:00+11:00", dt.date(2026, 11, 12)), "Today 09:05")
        self.assertEqual(present.fmt_when("2026-11-10T16:30:00+11:00", dt.date(2026, 11, 12)), "10 Nov 26, 16:30")
        self.assertEqual(present.fmt_when(None), "")
        self.assertEqual(present.title_of("Test Site", "2025-02-13"), "Test Site – 13 Feb 25")
        self.assertEqual(present.title_of("Test Site", None), "Test Site")

    def test_every_status_has_words_and_a_sign(self):                    # SYS-8
        for status in (st.QUEUED, st.RUNNING, st.FINISHED, st.FAILED, st.CANCELLED, st.INTERRUPTED):
            words, sign, _, _ = present.STATUS[status]
            self.assertTrue(words and sign)
        self.assertEqual(len({v[1] for v in present.STATUS.values()}), 6)

    def test_ground(self):
        self.assertIn("Worked out", present.ground_text("CSF"))
        self.assertIn("marked in the file", present.ground_text("file"))
        self.assertIsNone(present.ground_text(None))


class NewRun(unittest.TestCase):
    def report(self, ok=True):
        level = checks.OK if ok else checks.ERROR
        return checks.FileReport("x.las", "x.las", checks=[checks.Check("read", level, "The file can be read")])

    def test_start_problems_in_order(self):
        good = {"report": self.report(), "site": "Test Site", "date": "13 Feb 25"}
        self.assertIn("Choose a point cloud", present.start_problem([], "Sam"))
        self.assertIn("cannot be run", present.start_problem([{**good, "report": self.report(False)}], "Sam"))
        self.assertIn("Run by", present.start_problem([good], "  "))
        self.assertIn("site name", present.start_problem([{**good, "site": " "}], "Sam"))
        self.assertIn("not a date", present.start_problem([{**good, "date": "soon"}], "Sam"))
        self.assertIsNone(present.start_problem([good, {**good, "date": ""}], "Sam"))

    def test_file_facts(self):
        self.assertEqual(present.file_facts(self.report(), None), [])


class Running(unittest.TestCase):
    def view(self, status=st.RUNNING):
        live = LiveRun()
        live.steps.update(copy="done", find_trees="running")
        live.seconds["copy"] = 12.0
        live.started["find_trees"] = 1000.0
        live.label, live.fraction, live.eta_minutes = "Tile 2 of 3", 0.5, 4.0
        return {"status": status, "live": live, "steps_done": {"copy": {"seconds": 12.0}}}

    def test_step_rows(self):
        rows = {r["key"]: r for r in present.step_rows(self.view(), now=1090.0)}
        self.assertEqual([r for r in rows], list(steps.STEP_ORDER))
        self.assertEqual((rows["copy"]["state"], rows["copy"]["right"]), ("done", "Done · 12 s"))
        self.assertEqual(rows["find_trees"]["state"], "running")
        self.assertEqual(rows["find_trees"]["right"], "Running · 1 min so far")
        self.assertEqual((rows["find_trees"]["label"], rows["find_trees"]["eta"], rows["find_trees"]["fraction"]),
                         ("Tile 2 of 3", "About 4 min left", 0.5))
        self.assertEqual(rows["join"]["state"], "waiting")

    def test_a_stopped_run_shows_no_step_as_running(self):
        rows = present.step_rows(self.view(st.CANCELLED))
        self.assertEqual([r["state"] for r in rows], ["done", "waiting", "waiting", "waiting", "waiting"])

    def test_copy_shows_bytes(self):
        v = self.view()
        v["live"].steps.update(copy="running", find_trees="waiting")
        v["live"].copy = (1024 ** 3, 4 * 1024 ** 3)
        v["steps_done"] = {}
        self.assertEqual(present.step_rows(v)[0]["label"], "1.0 GB of 4.0 GB")

    def test_home_card_text(self):
        self.assertEqual(present.current_step_text(self.view()), "Step 2 of 5: Find trees · Tile 2 of 3")
        self.assertEqual(present.current_step_text({"live": None}), "")

    def test_failure_buttons(self):
        self.assertEqual(present.failure_buttons("out_of_ram")[0], "Try again with smaller tiles")
        self.assertNotIn("Try again", present.failure_buttons("docker_missing"))
        self.assertEqual(present.failure_buttons("other"), ["Try again", "Send log to Ryan", "Delete this run"])


class ResultsTable(unittest.TestCase):
    def setUp(self):
        self.trees = [tree(1, height=10.0), tree(2, height=30.0, qc="edge"), tree(3, height=None), tree(4, height=20.0)]
        present.add_labels(self.trees)

    def test_filter(self):
        ids = lambda rows: [t["tree_id"] for t in rows]
        self.assertEqual(ids(present.filter_trees(self.trees, find="00002")), [2])
        self.assertEqual(ids(present.filter_trees(self.trees, hmin=15)), [2, 4])
        self.assertEqual(ids(present.filter_trees(self.trees, hmin=15, hmax=25)), [4])
        self.assertEqual(ids(present.filter_trees(self.trees, qc="check")), [2])
        self.assertEqual(ids(present.filter_trees(self.trees, qc="ok")), [1, 3, 4])

    def test_sort_keeps_empty_cells_last(self):
        ids = lambda rows: [t["tree_id"] for t in rows]
        self.assertEqual(ids(present.sort_trees(self.trees, "height_m")), [1, 4, 2, 3])
        self.assertEqual(ids(present.sort_trees(self.trees, "height_m", True)), [2, 4, 1, 3])

    def test_tallest_and_labels(self):
        self.assertEqual(present.tallest(self.trees), 2)
        self.assertIsNone(present.tallest([]))
        self.assertEqual(self.trees[1]["qc_label"], "Check: at the edge")
        facts = dict(present.tree_facts(self.trees[1]))
        self.assertEqual((facts["Height"], facts["Quality check"]), ("30.0 m", "Check: at the edge"))

    def test_cards(self):
        cards = present.summary_cards({"trees": 1234, "height_mean_m": 18.25, "height_max_m": 31.0, "area_ha": 2.5,
                                       "trees_per_ha": 493.6, "qc": {"ok": 1200, "edge": 30, "few_points,edge": 4}})
        self.assertEqual(cards[0], ("Trees", "1,234", ""))
        self.assertEqual(cards[-1][1:], ("34", "34 at the edge, 4 with few points"))

    def test_run_info(self):
        rows = dict(present.run_info_rows({"site_name": "Test Site", "capture_date": "2025-02-13",
                                           "input": {"ground_source": "CSF"}, "pipeline": {"trees_before_merge": 15}}, []))
        self.assertEqual(rows["Capture date"], "13 Feb 25")
        self.assertEqual(rows["Trees before joining crowns"], "15")
        self.assertEqual(rows["Saved to"], "Not saved yet")
        self.assertEqual(rows["Run by"], "–")
        self.assertNotIn("Saved to", dict(present.run_info_rows({"site_name": "x"}, None)))


class CrownMap(unittest.TestCase):
    def test_outlines_keep_their_size_and_y_points_down(self):
        square = [(98.0, 198.0), (102.0, 198.0), (102.0, 202.0), (98.0, 202.0)]
        shapes, width, height = present.map_shapes([tree(1, 100.0, 200.0), tree(2, 140.0, 220.0)], {1: square})
        self.assertEqual([(s[0], s[1]) for s in shapes], [(1, "ring"), (2, "circle")])
        ring = shapes[0][2]
        self.assertEqual(max(p[0] for p in ring) - min(p[0] for p in ring), 4.0)
        self.assertEqual(max(p[1] for p in ring) - min(p[1] for p in ring), 4.0)
        self.assertEqual((width, height), (44.0, 24.0))                   # 98..142 m by 198..222 m
        x, y, r = shapes[1][2]
        self.assertEqual(r, 2.0)
        self.assertGreater(x, 0)                                         # east of the middle: to the right
        self.assertLess(y, 0)                                            # north of the middle: up on the screen

    def test_big_sites_get_lighter_shapes(self):
        long_ring = [(float(i), 0.0) for i in range(100)]
        self.assertEqual(len(present.map_shapes([tree(1)], {1: long_ring})[0][0][2]), 32)
        many = [tree(i, float(i), 0.0) for i in range(1, 3002)]
        self.assertEqual(len(present.map_shapes(many, {1: long_ring})[0][0][2]), 16)
        huge = [tree(i, float(i), 0.0) for i in range(1, 12002)]
        shapes, width, _ = present.map_shapes(huge, {1: long_ring})
        self.assertEqual({s[1] for s in shapes}, {"circle"})
        self.assertEqual(width, 12000 + 4.0)

    def test_a_tree_without_a_place_is_left_out(self):
        lost = tree(1)
        lost["top_x"] = None
        self.assertEqual([s[0] for s in present.map_shapes([lost, tree(2, 5.0, 5.0)], {})[0]], [2])
        self.assertEqual(present.map_shapes([], {}), ([], 10.0, 10.0))

    def test_colours_run_from_light_to_dark(self):
        self.assertEqual(present.ramp_rgb(10, 10, 30), (0xCF, 0xE8, 0xD8))
        self.assertEqual(present.ramp_rgb(30, 10, 30), (0x0F, 0x4A, 0x32))
        self.assertEqual(present.ramp_rgb(None, 10, 30), (0xCF, 0xE8, 0xD8))
        self.assertEqual(present.ramp_rgb(5, 5, 5), (0xCF, 0xE8, 0xD8))
        mid = present.ramp_rgb(20, 10, 30)
        self.assertEqual(mid, (0x5F, 0xAE, 0x85))
        self.assertEqual(present.value_range([tree(1, height=3.0), tree(2, height=None), tree(3, height=9.0)], "height_m"), (3.0, 9.0))


class QualityColours(unittest.TestCase):                                         # RES-3
    def test_each_class_has_its_own_colour_and_the_legend_counts_them(self):
        trees = [tree(1), tree(2, qc="edge"), tree(3, qc="edge"), tree(4, qc="few_points,edge"), tree(5, qc=None)]
        colours, legend = present.map_colours(trees, "qc_flag")
        self.assertEqual(colours[1], present.QC_COLOURS["ok"])
        self.assertEqual(colours[5], present.QC_COLOURS["ok"])           # no flag counts as OK
        self.assertEqual(colours[2], colours[3])
        self.assertEqual(len({colours[1], colours[2], colours[4]}), 3)
        self.assertEqual(legend, ("classes", [(present.QC_COLOURS["ok"], "OK", 2), (present.QC_COLOURS["edge"], "At the edge", 2),
                                              (present.QC_COLOURS["few_points,edge"], "Few points, at the edge", 1)]))

    def test_ok_is_always_in_the_legend_and_unknown_flags_are_not_lost(self):
        colours, legend = present.map_colours([tree(1, qc="edge"), tree(2, qc="new_flag")], "qc_flag")
        self.assertEqual([(words, n) for _, words, n in legend[1]], [("OK", 0), ("At the edge", 1), ("Other", 1)])
        self.assertNotEqual(colours[2], present.QC_COLOURS["ok"])

    def test_the_four_colours_differ_in_lightness_too(self):
        light = sorted(0.299 * r + 0.587 * g + 0.114 * b for r, g, b in present.QC_COLOURS.values())
        self.assertTrue(all(b - a > 25 for a, b in zip(light, light[1:])), light)

    def test_numbers_still_use_the_scale(self):
        colours, legend = present.map_colours([tree(1, height=10.0), tree(2, height=30.0)], "height_m")
        self.assertEqual(legend, ("scale", 10.0, 30.0))
        self.assertEqual(colours, {1: present.ramp_rgb(10, 10, 30), 2: present.ramp_rgb(30, 10, 30)})
        self.assertEqual(list(present.COLOUR_BY)[-1], "qc_flag")


class SaveAndStorage(unittest.TestCase):
    def test_save_problem(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertIn("Choose the folder", present.save_problem("", "x"))
        self.assertIn("Choose the folder", present.save_problem(str(tmp / "nope"), "x"))
        self.assertIn("name", present.save_problem(str(tmp), "  "))
        self.assertIn("too long", present.save_problem(str(tmp), "x" * 200))
        self.assertIsNone(present.save_problem(str(tmp), "Test Site – 13 Feb 25 – Tree Profiling"))

    def test_storage_line(self):
        row = {"site": "Test Site", "capture_date": "2025-02-13", "created_at": "2020-01-02T10:00:00+11:00",
               "size": 3 * 1024 ** 3, "saved": False, "status": st.FINISHED}
        self.assertEqual(present.storage_line(row), ("Test Site – 13 Feb 25", "2 Jan 20, 10:00 · 3.0 GB · Not saved", True))
        self.assertEqual(present.storage_line({**row, "saved": True})[1:], ("2 Jan 20, 10:00 · 3.0 GB · Saved", False))
        self.assertEqual(present.storage_line({**row, "status": st.CANCELLED})[1:], ("2 Jan 20, 10:00 · 3.0 GB · Cancelled", False))

    def test_recent_line(self):                                          # HOME-5
        entry = {"path": str(Path("/share") / "Job" / "Out" / "Test Site.treeprof"), "site": "Test Site",
                 "capture_date": "2025-02-13", "trees": 1234, "opened_at": "2020-01-02T10:00:00+11:00"}
        self.assertEqual(present.recent_line(entry), ("Test Site – 13 Feb 25",
                                                      f"1,234 trees · opened 2 Jan 20, 10:00 · {Path('/share') / 'Job' / 'Out'}"))
        self.assertEqual(present.recent_line({"path": "x.treeprof", "site": None})[0], "?")


if __name__ == "__main__":
    unittest.main()


class RemovalNote(unittest.TestCase):                                             # STO-6
    def test_only_in_the_last_week(self):
        now = dt.datetime(2026, 11, 12, 12, 0).astimezone()
        note = lambda days: present.removal_note(now + dt.timedelta(days=days), now)
        self.assertEqual([note(d) for d in (30, 8, 7, 2, 1, 0, -3)],
                         ["", "", "Removed in 7 days", "Removed in 2 days", "Removed tomorrow", "Removed today",
                          "Removed today"])
        self.assertEqual(present.removal_note(None, now), "")

    def test_the_storage_page_says_what_happens(self):
        self.assertIn("removed from this computer 30 days after", present.storage_note(30))
        self.assertIn("1 day after", present.storage_note(1))
        self.assertTrue(present.storage_note(0).startswith("Runs are never removed by themselves"))
