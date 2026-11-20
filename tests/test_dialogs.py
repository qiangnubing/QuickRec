from __future__ import annotations

import os
import sys
import tkinter as tk
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.dialogs import (
    _InputDialog,
    _ModalDialog,
    center_window_over_parent,
)
from short_video_capture.win32 import (
    display_monitors,
    enable_dpi_awareness,
    tk_geometry,
    window_rect,
)


@unittest.skipUnless(os.name == "nt", "仅 Windows")
class DialogCenteringTests(unittest.TestCase):
    def test_dialog_centers_over_moved_main_window_on_each_monitor(self) -> None:
        enable_dpi_awareness()
        monitors = display_monitors()
        root = tk.Tk()
        try:
            for monitor in monitors:
                # 刻意把主窗口放在远离屏幕中央的左上区域
                main_x = monitor.left + 80
                main_y = monitor.top + 60
                root.geometry(tk_geometry(668, 520, main_x, main_y))
                root.deiconify()
                root.update()

                parent_rect = window_rect(root)
                self.assertIsNotNone(parent_rect)
                assert parent_rect is not None

                dlg = tk.Toplevel(root)
                dlg.withdraw()
                dlg.transient(root)
                tk.Label(dlg, text="Recording complete", width=40, height=8).pack(
                    padx=20, pady=20
                )
                try:
                    center_window_over_parent(dlg, root)
                    dlg.deiconify()
                    dlg.update()
                    dlg_rect = window_rect(dlg)
                    self.assertIsNotNone(dlg_rect)
                    assert dlg_rect is not None

                    parent_cx = parent_rect.left + parent_rect.width / 2
                    parent_cy = parent_rect.top + parent_rect.height / 2
                    dlg_cx = dlg_rect.left + dlg_rect.width / 2
                    dlg_cy = dlg_rect.top + dlg_rect.height / 2

                    self.assertLessEqual(abs(parent_cx - dlg_cx), 1.0)
                    self.assertLessEqual(abs(parent_cy - dlg_cy), 1.0)
                finally:
                    dlg.destroy()
        finally:
            root.destroy()

    def test_modal_completion_dialog_centers_over_main_window_after_withdraw_restore(
        self,
    ) -> None:
        enable_dpi_awareness()
        monitors = display_monitors()
        root = tk.Tk()
        try:
            for monitor in monitors:
                main_x = monitor.left + 90
                main_y = monitor.top + 70
                root.geometry(tk_geometry(668, 520, main_x, main_y))
                root.deiconify()
                root.update()

                # 模拟录制期间隐藏主窗口，结束后恢复主窗口
                root.withdraw()
                root.update()
                root.deiconify()
                root.state("normal")
                root.update_idletasks()
                root.lift()
                root.attributes("-topmost", True)
                root.update_idletasks()
                root.attributes("-topmost", False)

                parent_rect = window_rect(root)
                self.assertIsNotNone(parent_rect)
                assert parent_rect is not None

                dialog = _ModalDialog(
                    root,
                    "录制完成",
                    "录制完成\n\n文件：demo.mp4\n时长：10 秒\n是否打开所在文件夹？",
                    kind="success",
                )
                try:
                    root.update()
                    dlg_rect = window_rect(dialog.window)
                    self.assertIsNotNone(dlg_rect)
                    assert dlg_rect is not None

                    parent_cx = parent_rect.left + parent_rect.width / 2
                    parent_cy = parent_rect.top + parent_rect.height / 2
                    dlg_cx = dlg_rect.left + dlg_rect.width / 2
                    dlg_cy = dlg_rect.top + dlg_rect.height / 2

                    self.assertLessEqual(abs(parent_cx - dlg_cx), 1.0)
                    self.assertLessEqual(abs(parent_cy - dlg_cy), 1.0)
                    dialog._on_confirm()
                    self.assertTrue(dialog._result)
                finally:
                    dialog._close()
        finally:
            root.destroy()

    def test_input_dialog_confirms_and_cancels(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            dlg = _InputDialog(root, "新建文件夹", "文件夹名称：", initial_value="demo")
            try:
                dlg.value_var.set("videos")
                dlg._on_confirm()
                self.assertEqual(dlg._result, "videos")
            finally:
                dlg._close()

            cancelled = _InputDialog(root, "新建文件夹", "文件夹名称：")
            try:
                cancelled.value_var.set("ignored")
                cancelled._on_cancel()
                self.assertIsNone(cancelled._result)
            finally:
                cancelled._close()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
