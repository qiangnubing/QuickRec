from __future__ import annotations

import os
import sys
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.countdown import (
    CountdownOverlay,
    compute_bottom_right_position,
)
from short_video_capture.win32 import ScreenRect, enable_dpi_awareness, window_rect


class CountdownFormatTests(unittest.TestCase):
    def test_formats_seconds_as_minutes_and_seconds(self) -> None:
        self.assertEqual(CountdownOverlay._format(40), "00:40")
        self.assertEqual(CountdownOverlay._format(10), "00:10")
        self.assertEqual(CountdownOverlay._format(9.01), "00:10")
        self.assertEqual(CountdownOverlay._format(0), "00:00")
        self.assertEqual(CountdownOverlay._format(61), "01:01")
        self.assertEqual(CountdownOverlay._format(3600), "01:00:00")
        self.assertEqual(CountdownOverlay._format(-1), "00:00")
        self.assertEqual(CountdownOverlay._format_elapsed(61), "01:01")
        self.assertEqual(CountdownOverlay._format_elapsed(-1), "00:00")

    def test_compute_bottom_right_position_across_monitors(self) -> None:
        primary = ScreenRect(0, 0, 1920, 1032)
        self.assertEqual(
            compute_bottom_right_position(primary, 260, 56, margin=24),
            (1636, 952),
        )
        above = ScreenRect(7, -1080, 1920, 1032)
        self.assertEqual(
            compute_bottom_right_position(above, 260, 56, margin=24),
            (1643, -128),
        )
        left = ScreenRect(-1920, 0, 1920, 1032)
        self.assertEqual(
            compute_bottom_right_position(left, 260, 56, margin=24),
            (-284, 952),
        )
        right = ScreenRect(1920, 0, 1920, 1032)
        self.assertEqual(
            compute_bottom_right_position(right, 260, 56, margin=24),
            (3556, 952),
        )

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_overlay_places_on_target_secondary_monitor(self) -> None:
        enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        try:
            target = ScreenRect(7, -1080, 1920, 1032)
            overlay = CountdownOverlay(root, 15, work_area=target)
            try:
                overlay.show()
                root.update()
                rect = window_rect(overlay.window)
                self.assertIsNotNone(rect)
                assert rect is not None
                self.assertGreaterEqual(rect.left, target.left)
                self.assertLessEqual(rect.right, target.right)
                self.assertGreaterEqual(rect.top, target.top)
                self.assertLessEqual(rect.bottom, target.bottom)
                self.assertEqual(rect.right, target.right - 24)
                self.assertEqual(rect.bottom, target.bottom - 24)
            finally:
                overlay.close()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
