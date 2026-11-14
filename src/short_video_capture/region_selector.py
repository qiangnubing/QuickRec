"""全屏框选录制区域。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .capture import CaptureRegion
from .i18n import tr
from .win32 import primary_work_area, virtual_screen_rect

MIN_SELECTION = 32
_OVERLAY_BG = "#020617"
_ACCENT = "#38BDF8"
_HINT_COLOR = "#E2E8F0"
_WARN_COLOR = "#FCA5A5"


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
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.45)
        self.window.configure(bg=_OVERLAY_BG)
        self.window.geometry(
            f"{self._virtual.width}x{self._virtual.height}"
            f"{self._virtual.left:+d}{self._virtual.top:+d}"
        )

        self.canvas = tk.Canvas(
            self.window,
            bg=_OVERLAY_BG,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill="both", expand=True)

        center_x = self._virtual.width // 2
        self._title_id = self.canvas.create_text(
            center_x,
            42,
            text=tr("拖动鼠标框选录制区域", "Drag to select a recording area"),
            fill="#FFFFFF",
            font=("Microsoft YaHei UI", 18, "bold"),
            tags="ui",
        )
        self._hint_id = self.canvas.create_text(
            center_x,
            76,
            text=tr(
                "松开后确认选区　·　Enter 开始录制　·　Esc 取消",
                "Release to confirm · Enter to start · Esc to cancel",
            ),
            fill=_HINT_COLOR,
            font=("Microsoft YaHei UI", 10),
            tags="ui",
        )
        self._hint_default = self.canvas.itemcget(self._hint_id, "text")

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

    def _show_action_bar(self) -> None:
        if self._selection is None or self._bar is not None:
            return
        left, top, width, height = self._selection
        bar = tk.Toplevel(self.window)
        bar.overrideredirect(True)
        bar.attributes("-topmost", True)
        bar.configure(bg="#0B1220")
        self._bar = bar

        frame = tk.Frame(
            bar,
            bg="#0B1220",
            highlightbackground="#334155",
            highlightthickness=1,
        )
        frame.pack(fill="both", expand=True)
        content = tk.Frame(frame, bg="#0B1220")
        content.pack(padx=12, pady=9)

        tk.Label(
            content,
            text=tr(
                "选区 {width} × {height}",
                "Selection {width} × {height}",
                width=width,
                height=height,
            ),
            bg="#0B1220",
            fg="#94A3B8",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 14))

        start_button = tk.Button(
            content,
            text=tr("开始录制", "Start recording"),
            command=self._confirm,
            bg="#EF4444",
            fg="#FFFFFF",
            activebackground="#DC2626",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=18,
            pady=6,
            cursor="hand2",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        start_button.pack(side="left", padx=(0, 8))

        tk.Button(
            content,
            text=tr("重新框选", "Reselect"),
            command=self._reset_selection,
            bg="#1E293B",
            fg="#E2E8F0",
            activebackground="#334155",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 8))

        tk.Button(
            content,
            text=tr("取消", "Cancel"),
            command=self.cancel,
            bg="#1E293B",
            fg="#CBD5E1",
            activebackground="#334155",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left")

        bar.update_idletasks()
        bar_width = bar.winfo_reqwidth()
        bar_height = bar.winfo_reqheight()
        area = primary_work_area()
        selection_center_x = self._virtual.left + left + width // 2
        selection_center_y = self._virtual.top + top + height // 2
        if not (
            area.left <= selection_center_x <= area.right
            and area.top <= selection_center_y <= area.bottom
        ):
            area = self._virtual
        x = selection_center_x - bar_width // 2
        y = self._virtual.top + top + height + 12
        if y + bar_height > area.bottom - 16:
            y = self._virtual.top + top - bar_height - 12
        if y < area.top + 16:
            y = area.bottom - bar_height - 16
        x = max(area.left + 16, min(x, area.right - bar_width - 16))
        bar.geometry(f"{bar_width}x{bar_height}{x:+d}{y:+d}")
        bar.lift()
        try:
            bar.bind("<Escape>", self._on_key)
        except tk.TclError:
            pass
        self.window.focus_force()
        bar.lift()

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
            self.canvas.itemconfigure(self._hint_id, text=text, fill=color)
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
        left, top, width, height = self._selection
        region = CaptureRegion(
            left=self._virtual.left + left,
            top=self._virtual.top + top,
            width=width,
            height=height,
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
