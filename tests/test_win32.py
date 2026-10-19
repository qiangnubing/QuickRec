from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.win32 import ScreenRect, primary_work_area, virtual_screen_rect


class ScreenRectTests(unittest.TestCase):
    def test_right_and_bottom(self) -> None:
        rect = ScreenRect(left=-1920, top=15, width=1920, height=1050)
        self.assertEqual(rect.right, 0)
        self.assertEqual(rect.bottom, 1065)


@unittest.skipUnless(os.name == "nt", "仅 Windows")
class Win32ScreenTests(unittest.TestCase):
    def test_virtual_screen_has_positive_size(self) -> None:
        rect = virtual_screen_rect()
        self.assertGreater(rect.width, 0)
        self.assertGreater(rect.height, 0)

    def test_work_area_is_inside_virtual_screen(self) -> None:
        virtual = virtual_screen_rect()
        work = primary_work_area()
        self.assertGreaterEqual(work.left, virtual.left)
        self.assertGreaterEqual(work.top, virtual.top)
        self.assertLessEqual(work.right, virtual.right)
        self.assertLessEqual(work.bottom, virtual.bottom)


if __name__ == "__main__":
    unittest.main()
