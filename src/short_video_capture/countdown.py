"""屏幕右下角录制倒计时浮层。"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

from .i18n import tr
from .widgets import RoundedButton, RoundedCard, render_dot_png
from .win32 import (
    exclude_from_capture,
    make_overlay_window,
    primary_work_area,
)

_TRANSPARENT_KEY = "#010203"
_BG = "#0F172A"
_FG = "#F8FAFC"
_MUTED = "#CBD5E1"
_ACCENT = "#F55342"
_ACCENT_HOVER = "#E54332"
_ACCENT_ACTIVE = "#D63726"
_DOT_DIM = "#7F1D1D"


class CountdownOverlay:
    """置顶、无边框、圆角抗锯齿的倒计时浮层，录制时显示在屏幕右下角。"""

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
        self._timer_width = 10 if self._max_mode else 7
        self._last_timer_text: str | None = None

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        try:
            self._dpi_scale = max(1.0, self.window.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi_scale = 1.0
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        transparent_supported = True
        try:
            self.window.configure(bg=_TRANSPARENT_KEY)
            self.window.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except tk.TclError:
            transparent_supported = False
            self.window.configure(bg=_BG)
        self.window.attributes("-alpha", 0.96)

        card = RoundedCard(
            self.window,
            parent_bg=_TRANSPARENT_KEY if transparent_supported else _BG,
            card_bg=_BG,
            border=_ACCENT,
            border_width=1.2,
            radius=self._s(12),
            pad_x=self._s(14),
            pad_y=self._s(9),
            shadow=False,
            transparent_key=transparent_supported,
        )
        card.pack(fill="both", expand=True)
        content = card.inner

        dot_size = self._s(16)
        dot_radius = self._s(4)
        self._dot_on_photo = tk.PhotoImage(
            master=self.window,
            data=render_dot_png(
                dot_size,
                _ACCENT,
                _BG,
                dot_radius=dot_radius,
                glow_alpha=0.38,
                glow_blur=3.0,
            ),
        )
        self._dot_off_photo = tk.PhotoImage(
            master=self.window,
            data=render_dot_png(
                dot_size,
                _DOT_DIM,
                _BG,
                dot_radius=dot_radius,
            ),
        )
        self._dot = tk.Label(
            content,
            image=self._dot_on_photo,
            bg=_BG,
            bd=0,
            highlightthickness=0,
        )
        self._dot.pack(side="left", padx=(0, self._s(5)))

        tk.Label(
            content,
            text=tr("录制中", "REC"),
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(8)))

        self._timer_font = tkfont.Font(family="Consolas", size=16, weight="bold")
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

        self._stop_button = RoundedButton(
            content,
            text=tr("停止", "Stop"),
            command=self._request_stop,
            parent_bg=_BG,
            fill=_ACCENT,
            hover_fill=_ACCENT_HOVER,
            active_fill=_ACCENT_ACTIVE,
            fg="#FFFFFF",
            disabled_fill="#7F1D1D",
            disabled_fg="#FECACA",
            font=("Microsoft YaHei UI", 9, "bold"),
            min_width=self._s(58),
            text_pad_x=self._s(12),
            height=self._s(30),
            radius=self._s(7),
        )
        self._stop_button.pack(side="left", padx=(self._s(10), 0))

    def _s(self, px: int) -> int:
        return max(1, round(px * self._dpi_scale))

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
        if text == self._last_timer_text:
            return
        self._last_timer_text = text
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
            self._dot.configure(
                image=self._dot_on_photo if self._dot_visible else self._dot_off_photo
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
            self._stop_button.set_text(tr("保存中…", "Saving…"))
            self._stop_button.set_state("disabled")
            self.window.update_idletasks()
            self._place_bottom_right()
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
