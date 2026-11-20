from __future__ import annotations

import os
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.directory_picker import (
    _PICKER_HEIGHT,
    _PICKER_WIDTH,
    DirectoryPicker,
    available_drives,
    list_subdirectories,
)
from short_video_capture.widgets import (
    RoundedButton,
    RoundedCard,
    RoundedDropdown,
    RoundedEntry,
    RoundedScrollbar,
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

    @unittest.skipUnless(os.name == "nt", "仅 Windows")
    def test_picker_uses_compact_size_and_rounded_widgets(self) -> None:
        self.assertLess(_PICKER_WIDTH, 660)
        self.assertLess(_PICKER_HEIGHT, 500)
        with tempfile.TemporaryDirectory() as temp:
            root_dir = Path(temp)
            (root_dir / "SubA").mkdir()
            (root_dir / "SubB").mkdir()
            root = tk.Tk()
            root.withdraw()
            try:
                picker = DirectoryPicker(root, root_dir)
                try:
                    picker.window.update()
                    self.assertIsInstance(picker.drive_dropdown, RoundedDropdown)
                    self.assertIsInstance(picker.scrollbar, RoundedScrollbar)
                    self.assertIsInstance(picker._list_card, RoundedCard)
                    self.assertIsInstance(picker.path_entry.master, RoundedEntry)
                    for btn in (
                        picker._go_button,
                        picker._up_button,
                        picker._refresh_button,
                        picker._new_folder_button,
                        picker._select_button,
                        picker._cancel_button,
                    ):
                        self.assertIsInstance(btn, RoundedButton)

                    # 验证出错提示显示时底部按钮栏依然完整位于窗口内
                    picker._set_error("测试错误提示")
                    picker.window.update()
                    self.assertTrue(picker._error_visible)
                    actions_bottom = (
                        picker._actions.winfo_y() + picker._actions.winfo_height()
                    )
                    self.assertLessEqual(actions_bottom, picker.window.winfo_height())

                    picker._select_current()
                    self.assertEqual(picker._result, root_dir.resolve())
                finally:
                    picker._close()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
