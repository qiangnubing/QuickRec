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
MONITOR_DEFAULTTONEAREST = 2

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


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(RECT),
    wintypes.LPARAM,
)


def tk_geometry(width: int, height: int, x: int, y: int) -> str:
    """构造 Tk ``wm geometry`` 字符串。

    Tk 中 ``-X`` 表示距主屏右/下边缘的距离而非负坐标；只有 ``+-X`` 才表示
    真正的负屏幕坐标（如扩展屏位于主屏左侧或上方），因此这里固定使用 ``+``。
    """

    return f"{int(width)}x{int(height)}+{int(x)}+{int(y)}"


def center_position(
    parent_rect: ScreenRect,
    child_width: int,
    child_height: int,
    work_area: ScreenRect | None = None,
) -> tuple[int, int]:
    """计算子窗口相对父窗口居中的左上角坐标，并可选限制在工作区内。"""

    x = parent_rect.left + (parent_rect.width - child_width) // 2
    y = parent_rect.top + (parent_rect.height - child_height) // 2
    if work_area is not None:
        max_x = max(work_area.left, work_area.right - child_width)
        max_y = max(work_area.top, work_area.bottom - child_height)
        x = max(work_area.left, min(x, max_x))
        y = max(work_area.top, min(y, max_y))
    return x, y


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


def monitor_work_area_for_rect(rect: ScreenRect) -> ScreenRect:
    """返回包含指定矩形面积最大的显示器工作区（不含任务栏）。"""

    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromRect.argtypes = [ctypes.POINTER(RECT), wintypes.DWORD]
        user32.MonitorFromRect.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL

        query = RECT(
            int(rect.left),
            int(rect.top),
            int(max(rect.left + 1, rect.right)),
            int(max(rect.top + 1, rect.bottom)),
        )
        monitor = user32.MonitorFromRect(ctypes.byref(query), MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return primary_work_area()
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return primary_work_area()
        work = info.rcWork
        width = work.right - work.left
        height = work.bottom - work.top
        if width <= 0 or height <= 0:
            return primary_work_area()
        return ScreenRect(work.left, work.top, width, height)
    except (AttributeError, OSError):
        return primary_work_area()


def monitor_work_area_for_point(x: int, y: int) -> ScreenRect:
    """返回指定屏幕坐标所在显示器的工作区（不含任务栏）。"""

    return monitor_work_area_for_rect(ScreenRect(int(x), int(y), 1, 1))


def display_monitors() -> list[ScreenRect]:
    """枚举当前所有活动显示器的屏幕矩形；失败时回退到整个虚拟桌面。"""

    monitors: list[ScreenRect] = []
    try:
        user32 = ctypes.windll.user32
        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(RECT),
            _MONITORENUMPROC,
            wintypes.LPARAM,
        ]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL

        def _collect(
            _hmon: int, _hdc: int, lprect: object, _lparam: int
        ) -> bool:
            rect = lprect.contents  # type: ignore[attr-defined]
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width > 0 and height > 0:
                monitors.append(ScreenRect(rect.left, rect.top, width, height))
            return True

        callback = _MONITORENUMPROC(_collect)
        user32.EnumDisplayMonitors(None, None, callback, 0)
    except (AttributeError, OSError):
        monitors.clear()
    if not monitors:
        return [virtual_screen_rect()]
    return monitors


def _toplevel_hwnd(window: object) -> int:
    hwnd = int(window.winfo_id())  # type: ignore[attr-defined]
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    parent = user32.GetParent(hwnd)
    return int(parent or hwnd)


def window_rect(window: object) -> ScreenRect | None:
    """返回 Tk 顶层窗口的外框物理屏幕矩形。"""

    try:
        hwnd = _toplevel_hwnd(window)
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return ScreenRect(rect.left, rect.top, width, height)
    except (AttributeError, OSError, ValueError):
        return None


def set_window_bounds(window: object, rect: ScreenRect) -> bool:
    """用 Win32 SetWindowPos 精确设置顶层窗口的物理坐标和尺寸。"""

    try:
        hwnd = _toplevel_hwnd(window)
        user32 = ctypes.windll.user32
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
        return bool(
            user32.SetWindowPos(
                hwnd,
                None,
                int(rect.left),
                int(rect.top),
                int(rect.width),
                int(rect.height),
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
        )
    except (AttributeError, OSError, ValueError):
        return False


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


def make_overlay_window(window: object, *, click_through: bool = False) -> None:
    """让浮层不抢焦点、不进入 Alt+Tab；可选择是否完全穿透鼠标。"""

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
    style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        style |= WS_EX_TRANSPARENT
    else:
        style &= ~WS_EX_TRANSPARENT
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


def make_click_through(window: object) -> None:
    make_overlay_window(window, click_through=True)
