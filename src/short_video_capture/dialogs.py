"""居中于父窗口的纯 Tk 模态提示弹窗。

Windows 原生 ``MessageBoxW`` 始终按屏幕居中，不会跟随主界面位置；
这里使用与主界面视觉一致的模态弹窗，确保在单屏与多屏扩展模式下都能
准确回归到主界面中央。
"""

from __future__ import annotations

import tkinter as tk

from .i18n import tr
from .widgets import RoundedButton, RoundedCard, render_dot_png
from .win32 import (
    ScreenRect,
    center_position,
    monitor_work_area_for_rect,
    primary_work_area,
    tk_geometry,
    window_rect,
)

_BG = "#F1F5F9"
_CARD_BG = "#FFFFFF"
_BORDER = "#E2E8F0"
_TEXT = "#0F172A"
_MUTED = "#64748B"
_ACCENT = "#F55342"
_ACCENT_HOVER = "#E54332"
_ACCENT_ACTIVE = "#D63726"
_BTN_NEUTRAL = "#E4EAF3"
_BTN_NEUTRAL_HOVER = "#D6DFEC"
_BTN_NEUTRAL_ACTIVE = "#C8D4E5"
_SUCCESS = "#15803D"
_INFO = "#2563EB"
_WARN = "#DC2626"

_KIND_COLORS = {
    "success": _SUCCESS,
    "info": _INFO,
    "warning": _WARN,
    "error": _WARN,
}


def _parent_outer_rect(parent: tk.Misc) -> tuple[ScreenRect | None, int, int]:
    """返回父窗口外框物理矩形及非客户区宽高差（边框与标题栏）。"""

    try:
        toplevel = parent.winfo_toplevel()
        toplevel.update_idletasks()
        if not toplevel.winfo_viewable():
            return None, 0, 0
        client_w = toplevel.winfo_width()
        client_h = toplevel.winfo_height()
        outer = window_rect(toplevel)
        if outer is not None and outer.width > 1 and outer.height > 1:
            chrome_w = max(0, outer.width - client_w) if client_w > 1 else 0
            chrome_h = max(0, outer.height - client_h) if client_h > 1 else 0
            return outer, chrome_w, chrome_h
        if client_w > 1 and client_h > 1:
            return (
                ScreenRect(
                    toplevel.winfo_x(),
                    toplevel.winfo_y(),
                    client_w,
                    client_h,
                ),
                0,
                0,
            )
    except tk.TclError:
        pass
    return None, 0, 0


def center_window_over_parent(
    window: tk.Toplevel,
    parent: tk.Misc,
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[int, int]:
    """把 ``window`` 精确居中放置到 ``parent`` 窗口中央，并限制在所在显示器工作区内。"""

    window.update_idletasks()
    req_w = max(1, int(width if width is not None else window.winfo_reqwidth()))
    req_h = max(1, int(height if height is not None else window.winfo_reqheight()))

    parent_rect, chrome_w, chrome_h = _parent_outer_rect(parent)
    try:
        if window.overrideredirect():
            chrome_w = 0
            chrome_h = 0
    except tk.TclError:
        pass

    if parent_rect is None:
        work_area = primary_work_area()
        target_rect = work_area
    else:
        work_area = monitor_work_area_for_rect(parent_rect)
        target_rect = parent_rect

    x, y = center_position(
        target_rect,
        req_w + chrome_w,
        req_h + chrome_h,
        work_area=work_area,
    )
    window.geometry(tk_geometry(req_w, req_h, x, y))
    return x, y


class _ModalDialog:
    """居中于父窗口的通用模态提示框。"""

    def __init__(
        self,
        parent: tk.Misc,
        title: str,
        message: str,
        *,
        kind: str = "info",
        yes_text: str | None = None,
        no_text: str | None = None,
        ok_text: str | None = None,
    ) -> None:
        self._parent = parent
        self._result = False
        self._confirm_on_enter = True

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(title)
        self.window.configure(bg=_BG)
        self.window.resizable(False, False)
        try:
            self._dpi_scale = max(1.0, self.window.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi_scale = 1.0
        try:
            self.window.transient(parent.winfo_toplevel())
        except tk.TclError:
            pass
        self.window.protocol("WM_DELETE_WINDOW", self._on_cancel)

        outer = tk.Frame(self.window, bg=_BG)
        outer.pack(
            fill="both",
            expand=True,
            padx=self._s(16),
            pady=(self._s(14), self._s(14)),
        )

        card = RoundedCard(
            outer,
            parent_bg=_BG,
            card_bg=_CARD_BG,
            border=_BORDER,
            radius=self._s(10),
            pad_x=self._s(18),
            pad_y=(self._s(16), self._s(16)),
            shadow=True,
        )
        card.pack(fill="both", expand=True)

        content = tk.Frame(card.inner, bg=_CARD_BG)
        content.pack(fill="both", expand=True)

        accent = _KIND_COLORS.get(kind, _INFO)
        dot_size = self._s(14)
        self._badge_photo = tk.PhotoImage(
            master=self.window,
            data=render_dot_png(
                dot_size,
                accent,
                _CARD_BG,
                dot_radius=self._s(4),
                glow_alpha=0.25,
                glow_blur=2.5,
            ),
        )

        header_row = tk.Frame(content, bg=_CARD_BG)
        header_row.pack(fill="x", pady=(0, self._s(10)))
        tk.Label(
            header_row,
            image=self._badge_photo,
            bg=_CARD_BG,
            bd=0,
            highlightthickness=0,
        ).pack(side="left", padx=(0, self._s(8)))
        tk.Label(
            header_row,
            text=title,
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        tk.Label(
            content,
            text=message,
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            wraplength=self._s(380),
        ).pack(fill="x", padx=(self._s(2), self._s(2)))

        actions = tk.Frame(outer, bg=_BG)
        actions.pack(fill="x", pady=(self._s(12), 0))

        btn_height = self._s(34)
        btn_radius = self._s(7)
        if ok_text is not None:
            RoundedButton(
                actions,
                text=ok_text,
                command=self._on_confirm,
                parent_bg=_BG,
                fill=_ACCENT,
                hover_fill=_ACCENT_HOVER,
                active_fill=_ACCENT_ACTIVE,
                fg="#FFFFFF",
                font=("Microsoft YaHei UI", 9, "bold"),
                min_width=self._s(84),
                text_pad_x=self._s(18),
                height=btn_height,
                radius=btn_radius,
            ).pack(side="right")
        else:
            RoundedButton(
                actions,
                text=no_text or tr("否", "No"),
                command=self._on_cancel,
                parent_bg=_BG,
                fill=_BTN_NEUTRAL,
                hover_fill=_BTN_NEUTRAL_HOVER,
                active_fill=_BTN_NEUTRAL_ACTIVE,
                fg=_TEXT,
                font=("Microsoft YaHei UI", 9),
                min_width=self._s(80),
                text_pad_x=self._s(16),
                height=btn_height,
                radius=btn_radius,
            ).pack(side="right")
            RoundedButton(
                actions,
                text=yes_text or tr("是", "Yes"),
                command=self._on_confirm,
                parent_bg=_BG,
                fill=_ACCENT,
                hover_fill=_ACCENT_HOVER,
                active_fill=_ACCENT_ACTIVE,
                fg="#FFFFFF",
                font=("Microsoft YaHei UI", 9, "bold"),
                min_width=self._s(84),
                text_pad_x=self._s(18),
                height=btn_height,
                radius=btn_radius,
            ).pack(side="right", padx=(0, self._s(8)))

        self.window.bind("<Escape>", lambda _event: self._on_cancel())
        self.window.bind("<Return>", lambda _event: self._on_confirm())
        self.window.bind("<KP_Enter>", lambda _event: self._on_confirm())

        self.window.update_idletasks()
        req_w = max(self._s(360), self.window.winfo_reqwidth())
        req_h = self.window.winfo_reqheight()
        center_window_over_parent(self.window, parent, width=req_w, height=req_h)
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    def _s(self, px: int) -> int:
        return max(1, round(px * self._dpi_scale))

    def _on_confirm(self) -> None:
        self._result = True
        self._close()

    def _on_cancel(self) -> None:
        self._result = False
        self._close()

    def _close(self) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def show(self) -> bool:
        try:
            self.window.wait_window(self.window)
        finally:
            try:
                self._parent.winfo_toplevel().focus_set()
            except tk.TclError:
                pass
        return self._result


def ask_yes_no(
    parent: tk.Misc,
    title: str,
    message: str,
    *,
    yes_text: str | None = None,
    no_text: str | None = None,
    kind: str = "info",
) -> bool:
    """显示居中于父窗口的是/否确认弹窗。"""

    return _ModalDialog(
        parent,
        title,
        message,
        kind=kind,
        yes_text=yes_text or tr("是", "Yes"),
        no_text=no_text or tr("否", "No"),
    ).show()


def show_error(parent: tk.Misc, title: str, message: str) -> None:
    """显示居中于父窗口的错误提示弹窗。"""

    _ModalDialog(
        parent,
        title,
        message,
        kind="error",
        ok_text=tr("确定", "OK"),
    ).show()
