"""基于 Win32 Shell_NotifyIcon 的系统托盘图标，无需额外依赖。"""

from __future__ import annotations

import ctypes
import itertools
import logging
import os
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Literal

LOGGER = logging.getLogger(__name__)

TrayCommand = Literal["stop", "cancel", "exit"]

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY_CALLBACK = WM_APP + 20

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512
SM_CXSMICON = 49
SM_CYSMICON = 50

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

CMD_STOP = 1001
CMD_CANCEL = 1002
CMD_EXIT = 1003

_TRAY_WINDOW_SEQUENCE = itertools.count(1)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class TrayError(RuntimeError):
    """系统托盘初始化失败。"""


class TrayIcon:
    """在独立线程中运行隐藏窗口和托盘消息循环。"""

    def __init__(
        self,
        tooltip: str,
        icon_path: Path,
        on_command: Callable[[TrayCommand], None],
    ) -> None:
        self._tooltip = tooltip
        self._icon_path = icon_path
        self._on_command = on_command
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._error: BaseException | None = None
        self._hwnd: int | None = None
        self._icon: int | None = None
        self._owns_icon = False
        self._class_name = (
            f"ShortVideoCaptureTray_{os.getpid()}_{next(_TRAY_WINDOW_SEQUENCE)}"
        )
        self._wndproc = WNDPROC(self._handle_message)
        self._menu_open = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, timeout: float = 4.0) -> None:
        if self._thread is not None:
            raise TrayError("托盘图标已经启动。")
        self._thread = threading.Thread(
            target=self._run, name="tray-icon", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TrayError("系统托盘启动超时，请稍后重试。")
        if self._error is not None:
            raise TrayError(f"系统托盘初始化失败：{self._error}") from self._error

    def stop(self, timeout: float = 2.0) -> None:
        hwnd = self._hwnd
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout)
            if thread.is_alive():
                LOGGER.warning("系统托盘线程未在 %.1f 秒内退出。", timeout)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        try:
            self._create_window()
            self._add_icon()
        except BaseException as exc:
            self._error = exc
            LOGGER.debug("创建系统托盘图标失败", exc_info=True)
        finally:
            self._ready.set()

        if self._error is None:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        self._cleanup()
        self._stopped.set()

    def _create_window(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        hinstance = kernel32.GetModuleHandleW(None)

        user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        user32.RegisterClassExW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
        user32.LoadCursorW.restype = wintypes.HANDLE
        user32.DefWindowProcW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.DestroyMenu.restype = wintypes.BOOL
        user32.AppendMenuW.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPCWSTR,
        ]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL

        window_class = WNDCLASSEXW()
        window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
        window_class.lpfnWndProc = self._wndproc
        window_class.hInstance = hinstance
        window_class.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        window_class.lpszClassName = self._class_name
        if not user32.RegisterClassExW(ctypes.byref(window_class)):
            error = ctypes.get_last_error()
            if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                raise ctypes.WinError(error)

        hwnd = user32.CreateWindowExW(
            0,
            self._class_name,
            "ShortVideoCapture",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = int(hwnd)

    def _load_icon(self) -> int:
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HANDLE
        hicon = user32.LoadImageW(
            None,
            str(self._icon_path),
            IMAGE_ICON,
            0,
            0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if hicon:
            self._owns_icon = True
            return int(hicon)

        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPVOID]
        user32.LoadIconW.restype = wintypes.HICON
        fallback = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
        if not fallback:
            raise TrayError("无法加载托盘图标。")
        self._owns_icon = False
        return int(fallback)

    def _add_icon(self) -> None:
        shell32 = ctypes.windll.shell32
        shell32.Shell_NotifyIconW.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        ]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        self._icon = self._load_icon()
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY_CALLBACK
        data.hIcon = self._icon
        data.szTip = self._tooltip[:127]
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            raise TrayError("无法把图标添加到系统托盘。")

    def _remove_icon(self) -> None:
        if not self._icon or not self._hwnd:
            return
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        try:
            ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        except OSError:
            LOGGER.warning("移除托盘图标失败")
        if self._owns_icon:
            try:
                ctypes.windll.user32.DestroyIcon(self._icon)
            except OSError:
                pass
        self._icon = None

    def _cleanup(self) -> None:
        self._remove_icon()
        hwnd = self._hwnd
        if hwnd:
            try:
                ctypes.windll.user32.DestroyWindow(hwnd)
            except OSError:
                pass
            self._hwnd = None

    def _handle_message(
        self, hwnd: int, message: int, wparam: int, lparam: int
    ) -> int:
        if message == WM_TRAY_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
            return 0
        if message == WM_CLOSE:
            self._cleanup()
            return 0
        if message == WM_DESTROY:
            self._remove_icon()
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        return int(
            ctypes.windll.user32.DefWindowProcW(hwnd, message, wparam, lparam)
        )

    def _show_menu(self) -> None:
        if self._menu_open:
            return
        self._menu_open = True
        user32 = ctypes.windll.user32
        menu = None
        try:
            user32.CreatePopupMenu.restype = wintypes.HMENU
            menu = user32.CreatePopupMenu()
            if not menu:
                return
            user32.AppendMenuW(menu, MF_STRING, CMD_STOP, "停止并保存")
            user32.AppendMenuW(menu, MF_STRING, CMD_CANCEL, "取消录制")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "退出程序（放弃录制）")

            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(self._hwnd)
            user32.TrackPopupMenu.argtypes = [
                wintypes.HMENU,
                wintypes.UINT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.LPVOID,
            ]
            user32.TrackPopupMenu.restype = wintypes.UINT
            command = user32.TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                point.x,
                point.y,
                0,
                self._hwnd,
                None,
            )
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            if command == CMD_STOP:
                self._emit("stop")
            elif command == CMD_CANCEL:
                self._emit("cancel")
            elif command == CMD_EXIT:
                self._emit("exit")
        finally:
            if menu:
                user32.DestroyMenu(menu)
            self._menu_open = False

    def _emit(self, command: TrayCommand) -> None:
        try:
            self._on_command(command)
        except Exception:
            LOGGER.exception("处理托盘菜单命令失败：%s", command)
