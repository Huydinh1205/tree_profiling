"""Test plan A-01, A-02, A-03 (spec 6.4, 6.5, RUN-3, RUN-4, RUN-8).

The sample outputs below are written from the print statements of sat_docker.py, merge_sat.py and
tree_info.py (commit 85aeeac). They are stand-ins until real outputs captured on the processing
computer are added to tests/samples (test data D7).
"""
import unittest

from treeprof_app import progress as pg

TILED = """\
SegmentAnyTree on cloud.laz (61,234,567 points). Docker output also goes to results\\cloud_sat\\sat_docker.log
Too many points for one SegmentAnyTree run (61,234,567 > 20,000,000): 2 x 2 tiles of 210 x 190 m (+ 20 m buffer), 4 Docker runs. A tile already done is skipped.

--- tile 1/4 (column 1, row 1): 14,000,000 points + buffer = 18,500,000: already done (tile_01_01.laz), skipped

--- tile 2/4 (column 1, row 2): 15,000,000 points + buffer = 19,000,000
INFO some line printed by Docker
100%|##########| 12/12 [00:31<00:00]
  19,000,000 result points mapped onto 19,000,000 input points
  done in 12.5 min -> results\\cloud_sat\\sat_result_tiles_210x190m\\tile_01_02.laz

--- tile 3/4 (column 2, row 1): 16,000,000 points + buffer = 19,900,000
Segmentation fault (core dumped)

SegmentAnyTree produced no result for tile 3/4 (column 2, row 1): 16,000,000 points + buffer = 19,900,000 (Docker exit code 139).
SegmentAnyTree crashed (Segmentation fault).
Full output in sat_docker.log.

--- tile 3/4 (column 2, row 1): 16,000,000 points + buffer = 19,900,000, thinned to one point per 5 cm: 9,000,000 points
  9,000,000 result points mapped onto 19,900,000 input points
  done in 7.5 min -> results\\cloud_sat\\sat_result_tiles_210x190m\\tile_02_01.laz

--- tile 4/4 (column 2, row 2): 16,234,567 points + buffer = 19,950,000
  done in 10.0 min -> results\\cloud_sat\\sat_result_tiles_210x190m\\tile_02_02.laz

All 4 tiles done. Putting them together ...
Writing results\\cloud_sat\\sat_result.laz ...
  1,234 trees in sat_result.laz
Done in 31.2 min -> results\\cloud_sat\\sat_result.laz
""".splitlines()

SINGLE = """\
SegmentAnyTree on small.laz (5,000,000 points). Docker output also goes to out\\sat_docker.log
INFO some line printed by Docker

SegmentAnyTree done in 8.3 min. Writing out\\sat_result.laz ...
  5,000,100 result points mapped onto 5,000,000 input points
Done in 9.0 min -> out\\sat_result.laz
""".splitlines()

OUT_OF_RAM = """\
SegmentAnyTree on big.laz (150,000,000 points). Docker output also goes to out\\sat_docker.log
joblib.externals.loky.process_executor.TerminatedWorkerError: A worker process was terminated {SIGKILL(-9)}

SegmentAnyTree: SegmentAnyTree produced no result (Docker exit code 137).
OUT OF RAM: Windows/WSL killed SegmentAnyTree (SIGKILL) because the file is too big for the memory Docker may use.
Full output in sat_docker.log.
""".splitlines()

TILES_FAILED = """\
--- tile 1/2 (column 1, row 1): 14,000,000 points + buffer = 18,500,000
RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB

SegmentAnyTree produced no result for tile 1/2 (column 1, row 1): 14,000,000 points + buffer = 18,500,000 (Docker exit code 1).
CUDA out of memory = too big for the GPU: run again with a smaller --max-points (e.g. 10000000).
Full output in sat_docker.log.

--- tile 1/2 (column 1, row 1): 14,000,000 points + buffer = 18,500,000, thinned to one point per 5 cm: 8,000,000 points
Segmentation fault (core dumped)

SegmentAnyTree produced no result for tile 1/2 (column 1, row 1): 14,000,000 points + buffer = 18,500,000, thinned to one point per 5 cm: 8,000,000 points (Docker exit code 139).
SegmentAnyTree crashed (Segmentation fault).
Full output in sat_docker.log.

SegmentAnyTree: SegmentAnyTree failed on 1 tile(s) even thinned: tile_01_01.
The other tiles are done and kept in out\\sat_result_tiles_210x190m; running again only retries these. Try a smaller --max-points (e.g. 10000000), or send this window and the log to Ryan.
""".splitlines()

MERGE = """\
sat_result.laz: 61,234,567 points, ground from file, field PredInstance_SAT, 1310 trees
  merged into 1234 trees  ({'no own top': 60, 'fragment': 10}; 35% of the pieces taller than 5 m show a stem; 6 trees lower than 3 m dropped)
  wrote sat_result_merged.laz, sat_result_merged_colour.laz, .png and _log.csv in 41 s
""".splitlines()

MEASURE = """\

Reading sat_result_merged.laz ...
  61,234,567 points, tree IDs from 'PredInstance_SAT', ground: file
  1234 trees measured
  -> sat_result_merged_info.laz, sat_result_merged_trees.csv, 1230 crown outlines -> sat_result_merged_crowns.shp  (95s)
""".splitlines()


def run(parser, lines):
    for line in lines:
        parser.feed(line)
    return parser


class FindTreesWithTiles(unittest.TestCase):                                     # A-01
    def test_step_by_step(self):
        p = pg.SatParser()
        s = p.state
        self.assertEqual((s.phase, s.fraction, s.label), ("starting", None, "Finding trees"))
        p.feed(TILED[0])
        self.assertEqual((s.phase, s.input_points), ("running", 61234567))
        p.feed(TILED[1])
        self.assertEqual((s.phase, s.tiles_total, s.fraction), ("tiles", 4, 0.0))
        run(p, TILED[2:4])                                   # tile 1 was done in an earlier run
        self.assertEqual((s.tiles_done, s.tiles_skipped, s.fraction, s.label), (1, {1}, 0.25, "Tile 1 of 4"))
        self.assertIsNone(s.eta_minutes)                     # no tile timed yet
        run(p, TILED[4:6])
        self.assertEqual((s.tile_current, s.tiles_done, s.label), (2, 1, "Tile 2 of 4"))
        run(p, TILED[6:10])
        self.assertEqual((s.tiles_done, s.tile_minutes, s.fraction), (2, [12.5], 0.5))
        self.assertEqual(s.eta_minutes, 25.0)                # 2 tiles left x 12.5 min
        run(p, TILED[10:17])                                 # tile 3 crashes: not counted as done
        self.assertEqual((s.tiles_done, s.phase), (2, "tiles"))
        run(p, TILED[17:19])                                 # same tile again, thinned
        self.assertEqual((s.tiles_thinned, s.label), ({3}, "Retrying tile 3 with fewer points"))
        run(p, TILED[19:21])
        self.assertEqual((s.tiles_done, s.eta_minutes), (3, 10.0))
        run(p, TILED[21:25])
        self.assertEqual((s.tiles_done, s.fraction), (4, 1.0))
        p.feed(TILED[25])
        self.assertEqual((s.phase, s.label), ("assembling", "Putting the tiles together"))
        p.feed(TILED[26])
        self.assertEqual(s.phase, "writing")
        run(p, TILED[27:])
        self.assertEqual((s.phase, s.trees, s.minutes, s.fraction), ("finished", 1234, 31.2, 1.0))
        self.assertIsNone(s.fail_message)

    def test_whole_output(self):
        s = run(pg.SatParser(), TILED).state
        self.assertEqual((s.tiles_total, s.tiles_done, s.tiles_skipped, s.tiles_thinned), (4, 4, {1}, {3}))
        self.assertEqual(s.tile_minutes, [12.5, 7.5, 10.0])

    def test_lines_with_newlines_and_bytes(self):
        s = run(pg.SatParser(), [(x + "\r\n").encode("utf-8") for x in TILED]).state
        self.assertEqual((s.phase, s.tiles_done), ("finished", 4))


class FindTreesInOneRun(unittest.TestCase):                                      # A-01
    def test_no_tiles_means_indeterminate_bar(self):
        p = pg.SatParser()
        run(p, SINGLE[:2])
        self.assertEqual((p.state.phase, p.state.fraction, p.state.eta_minutes), ("running", None, None))
        run(p, SINGLE[2:4])
        self.assertEqual(p.state.phase, "writing")
        run(p, SINGLE[4:])
        self.assertEqual((p.state.phase, p.state.fraction, p.state.minutes), ("finished", 1.0, 9.0))

    def test_model_download(self):
        p = pg.SatParser()
        p.feed("Downloading the SegmentAnyTree image ghcr.io/3dtrees-earth/3dtrees_sat:1.1.0 (several GB, "
               "only the first time)...")
        self.assertEqual((p.state.phase, p.state.label), ("downloading_model", "Downloading model (first time only)"))

    def test_failure_message_is_kept(self):
        s = run(pg.SatParser(), OUT_OF_RAM).state
        self.assertEqual(s.phase, "failed")
        self.assertIn("OUT OF RAM", s.fail_message)
        self.assertIn("Docker exit code 137", s.fail_message)


class OtherSteps(unittest.TestCase):                                             # A-01
    def test_join(self):
        p = run(pg.MergeParser(), MERGE)
        self.assertEqual(p.info, {"trees_before": 1310, "trees_after": 1234, "dropped_low": 6, "finished": True})

    def test_measure(self):
        p = run(pg.MeasureParser(), MEASURE)
        self.assertEqual(p.info, {"points": 61234567, "ground_source": "file", "trees": 1234,
                                  "crowns_skipped": None, "finished": True})

    def test_measure_without_crown_outlines(self):
        p = run(pg.MeasureParser(), ["  -> x_info.laz, x_trees.csv (crown outlines skipped: no CRS (bad))  (3s)"])
        self.assertEqual(p.info["crowns_skipped"], "no CRS (bad)")

    def test_parser_for(self):
        self.assertIsInstance(pg.parser_for("find_trees"), pg.SatParser)
        self.assertIsInstance(pg.parser_for("join"), pg.MergeParser)
        self.assertIsInstance(pg.parser_for("measure"), pg.MeasureParser)


class StrangeLines(unittest.TestCase):                                           # A-02
    JUNK = ["", "   ", None, 123, b"\xff\xfe\x00bad bytes", "--- tile x/y (column a, row b)", "done in fast min -> ",
            "--- tile 99999999999999999999/3 (column 1, row 1): 1", "x" * 100000, "Traceback (most recent call last):",
            "  done in 3.0 min -> orphan line before any tile", "All tiles done", "\x00\x01\x02"]

    def test_never_raises_and_bar_stays_indeterminate(self):
        for cls in (pg.SatParser, pg.MergeParser, pg.MeasureParser):
            p = cls()
            for line in self.JUNK:
                p.feed(line)
        s = run(pg.SatParser(), [x for x in self.JUNK if x != self.JUNK[7]]).state
        self.assertIsNone(s.fraction)
        self.assertEqual(s.tiles_done, 0)

    def test_junk_between_real_lines_changes_nothing(self):
        mixed = []
        for line in TILED:
            mixed += [line, "some unrelated line", ""]
        s = run(pg.SatParser(), mixed).state
        self.assertEqual((s.phase, s.tiles_done, s.trees), ("finished", 4, 1234))


class Failures(unittest.TestCase):                                               # A-03
    def kind(self, step, lines):
        return pg.classify_failure(step, lines)

    def test_out_of_ram(self):
        f = self.kind("find_trees", OUT_OF_RAM)
        self.assertEqual(f.kind, "out_of_ram")
        self.assertEqual(f.message, "This file is too large to process in one piece on this computer.")
        self.assertIn(pg.SMALLER_TILES, f.actions)

    def test_last_failure_decides(self):
        f = self.kind("find_trees", TILES_FAILED)           # GPU memory first, then a crash when thinned
        self.assertEqual(f.kind, "crash")
        self.assertEqual(f.actions, (pg.RETRY, pg.SEND_LOG, pg.DETAILS))

    def test_gpu_memory(self):
        f = self.kind("find_trees", TILES_FAILED[:6])
        self.assertEqual((f.kind, f.message), ("gpu_memory", "The graphics card ran out of memory."))
        self.assertIn(pg.SMALLER_TILES, f.actions)

    def test_crash_recovered_earlier_then_unknown_error(self):
        f = self.kind("find_trees", ["Segmentation fault (core dumped)", "Traceback (most recent call last):",
                                     "ValueError: boom"])
        self.assertEqual(f.kind, "crash")

    def test_docker_problems(self):
        cases = {
            "docker_not_running": "SegmentAnyTree: Docker Desktop is not running. Open Docker Desktop, wait for "
                                  "'Engine running', then run again.",
            "docker_missing": "SegmentAnyTree: Docker not found. Install Docker Desktop (WSL 2 engine), then open "
                              "a NEW window and run again.",
            "docker_gpu": "SegmentAnyTree: Docker cannot use the GPU (--gpus all failed). Check the NVIDIA driver",
            "image_download": "SegmentAnyTree: could not download the image. Check the internet connection.",
            "input_missing": "SegmentAnyTree: input not found: C:\\x\\cloud.laz",
        }
        for kind, line in cases.items():
            f = self.kind("find_trees", ["", line])
            self.assertEqual(f.kind, kind)
            self.assertTrue(f.message)
        self.assertEqual(self.kind("find_trees", ["", cases["docker_not_running"]]).actions, (pg.RETRY,))

    def test_no_trees_is_an_empty_result_not_an_error(self):
        for step, line in (("measure", "No trees in this file (all tree IDs are 0)."),
                           ("measure", "No tree ID field in this file (looked for PredInstance_SAT). Fields: []"),
                           ("join", "sat_result.laz: no tree ID field (looked for ['PredInstance_SAT']). Fields: []")):
            f = self.kind(step, [line])
            self.assertEqual(f.kind, "no_trees")
            self.assertTrue(f.is_empty_result)
            self.assertEqual(f.message, "No trees were found in this file.")

    def test_unknown_names_the_step(self):
        f = self.kind("join", ["Traceback (most recent call last):", "ValueError: boom"])
        self.assertEqual(f.kind, "unknown")
        self.assertEqual(f.message, "Something went wrong in step Join split crowns.")
        self.assertEqual(f.actions, (pg.RETRY, pg.DETAILS, pg.SEND_LOG))
        self.assertIn("ValueError: boom", f.detail)
        self.assertFalse(f.is_empty_result)

    def test_no_output_at_all(self):
        self.assertEqual(self.kind("measure", []).kind, "unknown")


if __name__ == "__main__":
    unittest.main()
