"""The report: report.html of run_segmentanytree.bat plus "Trees to check" (spec RES-11)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import report, steps, summary
from tests.helpers import TREES_CSV, make_workdir

REPORT_MD = ("# Tree profiling: Test Site\n\n## Results on this site\n\n| Model | Trees found |\n|---|---|\n"
             "| sat | 6 |\n| sat_merged | 4 |\n\n## Figures\n\n![crowns](fig.png)\n\n## Notes\n\n"
             "- Outputs per model are in sub-folders: `trees_crowns.gpkg`\n- `run_metadata.json` records the input.\n"
             "- Crown base, volumes and density are LiDAR proxies.\n")


def tree(i, area=10.0, diam=3.6, width=4.0, flag="ok"):
    return {"tree_uid": f"TEST_SITE_TREE{i:05d}", "tree_id": i, "height_m": 10.0 + i, "crown_area_m2": area,
            "crown_diam_m": diam, "crown_width_max_m": width, "qc_flag": flag, "top_x": 500000.0 + i,
            "top_y": 6250000.0 + i}


class Picking(unittest.TestCase):
    def test_only_trees_worth_a_look(self):
        trees = [tree(i) for i in range(1, 11)]
        trees.append(tree(11, area=45.0, diam=7.6, width=9.0))              # 4.5 x the median: large
        trees.append(tree(12, diam=3.0, width=7.0))                          # long and narrow
        trees.append(tree(13, flag="edge"))
        trees.append(tree(14, flag="few_points,edge"))
        found = report.trees_to_check(trees)
        self.assertEqual([(t["tree_id"], why) for t, why in found],
                         [(11, ["large"]), (12, ["long"]), (13, ["edge"]), (14, ["edge", "few_points"])])

    def test_small_crowns_are_never_large(self):
        trees = [tree(i, area=2.0) for i in range(1, 10)] + [tree(10, area=15.0)]   # 7.5 x median but < 20 m2
        self.assertEqual(report.trees_to_check(trees), [])

    def test_empty_cells_and_no_trees(self):
        self.assertEqual(report.trees_to_check([]), [])
        self.assertEqual(report.trees_to_check([{"tree_id": 1, "qc_flag": None}]), [])


class Text(unittest.TestCase):
    def test_section_goes_before_the_figures_and_unsaved_files_are_not_mentioned(self):
        trees = [tree(1), tree(2, flag="edge")]
        text = report.make_report_text(REPORT_MD, trees)
        self.assertLess(text.index("## Results on this site"), text.index("## Trees to check"))
        self.assertLess(text.index("## Trees to check"), text.index("## Figures"))
        self.assertIn("1 of 2 trees", text)
        self.assertIn("| TEST_SITE_TREE00002 | At the edge of the data: the crown may be cut | 12.0 |", text)
        self.assertNotIn("TEST_SITE_TREE00001", text)
        self.assertNotIn("Outputs per model", text)
        self.assertNotIn("run_metadata.json", text)
        self.assertIn("LiDAR proxies", text)

    def test_long_lists_are_cut(self):
        trees = [tree(i, flag="edge") for i in range(1, report.MAX_ROWS + 6)]
        text = report.check_section(trees)
        self.assertEqual(text.count("| TEST_SITE_TREE"), report.MAX_ROWS)
        self.assertIn("5 more trees to check", text)

    def test_nothing_to_check(self):
        text = report.check_section([tree(1), tree(2)])
        self.assertIn("0 of 2 trees", text)
        self.assertNotIn("| Tree ID |", text)

    def test_html_is_made_like_process2(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "fig.png").write_bytes(b"\x89PNG")
        page = report.html_from_md(report.make_report_text(REPORT_MD, [tree(1, flag="edge")]), tmp)
        self.assertIn("<h1>Tree profiling: Test Site</h1>", page)
        self.assertIn("<h2>Trees to check</h2>", page)
        self.assertIn("<th>Tree ID</th>", page)
        self.assertIn('src="data:image/png;base64,iVBORw==', page)      # the picture is inside the page
        self.assertIn("th{background:#DCE6F0}", page)                     # the old report's look


class Build(unittest.TestCase):
    def test_build_in_a_working_folder(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        w = make_workdir(tmp / "run")
        folder = w / steps.REPORT_DIR
        (folder / "external_sat").mkdir(parents=True)
        (folder / "external_sat" / "points_treeid.laz").write_bytes(b"big")
        (folder / "external_sat" / "trees.csv").write_text("tree_id\n")
        (folder / "fig.png").write_bytes(b"\x89PNG")
        (folder / "report.md").write_text(REPORT_MD, encoding="utf-8")
        n = report.build(w)
        self.assertEqual(n, len(report.trees_to_check(summary.load_trees(w / steps.TREES_CSV))))
        page = (w / steps.REPORT_HTML).read_text(encoding="utf-8")
        self.assertIn("TEST_SITE_TREE00003", page)                       # at the edge in TREES_CSV
        self.assertFalse((folder / "external_sat" / "points_treeid.laz").exists())   # full-size copies removed
        self.assertTrue((folder / "external_sat" / "trees.csv").exists())
        self.assertFalse((w / (steps.REPORT_HTML + ".part")).exists())
        self.assertIn("TEST_SITE_TREE", TREES_CSV)


if __name__ == "__main__":
    unittest.main()
