"""纯 Tk 的目录选择器。

Windows 的 Shell 目录对话框在受限/沙箱进程中可能不显示或长时间阻塞，
因此这里使用自绘窗口浏览目录，避免主界面卡死。
"""

from __future__ import annotations

import ctypes
import os
import string
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk

from .i18n import tr

_BG = "#F1F5F9"
_CARD = "#FFFFFF"
_BORDER = "#E2E8F0"
_TEXT = "#0F172A"
_MUTED = "#64748B"
_ACCENT = "#EF4444"
_ACCENT_HOVER = "#DC2626"
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

        self.window = tk.Toplevel(parent)
        self.window.withdraw()
        self.window.title(tr("选择保存文件夹", "Select a save folder"))
        self.window.transient(parent)
        self.window.configure(bg=_BG)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self.window.geometry("660x500")
        self.window.bind("<Escape>", lambda _event: self._cancel())

        header = tk.Frame(self.window, bg="#0F172A")
        header.pack(fill="x")
        tk.Label(
            header,
            text=tr("选择保存文件夹", "Select a save folder"),
            bg="#0F172A",
            fg="#F8FAFC",
            font=("Microsoft YaHei UI", 14, "bold"),
            anchor="w",
        ).pack(fill="x", padx=20, pady=14)

        body = tk.Frame(self.window, bg=_BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)

        path_row = tk.Frame(body, bg=_BG)
        path_row.pack(fill="x")
        self.path_var = tk.StringVar(value=str(self._current))
        self.path_entry = tk.Entry(
            path_row,
            textvariable=self.path_var,
            bg=_CARD,
            fg=_TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_INFO,
            font=("Microsoft YaHei UI", 9),
        )
        self.path_entry.pack(side="left", fill="x", expand=True, ipady=7)
        self.path_entry.bind("<Return>", lambda _event: self._go_to_entry())
        self._button(
            path_row, tr("转到", "Go"), self._go_to_entry, _CARD, _INFO
        ).pack(
            side="left", padx=(8, 0)
        )
        self._button(
            path_row, tr("上一级", "Up"), self._go_parent, _CARD, _TEXT
        ).pack(
            side="left", padx=(6, 0)
        )

        drive_row = tk.Frame(body, bg=_BG)
        drive_row.pack(fill="x", pady=(10, 8))
        tk.Label(
            drive_row,
            text=tr("驱动器", "Drives"),
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, 6))
        self.drive_var = tk.StringVar()
        drive_box = ttk.Combobox(
            drive_row,
            textvariable=self.drive_var,
            values=available_drives(),
            state="readonly",
            width=7,
            font=("Microsoft YaHei UI", 9),
        )
        drive_box.pack(side="left")
        drive_box.bind("<<ComboboxSelected>>", self._on_drive_selected)
        self._button(drive_row, tr("刷新", "Refresh"), self._refresh, _BG, _INFO).pack(
            side="left", padx=(10, 0)
        )

        list_frame = tk.Frame(
            body,
            bg=_CARD,
            highlightbackground=_BORDER,
            highlightthickness=1,
        )
        list_frame.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side="right", fill="y")
        self.listbox = tk.Listbox(
            list_frame,
            bg=_CARD,
            fg=_TEXT,
            selectbackground="#DBEAFE",
            selectforeground=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            activestyle="none",
            font=("Microsoft YaHei UI", 10),
            yscrollcommand=scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        scrollbar.configure(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", self._open_selected)
        self.listbox.bind("<Return>", self._open_selected)

        self.error_var = tk.StringVar()
        tk.Label(
            body,
            textvariable=self.error_var,
            bg=_BG,
            fg=_WARN,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
            justify="left",
            wraplength=610,
        ).pack(fill="x", pady=(6, 0))

        actions = tk.Frame(self.window, bg=_BG)
        actions.pack(fill="x", padx=18, pady=(0, 14))
        self._button(
            actions, tr("新建文件夹", "New folder"), self._create_folder, "#E2E8F0", _TEXT
        ).pack(
            side="left"
        )
        self._button(
            actions, tr("取消", "Cancel"), self._cancel, "#E2E8F0", _TEXT
        ).pack(
            side="right"
        )
        self._button(
            actions,
            tr("选择此文件夹", "Select this folder"),
            self._select_current,
            _ACCENT,
            "#FFFFFF",
        ).pack(side="right", padx=(0, 8))

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
        try:
            parent_x = self._parent.winfo_rootx()
            parent_y = self._parent.winfo_rooty()
            parent_w = self._parent.winfo_width()
            parent_h = self._parent.winfo_height()
        except tk.TclError:
            return
        width = 660
        height = 500
        x = max(0, parent_x + (parent_w - width) // 2)
        y = max(0, parent_y + (parent_h - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        background: str,
        foreground: str,
    ) -> tk.Button:
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground="#CBD5E1",
            activeforeground=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=12,
            pady=6,
            cursor="hand2",
            font=(
                ("Microsoft YaHei UI", 9, "bold")
                if background == _ACCENT
                else ("Microsoft YaHei UI", 9)
            ),
        )

    def _navigate(self, path: Path) -> None:
        try:
            target = path.expanduser().resolve()
            children = list_subdirectories(target)
        except OSError as exc:
            self.error_var.set(
                tr("无法打开文件夹：{error}", "Cannot open folder: {error}", error=exc)
            )
            return
        if not target.is_dir():
            self.error_var.set(
                tr("不是文件夹：{path}", "Not a folder: {path}", path=target)
            )
            return
        self.error_var.set("")
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

    def _on_drive_selected(self, _event: object) -> None:
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
        name = simpledialog.askstring(
            tr("新建文件夹", "New folder"),
            tr("文件夹名称：", "Folder name:"),
            parent=self.window,
        )
        if not name:
            return
        name = name.strip()
        if not name or name in (".", "..") or any(char in name for char in '\\/:*?"<>|'):
            self.error_var.set(tr("文件夹名称无效。", "Invalid folder name."))
            return
        target = self._current / name
        try:
            target.mkdir()
        except OSError as exc:
            self.error_var.set(
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
        self.window.destroy()


def ask_directory(parent: tk.Misc, initial: Path) -> Path | None:
    return DirectoryPicker(parent, initial).show()
