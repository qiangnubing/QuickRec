"""屏幕右下角录制倒计时浮层。"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

from .i18n import tr
from .win32 import (
    exclude_from_capture,
    make_overlay_window,
    primary_work_area,
)

_BG = "#0F172A"
_FG = "#F8FAFC"
_MUTED = "#CBD5E1"
_ACCENT = "#F43F5E"


class CountdownOverlay:
    """置顶、无边框、点击穿透的倒计时，录制时显示屏幕右下角。"""

    def __init__(
        self,
        root: tk.Misc,
        total_seconds: int | None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        self._root = root
        self._total_seconds = total_seconds
        self._max_mode = total_seconds is None
        self._on_stop = on_stop
        self._blink_job: str | None = None
        self._dot_visible = True
        self._stopping = False
        self._closed = False
        self._timer_width = 10 if self._max_mode else 8

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
        content.pack(padx=10, pady=8)

        self._dot = tk.Canvas(
            content,
            width=12,
            height=12,
            bg=_BG,
            highlightthickness=0,
            bd=0,
        )
        self._dot.pack(side="left", padx=(0, 6))
        self._dot_id = self._dot.create_oval(2, 2, 10, 10, fill=_ACCENT, outline="")

        tk.Label(
            content,
            text=tr("录制中", "REC"),
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 8))

        self._timer_font = tkfont.Font(family="Consolas", size=17, weight="bold")
        self._timer = tk.Label(
            content,
            text=(
                self._format_max(0)
                if self._max_mode
                else self._format(total_seconds)
            ),
            bg=_BG,
            fg=_FG,
            font=self._timer_font,
            width=self._timer_width,
            anchor="center",
        )
        self._timer.pack(side="left")

        self._stop_button = tk.Button(
            content,
            text=tr("停止", "Stop"),
            command=self._request_stop,
            bg="#DC2626",
            fg="#FFFFFF",
            activebackground="#B91C1C",
            activeforeground="#FFFFFF",
            disabledforeground="#FEE2E2",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=8,
            pady=2,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self._stop_button.pack(side="left", padx=(10, 0))

    def show(self) -> None:
        self.window.deiconify()
        self.window.update_idletasks()
        self._place_bottom_right()
        self.window.lift()
        self.window.update_idletasks()
        exclude_from_capture(self.window)
        make_overlay_window(self.window, click_through=False)
        self._schedule_blink()

    def _place_bottom_right(self) -> None:
        area = primary_work_area()
        width = self.window.winfo_reqwidth()
        height = self.window.winfo_reqheight()
        margin = 24
        x = area.right - width - margin
        y = area.bottom - height - margin
        self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _set_timer_text(self, text: str) -> None:
        # MAX 超过一小时后文本会变长，按需加宽并重新贴边，避免被截断。
        needed = max(self._timer_width, len(text))
        if needed != self._timer_width:
            self._timer_width = needed
            self._timer.configure(width=needed)
            self.window.update_idletasks()
            self._place_bottom_right()
        self._timer.configure(text=text)

    def set_remaining(self, remaining_seconds: float) -> None:
        if self._closed:
            return
        try:
            self._set_timer_text(self._format(remaining_seconds))
        except tk.TclError:
            self._closed = True

    def set_elapsed(self, elapsed_seconds: float) -> None:
        """MAX 模式显示已录制时长。"""
        if self._closed:
            return
        try:
            self._set_timer_text(self._format_max(elapsed_seconds))
        except tk.TclError:
            self._closed = True

    @staticmethod
    def _format(remaining_seconds: float) -> str:
        return CountdownOverlay._clock(math.ceil(remaining_seconds))

    @staticmethod
    def _format_elapsed(elapsed_seconds: float) -> str:
        return CountdownOverlay._clock(int(max(0.0, elapsed_seconds)))

    @staticmethod
    def _clock(whole_seconds: int) -> str:
        whole = max(0, whole_seconds)
        hours, remainder = divmod(whole, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _format_max(self, elapsed_seconds: float) -> str:
        return f"MAX {self._format_elapsed(elapsed_seconds)}"

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

    def _request_stop(self) -> None:
        if self._stopping:
            return
        self.mark_stopping()
        if self._on_stop is not None:
            self._on_stop()

    def mark_stopping(self) -> None:
        if self._stopping or self._closed:
            return
        self._stopping = True
        try:
            self._stop_button.configure(
                text=tr("保存中…", "Saving…"),
                state="disabled",
                bg="#7F1D1D",
                cursor="arrow",
            )
        except tk.TclError:
            self._closed = True

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
