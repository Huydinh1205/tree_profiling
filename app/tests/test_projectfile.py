"""Test plan A-07, A-08 (spec 7.1, OPEN-3, OPEN-5, OPEN-6, OPEN-7)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import projectfile as pf


def sample(**over):
    kw = dict(site_name="Test Site", run_by="Sam", app_version="0.1.0", capture_date="2025-02-13",
              files={"trees_csv": "Test Site_trees.csv", "crowns_shp": "Test Site_crowns.shp",
                     "info_laz": "Test Site_info.laz"},
              summary={"trees": 4})
    kw.update(over)
    return pf.new_project(**kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def saved_folder(self, name="saved", data=None):
        folder = self.tmp / name
        folder.mkdir()
        for f in ("Test Site_trees.csv", "Test Site_crowns.shp", "Test Site_crowns.shx", "Test Site_crowns.dbf",
                  "Test Site_info.laz"):
            (folder / f).write_bytes(b"x")
        path = pf.write_project(folder / "Test Site.treeprof", data or sample())
        return folder, path


class RoundTrip(Base):                                                           # A-07
    def test_write_then_read_is_identical(self):
        data = sample()
        path = pf.write_project(self.tmp / "a.treeprof", data)
        self.assertEqual(pf.read_project(path), data)

    def test_layout(self):
        data = sample()
        self.assertEqual(data["format"], "treeprof")
        self.assertEqual(data["format_version"], 1)
        self.assertEqual(set(data["files"]), set(pf.FILE_KEYS))
        self.assertIsNone(data["files"]["sat_raw_laz"])
        self.assertTrue(data["run_id"])

    def test_no_part_file_left(self):
        pf.write_project(self.tmp / "a.treeprof", sample())
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.treeprof"])

    def test_accents_survive(self):
        data = sample(site_name="Rừng Tràm – Đợt 1")
        path = pf.write_project(self.tmp / "a.treeprof", data)
        self.assertEqual(pf.read_project(path)["site_name"], data["site_name"])
        self.assertIn("Rừng", path.read_text(encoding="utf-8"))

    def test_unknown_fields_are_kept_and_ignored(self):
        data = sample()
        data["something_new"] = {"a": 1}
        path = pf.write_project(self.tmp / "a.treeprof", data)
        self.assertEqual(pf.read_project(path)["something_new"], {"a": 1})

    def test_file_with_byte_order_mark(self):
        path = self.tmp / "a.treeprof"
        path.write_text(json.dumps(sample()), encoding="utf-8-sig")
        self.assertEqual(pf.read_project(path)["site_name"], "Test Site")

    def test_paths_are_relative_so_the_folder_can_move(self):                    # OPEN-3
        folder, path = self.saved_folder()
        moved = self.tmp / "elsewhere" / "renamed"
        shutil.copytree(folder, moved)
        shutil.rmtree(folder)
        new_path = pf.find_in_folder(moved)
        data = pf.read_project(new_path)
        self.assertEqual(pf.check_openable(new_path, data), [])
        self.assertEqual(pf.result_paths(new_path, data)["trees_csv"], moved / "Test Site_trees.csv")


class BadFiles(Base):                                                            # A-08
    def code(self, text):
        path = self.tmp / "bad.treeprof"
        path.write_text(text, encoding="utf-8")
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.read_project(path)
        return cm.exception.code

    def test_newer_version(self):
        data = sample()
        data["format_version"] = 2
        self.assertEqual(self.code(json.dumps(data)), "newer_version")

    def test_not_json(self):
        self.assertEqual(self.code("{ this is not json"), "not_json")
        self.assertEqual(self.code(""), "not_json")

    def test_other_json(self):
        self.assertEqual(self.code('{"hello": 1}'), "not_treeprof")
        self.assertEqual(self.code("[1, 2]"), "not_treeprof")

    def test_missing_fields(self):
        for drop in ("site_name", "format_version", "files"):
            data = sample()
            del data[drop]
            self.assertEqual(self.code(json.dumps(data)), "missing_field", drop)
        data = sample()
        data["files"]["trees_csv"] = None
        self.assertEqual(self.code(json.dumps(data)), "missing_field")

    def test_file_does_not_exist(self):
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.read_project(self.tmp / "nothing.treeprof")
        self.assertEqual(cm.exception.code, "not_found")

    def test_an_invalid_project_is_never_written(self):
        with self.assertRaises(pf.ProjectFileError):
            pf.write_project(self.tmp / "a.treeprof", {"format": "treeprof"})
        self.assertEqual(list(self.tmp.iterdir()), [])


class MissingResultFiles(Base):                                                  # OPEN-5
    def test_missing_3d_file_still_opens(self):
        folder, path = self.saved_folder()
        (folder / "Test Site_info.laz").unlink()
        optional = pf.check_openable(path, pf.read_project(path))
        self.assertEqual([k for k, _ in optional], ["info_laz"])

    def test_3d_file_not_saved_is_not_missing(self):
        data = sample(files={"trees_csv": "Test Site_trees.csv", "crowns_shp": "Test Site_crowns.shp"})
        folder, path = self.saved_folder(data=data)
        self.assertEqual(pf.check_openable(path, pf.read_project(path)), [])

    def test_missing_table_does_not_open_and_names_the_file(self):
        folder, path = self.saved_folder()
        (folder / "Test Site_trees.csv").unlink()
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.check_openable(path, pf.read_project(path))
        self.assertEqual(cm.exception.code, "missing_file")
        self.assertIn("Test Site_trees.csv", str(cm.exception))
        self.assertIn(str(folder), str(cm.exception))

    def test_shapefile_without_its_parts_does_not_open(self):
        folder, path = self.saved_folder()
        (folder / "Test Site_crowns.dbf").unlink()
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.check_openable(path, pf.read_project(path))
        self.assertIn("Test Site_crowns.dbf", str(cm.exception))


class FindInFolder(Base):                                                        # OPEN-1
    def test_one_project_file(self):
        folder, path = self.saved_folder()
        self.assertEqual(pf.find_in_folder(folder), path)

    def test_none(self):
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.find_in_folder(self.tmp)
        self.assertEqual(cm.exception.code, "not_found")

    def test_two(self):
        folder, path = self.saved_folder()
        shutil.copy(path, folder / "Other.treeprof")
        with self.assertRaises(pf.ProjectFileError) as cm:
            pf.find_in_folder(folder)
        self.assertEqual(cm.exception.code, "ambiguous")


if __name__ == "__main__":
    unittest.main()
