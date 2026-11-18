"""The measuring code of ../processes (treeseg.tree_table, treeseg.split_clusters, tree_info.py, merge_sat.py):
canopy density at map-grid coordinates, and a tree ID made of several separate clusters.
Runs tests/real_pipeline/check_measure.py with the scripts' Python; skipped like test_real_scripts.py."""
import subprocess
import unittest

from tests.real_pipeline.prepare import HERE, SCRIPTS, script_python, why_not

REASON = why_not()


@unittest.skipIf(REASON, REASON)
class MeasureFixes(unittest.TestCase):
    def test_large_coordinates_and_separate_clusters(self):
        r = subprocess.run(script_python() + [str(HERE / "check_measure.py"), str(SCRIPTS)],
                           capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
