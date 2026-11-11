"""Test plan A-09 (spec 6.8, SAVE-6)."""
import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from treeprof_app import filecopy as fc

CHUNK = 1024


class FlakySource:
    """A source file that fails like a network drive that dropped, after `good_bytes` bytes."""

    def __init__(self, path, good_bytes):
        self.f = open(path, "rb")
        self.left = good_bytes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.f.close()

    def seek(self, n):
        self.f.seek(n)

    def read(self, n):
        if self.left <= 0:
            raise OSError("The specified network name is no longer available")
        block = self.f.read(n)
        self.left -= len(block)
        return block


class Copy(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.urandom(CHUNK * 10 + 137)
        self.src = self.tmp / "net" / "cloud.laz"
        self.src.parent.mkdir()
        self.src.write_bytes(self.data)
        self.dst = self.tmp / "local" / "input" / "cloud.laz"
        self.waits = []

    def copy(self, **kw):
        kw.setdefault("chunk_size", CHUNK)
        kw.setdefault("sleep", self.waits.append)
        return fc.copy_with_hash(self.src, self.dst, **kw)

    def leftovers(self):
        return sorted(p.name for p in self.dst.parent.iterdir()) if self.dst.parent.exists() else []

    def test_copy_and_hash(self):
        seen = []
        r = self.copy(progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(self.dst.read_bytes(), self.data)
        self.assertEqual((r.bytes, r.sha256, r.attempts), (len(self.data), hashlib.sha256(self.data).hexdigest(), 1))
        self.assertEqual(seen[-1], (len(self.data), len(self.data)))
        self.assertEqual(seen, sorted(seen))
        self.assertEqual(self.leftovers(), ["cloud.laz"])

    def test_empty_file(self):
        self.src.write_bytes(b"")
        r = self.copy()
        self.assertEqual((r.bytes, self.dst.read_bytes()), (0, b""))

    def test_existing_destination_is_replaced(self):
        self.dst.parent.mkdir(parents=True)
        self.dst.write_bytes(b"old content")
        self.copy()
        self.assertEqual(self.dst.read_bytes(), self.data)

    def test_connection_drops_then_copy_goes_on(self):
        calls = []

        def opener(path):
            calls.append(path)
            return FlakySource(path, CHUNK * 3) if len(calls) == 1 else open(path, "rb")

        r = self.copy(open_source=opener, wait_seconds=10)
        self.assertEqual(self.dst.read_bytes(), self.data)                       # identical, nothing doubled
        self.assertEqual(r.sha256, hashlib.sha256(self.data).hexdigest())
        self.assertEqual((r.attempts, self.waits), (2, [10]))
        self.assertEqual(self.leftovers(), ["cloud.laz"])

    def test_gives_up_after_the_retries_and_leaves_nothing(self):
        with self.assertRaises(fc.CopyError) as cm:
            self.copy(open_source=lambda p: FlakySource(p, CHUNK * 2), retries=3, wait_seconds=10)
        self.assertIn("4 tries", str(cm.exception))
        self.assertEqual(self.waits, [10, 10, 10])
        self.assertEqual(self.leftovers(), [])

    def test_cancel_leaves_nothing(self):
        state = {"n": 0}

        def cancel():
            state["n"] += 1
            return state["n"] > 3

        with self.assertRaises(fc.CopyCancelled):
            self.copy(cancel=cancel)
        self.assertEqual(self.leftovers(), [])

    def test_missing_source(self):
        self.src.unlink()
        with self.assertRaises(fc.CopyError):
            self.copy()

    def test_source_changed_during_the_copy(self):
        calls = []

        def opener(path):
            calls.append(path)
            if len(calls) == 1:
                return FlakySource(path, CHUNK)
            return open(path, "rb")

        def grow(_seconds):
            with open(self.src, "ab") as f:
                f.write(b"more")

        with self.assertRaises(fc.CopyError) as cm:
            self.copy(open_source=opener, sleep=grow)
        self.assertIn("changed", str(cm.exception))
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
