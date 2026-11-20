"""纯 Tk 的目录选择器。

Windows 的 Shell 目录对话框在受限/沙箱进程中可能不显示或长时间阻塞，
因此这里使用自绘圆角窗口浏览目录，避免主界面卡死并与主界面风格保持一致。
"""

from __future__ import annotations

import ctypes
import os
import string
import tkinter as tk
from pathlib import Path

from .dialogs import ask_string, center_window_over_parent
from .i18n import tr
from .widgets import (
    RoundedButton,
    RoundedCard,
    RoundedDropdown,
    RoundedEntry,
    RoundedScrollbar,
)

_PICKER_WIDTH = 520
_PICKER_HEIGHT = 390

_BG = "#F1F5F9"
_HEADER_BG = "#0F172A"
_CARD = "#FFFFFF"
_FIELD_BG = "#F8FAFC"
_BORDER = "#E2E8F0"
_INPUT_BORDER = "#D8E0EA"
_TEXT = "#0F172A"
_MUTED = "#64748B"
_ACCENT = "#F55342"
_ACCENT_HOVER = "#E54332"
_ACCENT_ACTIVE = "#D63726"
_BTN_NEUTRAL = "#E4EAF3"
_BTN_NEUTRAL_HOVER = "#D6DFEC"
_BTN_NEUTRAL_ACTIVE = "#C8D4E5"
_BTN_INFO_BG = "#EFF6FF"
_BTN_INFO_HOVER = "#DBEAFE"
_BTN_INFO_ACTIVE = "#BFDBFE"
_BTN_INFO_BORDER = "#DBEAFE"
_INFO = "#2563EB"
_WARN = "#B91C1C"


def list_subdirectories(path: Path) -> list[Path]:
    """返回目录下一级的子文件夹，无法访问的条目会被跳过。"""

    children: list[Path] = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    children.append(Path(entry.path))
            except OSError:
                continue
    return sorted(children, key=lambda item: item.name.casefold())


def available_drives() -> list[str]:
    try:
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    return [
        f"{letter}:\\"
        for index, letter in enumerate(string.ascii_uppercase)
        if mask & (1 << index)
    ]


class DirectoryPicker:
    def __init__(self, parent: tk.Misc, initial: Path) -> None:
        self._parent = parent
        self._result: Path | None = None
        self._items: list[Path] = []
        self._current = self._nearest_existing(initial)
        self._error_visible = False

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(tr("选择保存文件夹", "Select a save folder"))
        try:
            self.window.transient(parent.winfo_toplevel())
        except tk.TclError:
            pass
        self.window.configure(bg=_BG)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        try:
            self._dpi_scale = max(1.0, self.window.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi_scale = 1.0
        self._width = self._s(_PICKER_WIDTH)
        self._height = self._s(_PICKER_HEIGHT)
        self.window.geometry(f"{self._width}x{self._height}")
        self.window.bind("<Escape>", lambda _event: self._cancel())

        header = tk.Frame(self.window, bg=_HEADER_BG)
        header.pack(side="top", fill="x")
        tk.Label(
            header,
            text=tr("选择保存文件夹", "Select a save folder"),
            bg=_HEADER_BG,
            fg="#F8FAFC",
            font=("Microsoft YaHei UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x", padx=self._s(16), pady=self._s(10))

        self._actions = tk.Frame(self.window, bg=_BG)
        self._actions.pack(
            side="bottom",
            fill="x",
            padx=self._s(14),
            pady=(0, self._s(12)),
        )
        btn_height = self._s(32)
        btn_radius = self._s(7)

        self._new_folder_button = RoundedButton(
            self._actions,
            text=tr("新建文件夹", "New folder"),
            command=self._create_folder,
            parent_bg=_BG,
            fill=_BTN_NEUTRAL,
            hover_fill=_BTN_NEUTRAL_HOVER,
            active_fill=_BTN_NEUTRAL_ACTIVE,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            min_width=self._s(86),
            text_pad_x=self._s(14),
            height=btn_height,
            radius=btn_radius,
        )
        self._new_folder_button.pack(side="left")

        self._cancel_button = RoundedButton(
            self._actions,
            text=tr("取消", "Cancel"),
            command=self._cancel,
            parent_bg=_BG,
            fill=_BTN_NEUTRAL,
            hover_fill=_BTN_NEUTRAL_HOVER,
            active_fill=_BTN_NEUTRAL_ACTIVE,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            min_width=self._s(68),
            text_pad_x=self._s(14),
            height=btn_height,
            radius=btn_radius,
        )
        self._cancel_button.pack(side="right")

        self._select_button = RoundedButton(
            self._actions,
            text=tr("选择此文件夹", "Select this folder"),
            command=self._select_current,
            parent_bg=_BG,
            fill=_ACCENT,
            hover_fill=_ACCENT_HOVER,
            active_fill=_ACCENT_ACTIVE,
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
            min_width=self._s(98),
            text_pad_x=self._s(16),
            height=btn_height,
            radius=btn_radius,
        )
        self._select_button.pack(side="right", padx=(0, self._s(8)))

        body = tk.Frame(self.window, bg=_BG)
        body.pack(
            side="top",
            fill="both",
            expand=True,
            padx=self._s(14),
            pady=(self._s(10), self._s(10)),
        )

        # 路径栏与驱动器选择卡片
        nav_card = RoundedCard(
            body,
            parent_bg=_BG,
            card_bg=_CARD,
            border=_BORDER,
            radius=self._s(10),
            pad_x=self._s(12),
            pad_y=(self._s(9), self._s(10)),
            shadow=True,
        )
        nav_card.pack(fill="x")

        path_row = tk.Frame(nav_card.inner, bg=_CARD)
        path_row.pack(fill="x")
        self.path_var = tk.StringVar(value=str(self._current))
        path_box = RoundedEntry(
            path_row,
            textvariable=self.path_var,
            parent_bg=_CARD,
            field_bg=_FIELD_BG,
            border=_INPUT_BORDER,
            focus_border=_INFO,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            height=self._s(30),
            radius=self._s(6),
            pad_x=self._s(8),
        )
        path_box.pack(side="left", fill="x", expand=True)
        self.path_entry = path_box.entry
        self.path_entry.bind("<Return>", lambda _event: self._go_to_entry())

        self._go_button = RoundedButton(
            path_row,
            text=tr("转到", "Go"),
            command=self._go_to_entry,
            parent_bg=_CARD,
            fill=_BTN_INFO_BG,
            hover_fill=_BTN_INFO_HOVER,
            active_fill=_BTN_INFO_ACTIVE,
            fg=_INFO,
            border=_BTN_INFO_BORDER,
            font=("Microsoft YaHei UI", 9),
            min_width=self._s(52),
            text_pad_x=self._s(12),
            height=self._s(30),
            radius=self._s(6),
        )
        self._go_button.pack(side="left", padx=(self._s(6), 0))

        self._up_button = RoundedButton(
            path_row,
            text=tr("上一级", "Up"),
            command=self._go_parent,
            parent_bg=_CARD,
            fill=_BTN_NEUTRAL,
            hover_fill=_BTN_NEUTRAL_HOVER,
            active_fill=_BTN_NEUTRAL_ACTIVE,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            min_width=self._s(60),
            text_pad_x=self._s(12),
            height=self._s(30),
            radius=self._s(6),
        )
        self._up_button.pack(side="left", padx=(self._s(6), 0))

        drive_row = tk.Frame(nav_card.inner, bg=_CARD)
        drive_row.pack(fill="x", pady=(self._s(7), 0))
        tk.Label(
            drive_row,
            text=tr("驱动器", "Drives"),
            bg=_CARD,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(6)))
        self.drive_var = tk.StringVar()
        self.drive_dropdown = RoundedDropdown(
            drive_row,
            textvariable=self.drive_var,
            values=available_drives(),
            on_select=self._on_drive_selected,
            parent_bg=_CARD,
            field_bg=_CARD,
            border=_INPUT_BORDER,
            hover_border="#CBD5E1",
            active_border=_INFO,
            fg=_TEXT,
            arrow_color=_MUTED,
            font=("Microsoft YaHei UI", 9),
            width=self._s(82),
            height=self._s(28),
            radius=self._s(6),
            pad_x=self._s(10),
        )
        self.drive_dropdown.pack(side="left")

        self._refresh_button = RoundedButton(
            drive_row,
            text=tr("刷新", "Refresh"),
            command=self._refresh,
            parent_bg=_CARD,
            fill=_BTN_INFO_BG,
            hover_fill=_BTN_INFO_HOVER,
            active_fill=_BTN_INFO_ACTIVE,
            fg=_INFO,
            border=_BTN_INFO_BORDER,
            font=("Microsoft YaHei UI", 9),
            min_width=self._s(56),
            text_pad_x=self._s(12),
            height=self._s(28),
            radius=self._s(6),
        )
        self._refresh_button.pack(side="right")

        # 文件夹列表圆角卡片
        self._list_card = RoundedCard(
            body,
            parent_bg=_BG,
            card_bg=_CARD,
            border=_BORDER,
            radius=self._s(10),
            pad_x=self._s(10),
            pad_y=(self._s(8), self._s(10)),
            shadow=True,
        )
        self._list_card.pack(fill="both", expand=True, pady=(self._s(8), 0))

        self.scrollbar = RoundedScrollbar(
            self._list_card.inner,
            bg=_CARD,
            width=self._s(10),
            thumb_pad_x=self._s(2),
            thumb_pad_y=self._s(2),
            min_thumb_height=self._s(24),
        )
        self.scrollbar.pack(side="right", fill="y", padx=(self._s(4), 0))
        self.listbox = tk.Listbox(
            self._list_card.inner,
            bg=_CARD,
            fg=_TEXT,
            selectbackground="#DBEAFE",
            selectforeground=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            activestyle="none",
            height=1,
            font=("Microsoft YaHei UI", 9),
            yscrollcommand=self.scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar.configure(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._open_selected)
        self.listbox.bind("<Return>", self._open_selected)

        self.error_var = tk.StringVar()
        self._error_label = tk.Label(
            body,
            textvariable=self.error_var,
            bg=_BG,
            fg=_WARN,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
            justify="left",
            wraplength=self._s(_PICKER_WIDTH - 36),
        )

        self._navigate(self._current)
        self.window.update_idletasks()
        self._center_over_parent()
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.path_entry.focus_set()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    def _s(self, px: int) -> int:
        return max(1, round(px * self._dpi_scale))

    def _set_error(self, message: str) -> None:
        self.error_var.set(message)
        if message and not self._error_visible:
            self._error_label.pack(
                side="bottom",
                fill="x",
                pady=(self._s(6), 0),
                before=self._list_card,
            )
            self._error_visible = True
        elif not message and self._error_visible:
            self._error_label.pack_forget()
            self._error_visible = False

    def show(self) -> Path | None:
        self.window.wait_window(self.window)
        return self._result

    @staticmethod
    def _nearest_existing(path: Path) -> Path:
        try:
            candidate = path.expanduser().resolve()
        except OSError:
            candidate = Path.home()
        while not candidate.is_dir():
            if candidate.parent == candidate:
                return Path.home()
            candidate = candidate.parent
        return candidate

    def _center_over_parent(self) -> None:
        center_window_over_parent(
            self.window,
            self._parent,
            width=self._width,
            height=self._height,
        )

    def _navigate(self, path: Path) -> None:
        try:
            target = path.expanduser().resolve()
            children = list_subdirectories(target)
        except OSError as exc:
            self._set_error(
                tr("无法打开文件夹：{error}", "Cannot open folder: {error}", error=exc)
            )
            return
        if not target.is_dir():
            self._set_error(
                tr("不是文件夹：{path}", "Not a folder: {path}", path=target)
            )
            return
        self._set_error("")
        self._current = target
        self.path_var.set(str(target))
        self._items = []
        self.listbox.delete(0, tk.END)
        if target.parent != target:
            self._items.append(target.parent)
            self.listbox.insert(tk.END, "..")
        for child in children:
            self._items.append(child)
            self.listbox.insert(tk.END, child.name)
        drive = target.drive
        if drive:
            self.drive_var.set(drive + "\\")

    def _go_to_entry(self) -> None:
        self._navigate(Path(self.path_var.get().strip()))

    def _go_parent(self) -> None:
        self._navigate(self._current.parent)

    def _on_drive_selected(self, _event: object | None = None) -> None:
        if self.drive_var.get():
            self._navigate(Path(self.drive_var.get()))

    def _refresh(self) -> None:
        self._navigate(self._current)

    def _open_selected(self, _event: object | None = None) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._items):
            self._navigate(self._items[index])

    def _create_folder(self) -> None:
        name = ask_string(
            self.window,
            tr("新建文件夹", "New folder"),
            tr("文件夹名称：", "Folder name:"),
        )
        if not name:
            return
        name = name.strip()
        if not name or name in (".", "..") or any(char in name for char in '\\/:*?"<>|'):
            self._set_error(tr("文件夹名称无效。", "Invalid folder name."))
            return
        target = self._current / name
        try:
            target.mkdir()
        except OSError as exc:
            self._set_error(
                tr("创建文件夹失败：{error}", "Cannot create folder: {error}", error=exc)
            )
            return
        self._navigate(target)

    def _select_current(self) -> None:
        self._result = self._current
        self._close()

    def _cancel(self) -> None:
        self._result = None
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


def ask_directory(parent: tk.Misc, initial: Path) -> Path | None:
    return DirectoryPicker(parent, initial).show()
