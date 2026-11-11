"""The parts that talk to Windows: log zip, setup pieces, starting the app
(SYS-6, OPEN-1, spec 10). What needs a real Windows desktop is checked by hand (test plan M-01 to M-05)."""
import io
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest import mock

from treeprof_app import __main__ as entry
from treeprof_app import checks, desktop


class Setup(unittest.TestCase):
    def test_icon_is_a_real_ico_holding_a_png(self):
        ico = desktop.icon_bytes(64)
        reserved, kind, count = struct.unpack_from("<HHH", ico, 0)
        width, height, _, _, planes, bits, size, offset = struct.unpack_from("<BBBBHHII", ico, 6)
        self.assertEqual((reserved, kind, count, width, height, bits), (0, 1, 1, 64, 64, 32))
        png = ico[offset:offset + size]
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(len(ico), offset + size)
        pos, chunks = 8, {}
        while pos < len(png):                                         # every chunk has a correct checksum
            length, name = struct.unpack_from(">I4s", png, pos)
            data = png[pos + 8:pos + 8 + length]
            self.assertEqual(struct.unpack_from(">I", png, pos + 8 + length)[0], zlib.crc32(name + data) & 0xFFFFFFFF)
            chunks[name] = data
            pos += 12 + length
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (64, 64, 8, 6, 0, 0, 0))
        self.assertEqual(len(zlib.decompress(chunks[b"IDAT"])), 64 * (1 + 64 * 4))

    def test_shortcut_script_points_at_the_apps_own_python(self):
        app = Path("C:/Tree Profiling/it's here/app")
        script = desktop.shortcut_script(app)
        pythonw, launcher = desktop.launcher_command(app)
        self.assertIn("'Tree Profiling.lnk'", script)
        self.assertIn(str(pythonw).replace("'", "''"), script)
        self.assertIn('"' + str(launcher).replace("'", "''") + '"', script)
        self.assertNotIn("it's", script)                              # a quote in a path is doubled for PowerShell
        self.assertEqual(script.count("GetFolderPath"), 2)            # Desktop and Start menu

    def test_install_reports_each_part_and_goes_on(self):
        said = []
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(desktop, "make_shortcuts", side_effect=OSError("PowerShell is blocked")), \
                mock.patch.object(desktop, "register_file_type", return_value="cmd"):
            self.assertFalse(desktop.install(tmp, out=said.append, windows=True))
        self.assertTrue((tmp / "treeprof.ico").is_file())
        self.assertTrue(said[0].startswith("OK    icon"))
        self.assertIn("FAILED shortcuts", said[1])
        self.assertIn("PowerShell is blocked", said[1])
        self.assertTrue(said[2].startswith("OK    .treeprof"))

    def test_install_warns_inside_onedrive(self):
        said = []
        self.assertTrue(desktop.install(Path("C:/Users/x/OneDrive/app"), out=said.append, windows=False))
        self.assertIn("OneDrive", said[0])

    @unittest.skipUnless(os.name == "nt", "the registry only exists on Windows")
    def test_file_type_is_registered_for_this_account(self):           # runs on the Windows test machine
        import winreg
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(desktop, "FILE_TYPE", "TreeProfiling.Test"):
            command = desktop.register_file_type(tmp)
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\TreeProfiling.Test\shell\open\command") as k:
                    self.assertEqual(winreg.QueryValueEx(k, "")[0], command)
                self.assertTrue(command.endswith('launch.pyw" "%1"'))
            finally:
                for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\TreeProfiling.Test" + sub)
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.treeprof") as k:
                    winreg.SetValueEx(k, "", 0, winreg.REG_SZ, desktop.FILE_TYPE)


class LogZip(unittest.TestCase):
    def test_zip_holds_logs_and_project_file_but_no_results(self):     # SYS-6
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        work = tmp / "runs" / "r1"
        (work / "log").mkdir(parents=True)
        (work / "log" / "find_trees.log").write_text("docker said no")
        (work / "Test Site.treeprof").write_text("{}")
        (work / "sat_result_merged_trees.csv").write_text("tree_uid\n")
        (work / "sat_result.laz").write_bytes(b"points")
        (tmp / "state").mkdir()
        (tmp / "state" / "settings.json").write_text("{}")
        run = {"id": "r1", "site": "Test Site", "workdir": str(work), "status": "failed", "error_kind": "docker_down"}
        out = desktop.make_log_zip(tmp, run=run, machine=[checks.Check("docker", checks.ERROR, "Docker is not running")],
                                   out_dir=tmp)
        with zipfile.ZipFile(out) as z:
            names = sorted(z.namelist())
            text = z.read("summary.txt").decode()
        self.assertEqual(names, ["Test Site.treeprof", "log/find_trees.log", "settings.json", "summary.txt"])
        self.assertIn("check docker: error: Docker is not running", text)
        self.assertIn("run error_kind: docker_down", text)


class EntryPoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_log_goes_to_a_file_when_there_is_no_console(self):         # pythonw.exe
        old = sys.stdout, sys.stderr
        try:
            path = entry.log_to_file(self.tmp)
            print("hello from the app")
            sys.stderr.write("and a problem\n")
            sys.stdout.close()
        finally:
            sys.stdout, sys.stderr = old
        text = path.read_text(encoding="utf-8")
        self.assertIn("Tree Profiling", text)
        self.assertIn("hello from the app", text)
        self.assertIn("and a problem", text)

    def test_a_big_log_is_put_aside(self):                              # NFR-6
        state = self.tmp / "state"
        state.mkdir()
        (state / "app.log").write_bytes(b"x" * (entry.LOG_MAX_BYTES + 1))
        old = sys.stdout, sys.stderr
        try:
            entry.log_to_file(self.tmp)
            sys.stdout.close()
        finally:
            sys.stdout, sys.stderr = old
        self.assertEqual((state / "app.log.1").stat().st_size, entry.LOG_MAX_BYTES + 1)
        self.assertLess((state / "app.log").stat().st_size, 1000)

    def test_backup_copies_the_records_and_keeps_the_last_few(self):
        state = self.tmp / "state"
        (state / "viewcache").mkdir(parents=True)
        (state / "runs.sqlite").write_bytes(b"db")
        (state / "settings.json").write_text("{}")
        (state / "app.log").write_text("log")
        (state / "viewcache" / "a.npz").write_bytes(b"big")
        for i in range(7):
            (self.tmp / "state_backups" / f"2020010{i}_000000").mkdir(parents=True)
        target = entry.backup_state(self.tmp)
        self.assertEqual(sorted(p.name for p in target.iterdir()), ["runs.sqlite", "settings.json"])
        kept = sorted(p.name for p in (self.tmp / "state_backups").iterdir())
        self.assertEqual(len(kept), entry.BACKUPS_KEPT)
        self.assertEqual(kept[-1], target.name)

    def test_check_closed(self):
        out = io.StringIO()
        with mock.patch.object(entry, "app_is_open", return_value=False), mock.patch.object(sys, "stdout", out):
            self.assertEqual(entry.main(["--check-closed", "--home", str(self.tmp)]), 0)
        with mock.patch.object(entry, "app_is_open", return_value=True), mock.patch.object(sys, "stdout", out):
            self.assertEqual(entry.main(["--check-closed", "--home", str(self.tmp)]), 1)
        self.assertIn("Tree Profiling is open", out.getvalue())

    def test_icon_png_is_what_the_ico_holds(self):
        self.assertTrue(desktop.icon_bytes(32).endswith(desktop.icon_png(32)))


if __name__ == "__main__":
    unittest.main()
