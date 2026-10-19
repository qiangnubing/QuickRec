from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.directory_picker import (
    available_drives,
    list_subdirectories,
)


class DirectoryPickerTests(unittest.TestCase):
    def test_lists_only_subdirectories_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Bravo").mkdir()
            (root / "alpha").mkdir()
            (root / "file.txt").write_text("x", encoding="utf-8")
            names = [path.name for path in list_subdirectories(root)]
            self.assertEqual(names, ["alpha", "Bravo"])

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_available_drives(self) -> None:
        drives = available_drives()
        self.assertTrue(drives)
        self.assertTrue(all(drive.endswith(":\\") for drive in drives))


if __name__ == "__main__":
    unittest.main()
