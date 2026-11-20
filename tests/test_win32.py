from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.win32 import (
    ScreenRect,
    center_position,
    display_monitors,
    monitor_work_area_for_point,
    monitor_work_area_for_rect,
    primary_work_area,
    tk_geometry,
    virtual_screen_rect,
)


class ScreenRectTests(unittest.TestCase):
    def test_right_and_bottom(self) -> None:
        rect = ScreenRect(left=-1920, top=15, width=1920, height=1050)
        self.assertEqual(rect.right, 0)
        self.assertEqual(rect.bottom, 1065)

    def test_tk_geometry_uses_plus_prefix_for_negative_coordinates(self) -> None:
        self.assertEqual(tk_geometry(400, 300, 100, 200), "400x300+100+200")
        self.assertEqual(tk_geometry(1927, 2160, 0, -1080), "1927x2160+0+-1080")
        self.assertEqual(tk_geometry(3840, 1080, -1920, 0), "3840x1080+-1920+0")
        self.assertEqual(tk_geometry(300, 200, -1200, -600), "300x200+-1200+-600")

    def test_center_position_follows_parent_on_primary_and_secondary_monitors(self) -> None:
        # 主屏左上角的主窗口（偏离屏幕中央）
        parent = ScreenRect(left=80, top=60, width=680, height=560)
        work = ScreenRect(left=0, top=0, width=1920, height=1040)
        self.assertEqual(center_position(parent, 400, 240, work), (220, 220))

        # 位于主屏上方的扩展屏（y 为负数）
        top_work = ScreenRect(left=7, top=-1080, width=1920, height=1032)
        top_parent = ScreenRect(left=120, top=-960, width=680, height=560)
        self.assertEqual(center_position(top_parent, 400, 240, top_work), (260, -800))

        # 位于主屏左侧的扩展屏（x 为负数）
        left_work = ScreenRect(left=-1920, top=0, width=1920, height=1040)
        left_parent = ScreenRect(left=-1600, top=100, width=680, height=560)
        self.assertEqual(center_position(left_parent, 400, 240, left_work), (-1460, 260))

    def test_center_position_clamps_inside_work_area(self) -> None:
        work = ScreenRect(left=-1920, top=0, width=1920, height=1040)
        parent = ScreenRect(left=-1910, top=900, width=200, height=200)
        x, y = center_position(parent, 400, 300, work)
        self.assertEqual(x, -1920)
        self.assertEqual(y, 740)


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

    def test_display_monitors_and_monitor_work_area(self) -> None:
        virtual = virtual_screen_rect()
        monitors = display_monitors()
        self.assertGreaterEqual(len(monitors), 1)
        for mon in monitors:
            self.assertGreater(mon.width, 0)
            self.assertGreater(mon.height, 0)
            cx = mon.left + mon.width // 2
            cy = mon.top + mon.height // 2
            work = monitor_work_area_for_point(cx, cy)
            self.assertGreaterEqual(work.left, mon.left)
            self.assertGreaterEqual(work.top, mon.top)
            self.assertLessEqual(work.right, mon.right)
            self.assertLessEqual(work.bottom, mon.bottom)
            work_rect = monitor_work_area_for_rect(
                ScreenRect(cx - 50, cy - 50, 100, 100)
            )
            self.assertEqual(work_rect, work)
            self.assertGreaterEqual(mon.left, virtual.left)
            self.assertGreaterEqual(mon.top, virtual.top)


if __name__ == "__main__":
    unittest.main()
