"""项目用到的最小 Win32 封装。"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

LOGGER = logging.getLogger(__name__)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SPI_GETWORKAREA = 0x0030

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


@dataclass(frozen=True, slots=True)
class ScreenRect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def enable_dpi_awareness() -> None:
    """让 Tk 和 mss 使用同一套物理像素坐标。"""

    try:
        set_context = ctypes.windll.user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        if set_context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass

    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(2) == 0:  # PER_MONITOR_DPI_AWARE
            return
    except (AttributeError, OSError):
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        LOGGER.warning("无法启用 DPI 感知，框选坐标可能偏移。")


def virtual_screen_rect() -> ScreenRect:
    user32 = ctypes.windll.user32
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if width <= 0 or height <= 0:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
    return ScreenRect(left, top, width, height)


def primary_work_area() -> ScreenRect:
    rect = RECT()
    ok = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
    )
    if not ok:
        screen = virtual_screen_rect()
        return ScreenRect(screen.left, screen.top, screen.width, screen.height)
    return ScreenRect(
        rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    )


def _toplevel_hwnd(window: object) -> int:
    hwnd = int(window.winfo_id())  # type: ignore[attr-defined]
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    parent = user32.GetParent(hwnd)
    return int(parent or hwnd)


def exclude_from_capture(window: object) -> bool:
    """尽量让倒计时窗口不出现在录制画面中（Win10 2004+）。"""

    hwnd = _toplevel_hwnd(window)
    try:
        set_affinity = ctypes.windll.user32.SetWindowDisplayAffinity
        set_affinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        set_affinity.restype = wintypes.BOOL
        return bool(set_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
    except (AttributeError, OSError):
        return False


def make_click_through(window: object) -> None:
    """让倒计时浮层不抢焦点、不拦截鼠标，也不出现在 Alt+Tab 中。"""

    hwnd = _toplevel_hwnd(window)
    user32 = ctypes.windll.user32
    get_style = user32.GetWindowLongPtrW
    set_style = user32.SetWindowLongPtrW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    style = int(get_style(hwnd, GWL_EXSTYLE))
    style |= WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    set_style(hwnd, GWL_EXSTYLE, style)
    user32.SetWindowPos(
        hwnd,
        None,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
