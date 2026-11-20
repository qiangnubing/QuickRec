from __future__ import annotations

import os
import sys
import tkinter as tk
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.capture import CaptureRegion
from short_video_capture.region_selector import (
    RegionSelector,
    compute_action_bar_position,
)
from short_video_capture.win32 import (
    ScreenRect,
    display_monitors,
    enable_dpi_awareness,
    monitor_work_area_for_rect,
    virtual_screen_rect,
    window_rect,
)


class ActionBarPositionTests(unittest.TestCase):
    def test_positions_bar_on_top_extended_monitor(self) -> None:
        area = ScreenRect(7, -1080, 1920, 1032)  # bottom = -48
        selection = ScreenRect(300, -800, 600, 400)  # bottom = -400
        x, y = compute_action_bar_position(selection, 360, 52, area)
        self.assertEqual(x, 420)
        self.assertEqual(y, -388)
        self.assertGreaterEqual(x, area.left)
        self.assertLessEqual(x + 360, area.right)
        self.assertGreaterEqual(y, area.top)
        self.assertLessEqual(y + 52, area.bottom)

    def test_positions_bar_on_left_extended_monitor(self) -> None:
        area = ScreenRect(-1920, 0, 1920, 1032)
        selection = ScreenRect(-1500, 200, 600, 400)
        x, y = compute_action_bar_position(selection, 360, 52, area)
        self.assertEqual(x, -1380)
        self.assertEqual(y, 612)

    def test_positions_bar_on_right_extended_monitor(self) -> None:
        area = ScreenRect(1920, 0, 1920, 1032)
        selection = ScreenRect(2200, 200, 600, 400)
        x, y = compute_action_bar_position(selection, 360, 52, area)
        self.assertEqual(x, 2320)
        self.assertEqual(y, 612)

    def test_flips_above_when_near_bottom_edge(self) -> None:
        area = ScreenRect(7, -1080, 1920, 1032)  # bottom = -48
        selection = ScreenRect(400, -300, 500, 240)  # bottom = -60
        x, y = compute_action_bar_position(selection, 360, 52, area)
        self.assertEqual(x, 470)
        self.assertEqual(y, -364)


@unittest.skipUnless(os.name == "nt", "仅 Windows")
class RegionSelectorMultiMonitorTests(unittest.TestCase):
    def test_selector_spans_virtual_screen_and_maps_each_monitor_accurately(self) -> None:
        enable_dpi_awareness()
        virtual = virtual_screen_rect()
        monitors = display_monitors()
        root = tk.Tk()
        root.withdraw()
        try:
            for monitor in monitors:
                confirmed: list[CaptureRegion] = []
                selector = RegionSelector(
                    root,
                    on_confirm=confirmed.append,
                    on_cancel=lambda: None,
                )
                try:
                    root.update()
                    overlay_rect = window_rect(selector.window)
                    self.assertEqual(overlay_rect, virtual)

                    # 在该显示器的客户区中央拖拽一个 300x200 的选区
                    target_left = monitor.left + 120
                    target_top = monitor.top + 120
                    canvas_x1 = target_left - virtual.left
                    canvas_y1 = target_top - virtual.top
                    canvas_x2 = canvas_x1 + 300
                    canvas_y2 = canvas_y1 + 200

                    selector._on_press(types.SimpleNamespace(x=canvas_x1, y=canvas_y1))
                    selector._on_drag(types.SimpleNamespace(x=canvas_x2, y=canvas_y2))
                    selector._on_release(types.SimpleNamespace(x=canvas_x2, y=canvas_y2))
                    root.update()

                    self.assertIsNotNone(selector._bar)
                    bar_rect = window_rect(selector._bar)
                    self.assertIsNotNone(bar_rect)
                    assert bar_rect is not None
                    mon_work = monitor_work_area_for_rect(
                        ScreenRect(target_left, target_top, 300, 200)
                    )
                    self.assertGreaterEqual(bar_rect.left, mon_work.left)
                    self.assertLessEqual(bar_rect.right, mon_work.right)
                    self.assertGreaterEqual(bar_rect.top, mon_work.top)
                    self.assertLessEqual(bar_rect.bottom, mon_work.bottom)

                    selector._confirm()
                    self.assertEqual(
                        confirmed,
                        [CaptureRegion(target_left, target_top, 300, 200)],
                    )
                finally:
                    selector._close()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
