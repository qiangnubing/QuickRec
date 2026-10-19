"""屏幕右下角录制倒计时浮层。"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

from .win32 import (
    exclude_from_capture,
    make_click_through,
    primary_work_area,
)

_BG = "#0F172A"
_FG = "#F8FAFC"
_MUTED = "#CBD5E1"
_ACCENT = "#F43F5E"


class CountdownOverlay:
    """置顶、无边框、点击穿透的倒计时，录制时显示屏幕右下角。"""

    def __init__(self, root: tk.Misc, total_seconds: int) -> None:
        self._root = root
        self._total_seconds = total_seconds
        self._blink_job: str | None = None
        self._dot_visible = True
        self._closed = False

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.96)
        self.window.configure(bg=_BG)

        card = tk.Frame(
            self.window,
            bg=_BG,
            highlightbackground=_ACCENT,
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True)

        content = tk.Frame(card, bg=_BG)
        content.pack(padx=14, pady=9)

        self._dot = tk.Canvas(
            content,
            width=12,
            height=12,
            bg=_BG,
            highlightthickness=0,
            bd=0,
        )
        self._dot.pack(side="left", padx=(0, 8))
        self._dot_id = self._dot.create_oval(2, 2, 10, 10, fill=_ACCENT, outline="")

        tk.Label(
            content,
            text="录制中",
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 12))

        self._timer_font = tkfont.Font(family="Consolas", size=17, weight="bold")
        self._timer = tk.Label(
            content,
            text=self._format(total_seconds),
            bg=_BG,
            fg=_FG,
            font=self._timer_font,
        )
        self._timer.pack(side="left")

    def show(self) -> None:
        self.window.deiconify()
        self.window.update_idletasks()
        self._place_bottom_right()
        self.window.lift()
        self.window.update_idletasks()
        exclude_from_capture(self.window)
        make_click_through(self.window)
        self._schedule_blink()

    def _place_bottom_right(self) -> None:
        area = primary_work_area()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        margin = 24
        x = area.right - width - margin
        y = area.bottom - height - margin
        self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def set_remaining(self, remaining_seconds: float) -> None:
        if self._closed:
            return
        try:
            self._timer.configure(text=self._format(remaining_seconds))
        except tk.TclError:
            self._closed = True

    @staticmethod
    def _format(remaining_seconds: float) -> str:
        whole = max(0, math.ceil(remaining_seconds))
        minutes, seconds = divmod(whole, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _schedule_blink(self) -> None:
        if self._closed:
            return
        self._blink_job = self.window.after(500, self._blink)

    def _blink(self) -> None:
        if self._closed:
            return
        self._dot_visible = not self._dot_visible
        try:
            self._dot.itemconfigure(
                self._dot_id, fill=_ACCENT if self._dot_visible else "#7F1D1D"
            )
        except tk.TclError:
            self._closed = True
            return
        self._schedule_blink()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._blink_job is not None:
            try:
                self.window.after_cancel(self._blink_job)
            except tk.TclError:
                pass
            self._blink_job = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
