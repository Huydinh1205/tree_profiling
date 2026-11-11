"""Test plan A-10 and part of A-27 (spec SAVE-2 to SAVE-8, OPEN-3)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from treeprof_app import filecopy, projectfile, saving
from tests.helpers import folder_snapshot, make_workdir

SITE = "Test Site"


def project():
    return projectfile.new_project(site_name=SITE, run_by="Sam", app_version="0.1.0",
                                   files={"trees_csv": "sat_result_merged_trees.csv",
                                          "crowns_shp": "sat_result_merged_crowns.shp",
                                          "info_laz": "sat_result_merged_info.laz"})


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.w = make_workdir(self.tmp / "run")
        self.before = folder_snapshot(self.w)
        self.dest = self.tmp / "shared" / "Test Site – 13 Feb 25 – Tree Profiling"

    def names(self, folder=None):
        folder = folder or self.dest
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def assert_workdir_untouched(self):
        self.assertEqual(folder_snapshot(self.w), self.before)


class Plan(Base):
    def test_default_selection(self):
        plan = saving.make_plan(self.w, self.dest, SITE)
        self.assertEqual(sorted(d.name for _, d in plan.copies),
                         ["Test Site_crowns.dbf", "Test Site_crowns.prj", "Test Site_crowns.shp",
                          "Test Site_crowns.shx", "Test Site_info.laz", "Test Site_trees.csv"])
        self.assertEqual(plan.files, {"trees_csv": "Test Site_trees.csv", "crowns_shp": "Test Site_crowns.shp",
                                      "info_laz": "Test Site_info.laz", "sat_merged_laz": None, "sat_raw_laz": None})
        self.assertEqual(plan.project_path, self.dest / "Test Site.treeprof")
        self.assertEqual(plan.total_bytes, sum(s.stat().st_size for s, _ in plan.copies))
        self.assertEqual(plan.conflicts, [])

    def test_required_files_cannot_be_unticked(self):
        plan = saving.make_plan(self.w, self.dest, SITE, selected=set())
        self.assertEqual(plan.files["trees_csv"], "Test Site_trees.csv")
        self.assertEqual(plan.files["crowns_shp"], "Test Site_crowns.shp")
        self.assertEqual(plan.files["info_laz"], "Test Site_info.laz")       # the coloured cloud is always saved
        self.assertIsNone(plan.files["sat_raw_laz"])

    def test_big_files_when_ticked(self):
        plan = saving.make_plan(self.w, self.dest, SITE, selected={"sat_merged_laz", "sat_raw_laz"})
        self.assertEqual((plan.files["sat_merged_laz"], plan.files["sat_raw_laz"]),
                         ("Test Site_sat_merged.laz", "Test Site_sat_raw.laz"))
        sources = {s.name: d.name for s, d in plan.copies}
        self.assertEqual(sources["sat_result.laz"], "Test Site_sat_raw.laz")
        self.assertEqual(sources["sat_result_merged.laz"], "Test Site_sat_merged.laz")

    def test_optional_shapefile_part_missing_is_fine(self):
        (self.w / "sat_result_merged_crowns.prj").unlink()
        plan = saving.make_plan(self.w, self.dest, SITE)
        self.assertNotIn("Test Site_crowns.prj", [d.name for _, d in plan.copies])

    def test_missing_source_is_named(self):
        (self.w / "sat_result_merged_crowns.dbf").unlink()
        with self.assertRaises(saving.SaveError) as cm:
            saving.make_plan(self.w, self.dest, SITE)
        self.assertEqual(cm.exception.code, "missing_source")
        self.assertIn("sat_result_merged_crowns.dbf", str(cm.exception))

    def test_site_name_is_made_safe(self):
        plan = saving.make_plan(self.w, self.dest, 'A/B: "Site"')
        self.assertEqual(plan.project_path.name, "A B Site.treeprof")

    def test_no_space(self):
        plan = saving.make_plan(self.w, self.dest, SITE)
        with self.assertRaises(saving.SaveError) as cm:
            saving.check_destination(plan, free_bytes=10)
        self.assertEqual(cm.exception.code, "no_space")
        self.assertEqual(self.names(self.tmp), ["run"])                          # nothing was created

    @unittest.skipIf(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "needs a read-only folder and a normal user")
    def test_read_only_folder(self):
        locked = self.tmp / "locked"
        locked.mkdir()
        os.chmod(locked, 0o555)
        self.addCleanup(os.chmod, locked, 0o755)
        plan = saving.make_plan(self.w, locked / "out", SITE)
        with self.assertRaises(saving.SaveError) as cm:
            saving.execute(plan, project())
        self.assertEqual(cm.exception.code, "not_writable")
        self.assertEqual(self.names(locked), [])


class Execute(Base):
    def save(self, **kw):
        plan = kw.pop("plan", None) or saving.make_plan(self.w, self.dest, SITE)
        return plan, saving.execute(plan, project(), **kw)

    def test_save(self):
        seen = []
        plan, path = self.save(progress=lambda done, total, name: seen.append((done, total, name)))
        self.assertEqual(self.names(), ["Test Site.treeprof", "Test Site_crowns.dbf", "Test Site_crowns.prj",
                                        "Test Site_crowns.shp", "Test Site_crowns.shx", "Test Site_info.laz",
                                        "Test Site_trees.csv"])
        for src, dst in plan.copies:
            self.assertEqual(src.read_bytes(), dst.read_bytes())
        data = projectfile.read_project(path)
        self.assertEqual(data["files"], plan.files)
        self.assertEqual(projectfile.check_openable(path, data), [])
        self.assertEqual(seen[-1][:2], (plan.total_bytes, plan.total_bytes))
        self.assertEqual([s[0] for s in seen], sorted(s[0] for s in seen))
        self.assert_workdir_untouched()

    def test_project_file_is_written_last(self):
        plan = saving.make_plan(self.w, self.dest, SITE)

        def copy_file(src, dst, **kw):
            self.assertFalse(plan.project_path.exists(), "the .treeprof file must come after every copy")
            return filecopy.copy_with_hash(src, dst, **kw)

        saving.execute(plan, project(), copy_file=copy_file)
        self.assertTrue(plan.project_path.exists())

    def test_coloured_cloud_is_saved_even_when_nothing_is_ticked(self):
        plan = saving.make_plan(self.w, self.dest, SITE, selected=set())
        path = saving.execute(plan, project())
        data = projectfile.read_project(path)
        self.assertEqual(data["files"]["info_laz"], "Test Site_info.laz")
        self.assertIn("Test Site_info.laz", self.names())
        self.assertEqual(projectfile.check_openable(path, data), [])

    def test_saved_folder_can_be_moved_and_reopened(self):                       # A-27, OPEN-3
        self.save()
        moved = self.tmp / "usb" / "renamed"
        shutil.copytree(self.dest, moved)
        shutil.rmtree(self.dest)
        path = projectfile.find_in_folder(moved)
        self.assertEqual(projectfile.check_openable(path, projectfile.read_project(path)), [])

    def test_same_place_twice_asks_first(self):                                  # SAVE-5
        self.save()
        plan = saving.make_plan(self.w, self.dest, SITE)
        self.assertEqual(len(plan.conflicts), 7)
        with self.assertRaises(saving.SaveError) as cm:
            saving.execute(plan, project())
        self.assertEqual(cm.exception.code, "conflict")

    def test_replace(self):
        self.save()
        (self.dest / "Test Site_trees.csv").write_text("changed by someone")
        plan = saving.make_plan(self.w, self.dest, SITE)
        saving.execute(plan, project(), replace=True)
        self.assertEqual((self.dest / "Test Site_trees.csv").read_bytes(),
                         (self.w / "sat_result_merged_trees.csv").read_bytes())
        self.assertEqual(len(self.names()), 7)

    def test_keep_both(self):
        self.save()
        plan = saving.plan_keeping_both(self.w, self.dest, SITE)
        self.assertEqual((plan.stem, plan.conflicts), ("Test Site (2)", []))
        path = saving.execute(plan, project())
        self.assertEqual(path.name, "Test Site (2).treeprof")
        self.assertEqual(len(self.names()), 14)
        self.assertEqual(projectfile.check_openable(path, projectfile.read_project(path)), [])
        self.assertEqual(saving.plan_keeping_both(self.w, self.dest, SITE).stem, "Test Site (3)")

    def failing_copy(self, fail_on, error):
        count = {"n": 0}

        def copy_file(src, dst, **kw):
            count["n"] += 1
            if count["n"] == fail_on:
                Path(str(dst) + ".part").write_bytes(b"half")
                raise error
            return filecopy.copy_with_hash(src, dst, **kw)
        return copy_file

    def test_failure_half_way_leaves_nothing(self):                              # SAVE-7
        plan = saving.make_plan(self.w, self.dest, SITE)
        with self.assertRaises(saving.SaveError) as cm:
            saving.execute(plan, project(), copy_file=self.failing_copy(3, filecopy.CopyError("network dropped")))
        self.assertEqual(cm.exception.code, "copy_failed")
        self.assertFalse(self.dest.exists())                                     # the folder we made is gone too
        self.assert_workdir_untouched()

    def test_failure_keeps_what_was_already_in_the_folder(self):
        self.dest.mkdir(parents=True)
        (self.dest / "notes.txt").write_text("someone else's file")
        plan = saving.make_plan(self.w, self.dest, SITE)
        with self.assertRaises(saving.SaveError):
            saving.execute(plan, project(), copy_file=self.failing_copy(4, OSError("disk full")))
        self.assertEqual(self.names(), ["notes.txt"])

    def test_cancel(self):
        plan = saving.make_plan(self.w, self.dest, SITE)
        with self.assertRaises(saving.SaveCancelled):
            saving.execute(plan, project(), copy_file=self.failing_copy(2, filecopy.CopyCancelled()))
        self.assertFalse(self.dest.exists())
        self.assert_workdir_untouched()

    def test_try_again_after_a_failure_works(self):
        plan = saving.make_plan(self.w, self.dest, SITE)
        with self.assertRaises(saving.SaveError):
            saving.execute(plan, project(), copy_file=self.failing_copy(3, filecopy.CopyError("network dropped")))
        plan = saving.make_plan(self.w, self.dest, SITE)
        self.assertEqual(plan.conflicts, [])
        saving.execute(plan, project())
        self.assertEqual(len(self.names()), 7)


if __name__ == "__main__":
    unittest.main()
