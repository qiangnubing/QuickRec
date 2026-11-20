"""全屏框选录制区域。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .capture import CaptureRegion
from .i18n import tr
from .widgets import RoundedButton, RoundedCard
from .win32 import (
    ScreenRect,
    display_monitors,
    monitor_work_area_for_rect,
    set_window_bounds,
    tk_geometry,
    virtual_screen_rect,
    window_rect,
)

MIN_SELECTION = 32
_OVERLAY_BG = "#020617"
_ACCENT = "#38BDF8"
_HINT_COLOR = "#E2E8F0"
_WARN_COLOR = "#FCA5A5"
_TRANSPARENT_KEY = "#010203"
_BAR_BG = "#0F172A"
_BAR_BORDER = "#334155"
_BTN_PRIMARY = "#F55342"
_BTN_PRIMARY_HOVER = "#E54332"
_BTN_PRIMARY_ACTIVE = "#D63726"
_BTN_SECONDARY = "#1E293B"
_BTN_SECONDARY_HOVER = "#334155"
_BTN_SECONDARY_ACTIVE = "#475569"


def compute_action_bar_position(
    selection: ScreenRect,
    bar_width: int,
    bar_height: int,
    area: ScreenRect,
    *,
    gap: int = 12,
    margin: int = 16,
) -> tuple[int, int]:
    """计算框选确认操作条的屏幕坐标，使其停留在选区所在显示器工作区内。"""

    selection_center_x = selection.left + selection.width // 2
    x = selection_center_x - bar_width // 2
    y = selection.bottom + gap
    if y + bar_height > area.bottom - margin:
        y = selection.top - bar_height - gap
    if y < area.top + margin:
        y = area.bottom - bar_height - margin
    max_x = max(area.left + margin, area.right - bar_width - margin)
    max_y = max(area.top + margin, area.bottom - bar_height - margin)
    x = max(area.left + margin, min(x, max_x))
    y = max(area.top + margin, min(y, max_y))
    return x, y


class RegionSelector:
    """跨显示器的全屏遮罩，拖拽框选并确认后回调。"""

    def __init__(
        self,
        root: tk.Misc,
        on_confirm: Callable[[CaptureRegion], None],
        on_cancel: Callable[[], None],
    ) -> None:
        self._root = root
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._virtual = virtual_screen_rect()
        self._selection: tuple[int, int, int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._dragging = False
        self._closed = False
        self._bar: tk.Toplevel | None = None
        self._warning_job: str | None = None

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        try:
            self._dpi_scale = max(1.0, self.window.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi_scale = 1.0
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.45)
        self.window.configure(bg=_OVERLAY_BG)
        self.window.maxsize(self._virtual.width, self._virtual.height)
        self.window.geometry(
            tk_geometry(
                self._virtual.width,
                self._virtual.height,
                self._virtual.left,
                self._virtual.top,
            )
        )

        self.canvas = tk.Canvas(
            self.window,
            bg=_OVERLAY_BG,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        title_text = tr("拖动鼠标框选录制区域", "Drag to select a recording area")
        self._hint_default = tr(
            "松开后确认选区　·　Enter 开始录制　·　Esc 取消",
            "Release to confirm · Enter to start · Esc to cancel",
        )
        self._title_ids: list[int] = []
        self._hint_ids: list[int] = []
        for monitor in display_monitors():
            center_x = (monitor.left - self._virtual.left) + monitor.width // 2
            top_y = monitor.top - self._virtual.top
            self._title_ids.append(
                self.canvas.create_text(
                    center_x,
                    top_y + 42,
                    text=title_text,
                    fill="#FFFFFF",
                    font=("Microsoft YaHei UI", 18, "bold"),
                    tags=("ui", "title"),
                )
            )
            self._hint_ids.append(
                self.canvas.create_text(
                    center_x,
                    top_y + 76,
                    text=self._hint_default,
                    fill=_HINT_COLOR,
                    font=("Microsoft YaHei UI", 10),
                    tags=("ui", "hint"),
                )
            )
        self._title_id = self._title_ids[0]
        self._hint_id = self._hint_ids[0]

        self._cross_h = self.canvas.create_line(
            0, -10, self._virtual.width, -10, fill=_ACCENT, dash=(4, 6), tags="cross"
        )
        self._cross_v = self.canvas.create_line(
            -10, 0, -10, self._virtual.height, fill=_ACCENT, dash=(4, 6), tags="cross"
        )

        self._selection_id = self.canvas.create_rectangle(
            0, 0, 0, 0, outline=_ACCENT, width=2, state="hidden", tags="selection"
        )
        self._size_id = self.canvas.create_text(
            0,
            0,
            text="",
            fill="#E0F2FE",
            font=("Consolas", 10, "bold"),
            anchor="nw",
            state="hidden",
            tags="selection",
        )
        self._handles = [
            self.canvas.create_rectangle(
                0, 0, 0, 0, fill="#FFFFFF", outline=_ACCENT, state="hidden", tags="selection"
            )
            for _ in range(4)
        ]

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Button-3>", lambda _event: self.cancel())

        for sequence in ("<Escape>", "<Return>", "<KP_Enter>"):
            self.window.bind(sequence, self._on_key)

        self.window.deiconify()
        self.window.update_idletasks()
        actual = window_rect(self.window)
        if actual != self._virtual:
            set_window_bounds(self.window, self._virtual)
            self.window.update_idletasks()
        self.window.lift()
        self.window.focus_force()
        self.canvas.focus_set()

    def _on_key(self, event: tk.Event) -> str:
        if event.keysym == "Escape":
            self.cancel()
        elif self._selection is not None:
            self._confirm()
        return "break"

    def _on_hover(self, event: tk.Event) -> None:
        if self._dragging or self._selection is not None:
            return
        x = max(0, min(event.x, self._virtual.width - 1))
        y = max(0, min(event.y, self._virtual.height - 1))
        self.canvas.coords(self._cross_h, 0, y, self._virtual.width, y)
        self.canvas.coords(self._cross_v, x, 0, x, self._virtual.height)

    def _on_press(self, event: tk.Event) -> None:
        self._dismiss_bar()
        self._selection = None
        self._set_hint(self._hint_default, _HINT_COLOR)
        self.canvas.itemconfigure(self._size_id, state="hidden")
        for handle in self._handles:
            self.canvas.itemconfigure(handle, state="hidden")
        self.canvas.itemconfigure(self._cross_h, state="hidden")
        self.canvas.itemconfigure(self._cross_v, state="hidden")

        x = max(0, min(event.x, self._virtual.width - 1))
        y = max(0, min(event.y, self._virtual.height - 1))
        self._drag_origin = (x, y)
        self._dragging = True
        self.canvas.coords(self._selection_id, x, y, x, y)
        self.canvas.itemconfigure(self._selection_id, state="normal")

    def _on_drag(self, event: tk.Event) -> None:
        if not self._dragging or self._drag_origin is None:
            return
        x1, y1 = self._drag_origin
        x2 = max(0, min(event.x, self._virtual.width - 1))
        y2 = max(0, min(event.y, self._virtual.height - 1))
        left, top = min(x1, x2), min(y1, y2)
        right, bottom = max(x1, x2), max(y1, y2)
        self.canvas.coords(self._selection_id, left, top, right, bottom)
        self._move_size_label(left, top, right - left, bottom - top)

    def _on_release(self, event: tk.Event) -> None:
        if not self._dragging or self._drag_origin is None:
            return
        self._dragging = False
        x1, y1 = self._drag_origin
        x2 = max(0, min(event.x, self._virtual.width - 1))
        y2 = max(0, min(event.y, self._virtual.height - 1))
        self._drag_origin = None
        left, top = min(x1, x2), min(y1, y2)
        right, bottom = max(x1, x2), max(y1, y2)
        width, height = right - left, bottom - top

        if width < MIN_SELECTION or height < MIN_SELECTION:
            self._selection = None
            self.canvas.itemconfigure(self._selection_id, state="hidden")
            self.canvas.itemconfigure(self._size_id, state="hidden")
            for handle in self._handles:
                self.canvas.itemconfigure(handle, state="hidden")
            self._set_hint(
                tr(
                    "选区至少需要 {size}×{size} 像素，请重新拖动",
                    "The selection must be at least {size}×{size} pixels. Drag again.",
                    size=MIN_SELECTION,
                ),
                _WARN_COLOR,
            )
            self._schedule_hint_reset()
            return

        self._selection = (left, top, width, height)
        self.canvas.coords(self._selection_id, left, top, right, bottom)
        self._move_size_label(left, top, width, height)
        self._place_handles(left, top, right, bottom)
        self._set_hint(
            tr(
                "确认选区后开始录制　·　Enter 开始　·　Esc 取消",
                "Press Enter or Start to record · Esc to cancel",
            ),
            _HINT_COLOR,
        )
        self._show_action_bar()

    def _move_size_label(self, left: int, top: int, width: int, height: int) -> None:
        label_x = min(max(left + 8, 8), self._virtual.width - 150)
        label_y = max(top + 8, 8)
        self.canvas.coords(self._size_id, label_x, label_y)
        self.canvas.itemconfigure(
            self._size_id, text=f"{width} × {height}", state="normal"
        )

    def _place_handles(
        self, left: int, top: int, right: int, bottom: int
    ) -> None:
        size = 6
        points = (
            (left, top),
            (right, top),
            (left, bottom),
            (right, bottom),
        )
        for handle, (x, y) in zip(self._handles, points, strict=True):
            self.canvas.coords(
                handle, x - size, y - size, x + size, y + size
            )
            self.canvas.itemconfigure(handle, state="normal")
            self.canvas.tag_raise(handle)

    def _s(self, px: int) -> int:
        return max(1, round(px * self._dpi_scale))

    def _show_action_bar(self) -> None:
        if self._selection is None or self._bar is not None:
            return
        left, top, width, height = self._selection
        bar = tk.Toplevel(self.window)
        bar.withdraw()
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        transparent_supported = True
        try:
            bar.configure(bg=_TRANSPARENT_KEY)
            bar.attributes("-transparentcolor", _TRANSPARENT_KEY)
        except tk.TclError:
            transparent_supported = False
            bar.configure(bg=_BAR_BG)
        self._bar = bar

        card = RoundedCard(
            bar,
            parent_bg=_TRANSPARENT_KEY if transparent_supported else _BAR_BG,
            card_bg=_BAR_BG,
            border=_BAR_BORDER,
            border_width=1.0,
            radius=self._s(10),
            pad_x=self._s(14),
            pad_y=self._s(10),
            shadow=False,
            transparent_key=transparent_supported,
        )
        card.pack(fill="both", expand=True)
        content = card.inner

        tk.Label(
            content,
            text=tr(
                "选区 {width} × {height}",
                "Selection {width} × {height}",
                width=width,
                height=height,
            ),
            bg=_BAR_BG,
            fg="#CBD5E1",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(14)))

        btn_height = self._s(32)
        btn_radius = self._s(7)
        RoundedButton(
            content,
            text=tr("开始录制", "Start recording"),
            command=self._confirm,
            parent_bg=_BAR_BG,
            fill=_BTN_PRIMARY,
            hover_fill=_BTN_PRIMARY_HOVER,
            active_fill=_BTN_PRIMARY_ACTIVE,
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
            text_pad_x=self._s(16),
            height=btn_height,
            radius=btn_radius,
        ).pack(side="left", padx=(0, self._s(8)))

        RoundedButton(
            content,
            text=tr("重新框选", "Reselect"),
            command=self._reset_selection,
            parent_bg=_BAR_BG,
            fill=_BTN_SECONDARY,
            hover_fill=_BTN_SECONDARY_HOVER,
            active_fill=_BTN_SECONDARY_ACTIVE,
            fg="#E2E8F0",
            border=_BAR_BORDER,
            font=("Microsoft YaHei UI", 9),
            text_pad_x=self._s(12),
            height=btn_height,
            radius=btn_radius,
        ).pack(side="left", padx=(0, self._s(8)))

        RoundedButton(
            content,
            text=tr("取消", "Cancel"),
            command=self.cancel,
            parent_bg=_BAR_BG,
            fill=_BTN_SECONDARY,
            hover_fill=_BTN_SECONDARY_HOVER,
            active_fill=_BTN_SECONDARY_ACTIVE,
            fg="#CBD5E1",
            border=_BAR_BORDER,
            font=("Microsoft YaHei UI", 9),
            text_pad_x=self._s(12),
            height=btn_height,
            radius=btn_radius,
        ).pack(side="left")

        bar.update_idletasks()
        bar_width = bar.winfo_reqwidth()
        bar_height = bar.winfo_reqheight()
        selection_rect = self._selection_screen_rect()
        area = monitor_work_area_for_rect(selection_rect)
        x, y = compute_action_bar_position(
            selection_rect,
            bar_width,
            bar_height,
            area,
            gap=self._s(12),
            margin=self._s(16),
        )
        bar.geometry(tk_geometry(bar_width, bar_height, x, y))
        bar.deiconify()
        bar.lift(self.window)
        try:
            for sequence in ("<Escape>", "<Return>", "<KP_Enter>"):
                bar.bind(sequence, self._on_key)
        except tk.TclError:
            pass
        self.window.focus_force()
        bar.lift(self.window)

    def _canvas_origin(self) -> tuple[int, int]:
        try:
            return int(self.canvas.winfo_rootx()), int(self.canvas.winfo_rooty())
        except tk.TclError:
            return self._virtual.left, self._virtual.top

    def _selection_screen_rect(self) -> ScreenRect:
        assert self._selection is not None
        left, top, width, height = self._selection
        origin_x, origin_y = self._canvas_origin()
        return ScreenRect(origin_x + left, origin_y + top, width, height)

    def _reset_selection(self) -> None:
        self._dismiss_bar()
        self._selection = None
        self._dragging = False
        self._drag_origin = None
        self.canvas.itemconfigure(self._selection_id, state="hidden")
        self.canvas.itemconfigure(self._size_id, state="hidden")
        for handle in self._handles:
            self.canvas.itemconfigure(handle, state="hidden")
        self.canvas.itemconfigure(self._cross_h, state="normal")
        self.canvas.itemconfigure(self._cross_v, state="normal")
        self._set_hint(self._hint_default, _HINT_COLOR)
        self.window.focus_force()

    def _dismiss_bar(self) -> None:
        if self._bar is None:
            return
        try:
            self._bar.destroy()
        except tk.TclError:
            pass
        self._bar = None

    def _set_hint(self, text: str, color: str) -> None:
        try:
            self.canvas.itemconfigure("hint", text=text, fill=color)
        except tk.TclError:
            pass

    def _schedule_hint_reset(self) -> None:
        if self._warning_job is not None:
            try:
                self.window.after_cancel(self._warning_job)
            except tk.TclError:
                pass
        self._warning_job = self.window.after(
            1800, lambda: self._set_hint(self._hint_default, _HINT_COLOR)
        )

    def _confirm(self) -> None:
        if self._selection is None or self._closed:
            return
        selection_rect = self._selection_screen_rect()
        region = CaptureRegion(
            left=selection_rect.left,
            top=selection_rect.top,
            width=selection_rect.width,
            height=selection_rect.height,
        )
        self._close()
        self._on_confirm(region)

    def cancel(self) -> None:
        if self._closed:
            return
        self._close()
        self._on_cancel()

    def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._warning_job is not None:
            try:
                self.window.after_cancel(self._warning_job)
            except tk.TclError:
                pass
            self._warning_job = None
        self._dismiss_bar()
        try:
            self.window.destroy()
        except tk.TclError:
            pass
