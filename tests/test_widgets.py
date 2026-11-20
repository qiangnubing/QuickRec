"""自定义圆角控件渲染与交互单元测试。"""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from short_video_capture.app import ICON_PNG
from short_video_capture.widgets import (
    load_scaled_icon_png,
    render_checkbox_png,
    render_dot_png,
    render_rounded_rect_png,
)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


class WidgetsRenderTests(unittest.TestCase):
    def test_render_rounded_rect_png_dimensions_and_cache(self) -> None:
        png1 = render_rounded_rect_png(
            120,
            40,
            8.0,
            "#F55342",
            "#FFFFFF",
            shadow_color="#F55342",
            shadow_alpha=0.22,
            shadow_blur=4.0,
            shadow_offset_y=2.0,
            pad_x=4,
            pad_top=1,
            pad_bottom=5,
        )
        png2 = render_rounded_rect_png(
            120,
            40,
            8.0,
            "#F55342",
            "#FFFFFF",
            shadow_color="#F55342",
            shadow_alpha=0.22,
            shadow_blur=4.0,
            shadow_offset_y=2.0,
            pad_x=4,
            pad_top=1,
            pad_bottom=5,
        )
        self.assertEqual(_png_dimensions(png1), (120, 40))
        self.assertIs(png1, png2)

    def test_render_rounded_rect_with_border(self) -> None:
        png = render_rounded_rect_png(
            200,
            34,
            6.0,
            "#F8FAFC",
            "#FFFFFF",
            border="#D8E0EA",
            border_width=1.0,
        )
        self.assertEqual(_png_dimensions(png), (200, 34))

    def test_render_rounded_rect_transparent_key_mode(self) -> None:
        png = render_rounded_rect_png(
            260,
            48,
            12.0,
            "#0F172A",
            "#010203",
            border="#F55342",
            border_width=1.2,
            transparent_key=True,
        )
        self.assertEqual(_png_dimensions(png), (260, 48))

    def test_render_dot_png(self) -> None:
        on_png = render_dot_png(
            16, "#F55342", "#0F172A", dot_radius=4.0, glow_alpha=0.38, glow_blur=3.0
        )
        off_png = render_dot_png(16, "#7F1D1D", "#0F172A", dot_radius=4.0)
        self.assertEqual(_png_dimensions(on_png), (16, 16))
        self.assertEqual(_png_dimensions(off_png), (16, 16))
        self.assertNotEqual(on_png, off_png)

    def test_render_checkbox_png_states(self) -> None:
        for checked in (False, True):
            for disabled in (False, True):
                for hovered in (False, True):
                    png = render_checkbox_png(
                        16,
                        checked,
                        disabled,
                        hovered,
                        4.0,
                        "#FFFFFF",
                    )
                    self.assertEqual(_png_dimensions(png), (16, 16))

    def test_load_scaled_icon_png(self) -> None:
        self.assertTrue(ICON_PNG.exists())
        scaled = load_scaled_icon_png(ICON_PNG, 48)
        self.assertIsNotNone(scaled)
        assert scaled is not None
        self.assertEqual(_png_dimensions(scaled), (48, 48))

    def test_load_scaled_icon_png_missing_file(self) -> None:
        self.assertIsNone(load_scaled_icon_png(Path("nonexistent_icon.png"), 48))


if __name__ == "__main__":
    unittest.main()
