"""Test plan A-14 (spec RES-1, 7.2)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import summary
from tests.helpers import TREES_CSV


class Summary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.csv = self.tmp / "trees.csv"
        self.csv.write_text(TREES_CSV, encoding="utf-8")

    def test_load(self):
        trees = summary.load_trees(self.csv)
        self.assertEqual(len(trees), 4)
        t = trees[0]
        self.assertEqual((t["tree_uid"], t["tree_id"], t["height_m"], t["n_points"], t["qc_flag"]),
                         ("TEST_SITE_TREE00001", 1, 10.0, 800, "ok"))
        self.assertIsInstance(t["tree_id"], int)
        self.assertIsNone(t["species_code"])                 # columns kept for later are empty
        self.assertIsNone(trees[3]["height_m"])              # an empty cell

    def test_overview(self):
        s = summary.summarise(summary.load_trees(self.csv), area_ha=2.0)
        self.assertEqual(s, {"trees": 4, "height_mean_m": 20.0, "height_median_m": 20.0, "height_max_m": 30.0,
                             "area_ha": 2.0, "trees_per_ha": 2.0,
                             "qc": {"ok": 2, "few_points": 1, "edge": 1, "few_points,edge": 0}})

    def test_without_area(self):
        s = summary.summarise(summary.load_trees(self.csv))
        self.assertEqual((s["area_ha"], s["trees_per_ha"]), (None, None))

    def test_no_trees(self):
        s = summary.summarise([])
        self.assertEqual((s["trees"], s["height_mean_m"], s["height_max_m"]), (0, None, None))
        self.assertEqual(sum(s["qc"].values()), 0)

    def test_extra_and_missing_columns(self):
        self.csv.write_text("tree_id,height_m,new_column,qc_flag\n1,12.5,hello,ok\n2,nan,,odd_flag\n3,abc,,\n",
                            encoding="utf-8-sig")
        trees = summary.load_trees(self.csv)
        self.assertEqual(trees[0], {"tree_id": 1, "height_m": 12.5, "new_column": "hello", "qc_flag": "ok"})
        self.assertEqual((trees[1]["height_m"], trees[2]["height_m"]), (None, None))
        s = summary.summarise(trees)
        self.assertEqual((s["trees"], s["height_mean_m"]), (3, 12.5))
        self.assertEqual((s["qc"]["ok"], s["qc"]["odd_flag"]), (2, 1))

    def test_every_flag_has_an_explanation(self):
        self.assertEqual(set(summary.QC_HELP), set(summary.QC_FLAGS))


if __name__ == "__main__":
    unittest.main()
