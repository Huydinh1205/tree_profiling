"""Test plan A-04, A-05, A-06 (spec NEW-4, SAVE-1, SAVE-2, SAVE-4)."""
import datetime as dt
import unittest

from treeprof_app import naming

D = dt.date(2025, 2, 13)


class FolderName(unittest.TestCase):                                             # A-04
    def test_convention_with_en_dash(self):
        self.assertEqual(naming.parse_folder_name("Test Site \u2013 13 Feb 25 \u2013 LiDAR"), ("Test Site", D))

    def test_hyphen_instead_of_dash(self):
        self.assertEqual(naming.parse_folder_name("Test Site - 13 Feb 25 - LiDAR"), ("Test Site", D))

    def test_no_spaces_around_dash(self):
        self.assertEqual(naming.parse_folder_name("Test Site\u201313 Feb 25\u2013LiDAR"), ("Test Site", D))

    def test_full_month_and_four_digit_year(self):
        self.assertEqual(naming.parse_folder_name("Test Site \u2013 3 February 2025 \u2013 ArcGIS"),
                         ("Test Site", dt.date(2025, 2, 3)))

    def test_slashes_in_date(self):
        self.assertEqual(naming.parse_folder_name("Test Site \u2013 13/Feb/25 \u2013 LiDAR"), ("Test Site", D))

    def test_no_separator_in_date(self):
        self.assertEqual(naming.parse_folder_name("Test Site - 13Feb25 - LiDAR"), ("Test Site", D))

    def test_site_with_a_hyphen(self):
        self.assertEqual(naming.parse_folder_name("Smith-Jones Reserve \u2013 01 Mar 26 \u2013 LiDAR"),
                         ("Smith-Jones Reserve", dt.date(2026, 3, 1)))

    def test_without_last_part(self):
        self.assertEqual(naming.parse_folder_name("Test Site \u2013 13 Feb 25"), ("Test Site", D))

    def test_not_the_convention(self):
        for name in ("terra_lidar", "Project", "Site - 5 Mile Creek - LiDAR", "2025-02-13", "Site - 13 Xyz 25 - L", ""):
            self.assertIsNone(naming.parse_folder_name(name), name)

    def test_impossible_date_keeps_the_site(self):
        self.assertEqual(naming.parse_folder_name("Test Site \u2013 31 Feb 25 \u2013 LiDAR"), ("Test Site", None))


class SiteFromPath(unittest.TestCase):                                           # A-04
    def test_network_path(self):
        p = "\\\\server\\share\\TEST-26001\\Project\\Test Site \u2013 13 Feb 25 \u2013 LiDAR\\terra\\cloud_merged.las"
        self.assertEqual(naming.site_from_path(p), naming.SiteInfo("Test Site", D, "folder"))

    def test_nearest_folder_wins(self):
        p = "D:\\Old Site - 01 Jan 24 - LiDAR\\New Site - 13 Feb 25 - LiDAR\\cloud.laz"
        self.assertEqual(naming.site_from_path(p).site, "New Site")

    def test_falls_back_to_file_name(self):
        self.assertEqual(naming.site_from_path("D:\\data\\cloud_merged.las"),
                         naming.SiteInfo("cloud_merged", None, "file"))

    def test_posix_path(self):
        self.assertEqual(naming.site_from_path("/data/Test Site - 13 Feb 25 - LiDAR/cloud.laz").site, "Test Site")


class ProjectFolder(unittest.TestCase):                                          # SAVE-1
    def test_inside_project(self):
        p = "\\\\server\\share\\TEST-26001\\Project\\Test Site \u2013 13 Feb 25 \u2013 LiDAR\\cloud.las"
        self.assertEqual(naming.project_folder(p), "\\\\server\\share\\TEST-26001\\Project")

    def test_inside_raw(self):
        p = "D:\\TEST-26001\\RAW\\Test Site \u2013 13 Feb 25 \u2013 FLT1\\cloud.las"
        self.assertEqual(naming.project_folder(p), "D:\\TEST-26001\\Project")

    def test_no_job_folder(self):
        self.assertIsNone(naming.project_folder("D:\\data\\cloud.las"))


class SaveFolderName(unittest.TestCase):                                         # A-05
    def test_with_date(self):
        self.assertEqual(naming.suggested_folder_name("Test Site", D), "Test Site \u2013 13 Feb 25 \u2013 Tree Profiling")

    def test_single_digit_day_has_two_digits(self):
        self.assertEqual(naming.suggested_folder_name("Test Site", dt.date(2025, 2, 5)),
                         "Test Site \u2013 05 Feb 25 \u2013 Tree Profiling")

    def test_without_date(self):
        self.assertEqual(naming.suggested_folder_name("Test Site", None), "Test Site \u2013 Tree Profiling")

    def test_free_name_is_kept(self):
        self.assertEqual(naming.next_free_name("A", ["B"]), "A")

    def test_taken_name_gets_v2_then_v3(self):
        self.assertEqual(naming.next_free_name("A", ["A"]), "A v2")
        self.assertEqual(naming.next_free_name("A", ["A", "A v2"]), "A v3")

    def test_windows_names_ignore_case(self):
        self.assertEqual(naming.next_free_name("Test Site", ["test site"]), "Test Site v2")


class TypedDate(unittest.TestCase):                                             # NEW-4
    def test_forms_people_type(self):
        for text in ("13 Feb 25", "13 February 2025", "13/02/2025", "13.02.25", "2025-02-13", " 13-Feb-2025 "):
            self.assertEqual(naming.parse_user_date(text), D, text)

    def test_not_a_date(self):
        for text in ("", "   ", "soon", "31 Feb 25", "13/13/2025", None):
            self.assertIsNone(naming.parse_user_date(text), text)

    def test_round_trip_with_the_display_form(self):
        self.assertEqual(naming.parse_user_date(naming.format_capture_date(D)), D)


class SafeName(unittest.TestCase):                                               # A-06
    def test_forbidden_characters(self):
        self.assertEqual(naming.safe_name('A/B:C*?"<>|D\\E'), "A B C D E")

    def test_trailing_dots_and_spaces(self):
        self.assertEqual(naming.safe_name("  Site A. . "), "Site A")

    def test_reserved_name(self):
        self.assertEqual(naming.safe_name("CON"), "_CON")
        self.assertEqual(naming.safe_name("com1.txt"), "_com1.txt")

    def test_accents_and_dash_are_kept(self):
        name = "R\u1eebng Tr\u00e0m \u2013 \u0110\u1ee3t 1"
        self.assertEqual(naming.safe_name(name), name)

    def test_empty(self):
        self.assertEqual(naming.safe_name(' /:* '), "Untitled")

    def test_long_path(self):
        self.assertTrue(naming.path_too_long("C:\\" + "a" * 240))
        self.assertFalse(naming.path_too_long("C:\\short"))


if __name__ == "__main__":
    unittest.main()
