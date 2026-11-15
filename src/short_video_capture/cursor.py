"""把 Windows 鼠标指针绘制进屏幕帧。"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .capture import CaptureRegion

LOGGER = logging.getLogger(__name__)

CURSOR_SHOWING = 0x0001
CURSOR_SUPPRESSED = 0x0002
SM_CXCURSOR = 13
SM_CYCURSOR = 14
DI_NORMAL = 0x0003
DIB_RGB_COLORS = 0
BI_RGB = 0


class CURSORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", wintypes.POINT),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class CursorRenderer:
    """把当前鼠标指针画到帧的对应位置。

    先复制光标下方的一小块原画面到 32 位 DIB，再让 ``DrawIconEx``
    直接把光标绘制到这块背景上，这样彩色光标和单色光标都能正确
    处理透明、阴影与轮廓。
    """

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._width = max(8, int(self._user32.GetSystemMetrics(SM_CXCURSOR)))
        self._height = max(8, int(self._user32.GetSystemMetrics(SM_CYCURSOR)))
        self._setup_api()

        self._screen_dc = self._user32.GetDC(None)
        self._memory_dc = self._gdi32.CreateCompatibleDC(self._screen_dc)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = self._width
        info.bmiHeader.biHeight = -self._height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        self._dib = self._gdi32.CreateDIBSection(
            self._memory_dc,
            ctypes.byref(info),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        if not self._dib or not bits:
            raise OSError("无法创建鼠标指针绘制缓冲区。")
        self._bits = bits
        self._old_bitmap = self._gdi32.SelectObject(self._memory_dc, self._dib)
        buffer_type = ctypes.c_ubyte * (self._width * self._height * 4)
        self._buffer = buffer_type.from_address(bits.value)
        self._array = np.ctypeslib.as_array(self._buffer).reshape(
            self._height, self._width, 4
        )
        self._backup = np.empty((self._height, self._width, 4), dtype=np.uint8)
        self._cached_cursor: int = 0
        self._cached_hotspot: tuple[int, int] = (0, 0)
        self._cached_at: float = 0.0
        self._closed = False

    def _setup_api(self) -> None:
        user32 = self._user32
        gdi32 = self._gdi32
        user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        user32.GetCursorInfo.restype = wintypes.BOOL
        user32.GetIconInfo.argtypes = [
            wintypes.HICON,
            ctypes.POINTER(ICONINFO),
        ]
        user32.GetIconInfo.restype = wintypes.BOOL
        user32.DrawIconEx.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HICON,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.HANDLE,
            wintypes.UINT,
        ]
        user32.DrawIconEx.restype = wintypes.BOOL
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL

    def _cursor_hotspot(self, handle: int) -> tuple[int, int] | None:
        now = time.monotonic()
        if handle == self._cached_cursor and now - self._cached_at < 1.0:
            return self._cached_hotspot
        icon = ICONINFO()
        if not self._user32.GetIconInfo(handle, ctypes.byref(icon)):
            return None
        try:
            hotspot = (int(icon.xHotspot), int(icon.yHotspot))
        finally:
            if icon.hbmMask:
                self._gdi32.DeleteObject(icon.hbmMask)
            if icon.hbmColor:
                self._gdi32.DeleteObject(icon.hbmColor)
        self._cached_cursor = handle
        self._cached_hotspot = hotspot
        self._cached_at = now
        return hotspot

    def _draw_core(
        self,
        frame: np.ndarray,
        region: "CaptureRegion",
        *,
        save_backup: bool,
    ) -> tuple[int, int, int, int] | None:
        if self._closed:
            return None
        info = CURSORINFO()
        info.cbSize = ctypes.sizeof(CURSORINFO)
        if not self._user32.GetCursorInfo(ctypes.byref(info)):
            return None
        if not info.flags & CURSOR_SHOWING or info.flags & CURSOR_SUPPRESSED:
            return None
        if not info.hCursor:
            return None

        hotspot = self._cursor_hotspot(int(info.hCursor))
        if hotspot is None:
            return None
        left = int(info.ptScreenPos.x) - hotspot[0] - region.left
        top = int(info.ptScreenPos.y) - hotspot[1] - region.top
        frame_h, frame_w = frame.shape[:2]
        crop_left = max(0, left)
        crop_top = max(0, top)
        crop_right = min(frame_w, left + self._width)
        crop_bottom = min(frame_h, top + self._height)
        if crop_right <= crop_left or crop_bottom <= crop_top:
            return None

        dib_x = crop_left - left
        dib_y = crop_top - top
        copy_w = crop_right - crop_left
        copy_h = crop_bottom - crop_top
        region_slice = frame[crop_top:crop_bottom, crop_left:crop_right]
        if save_backup:
            self._backup[:copy_h, :copy_w] = region_slice
        self._array[dib_y : dib_y + copy_h, dib_x : dib_x + copy_w] = region_slice
        if not self._user32.DrawIconEx(
            self._memory_dc,
            0,
            0,
            info.hCursor,
            self._width,
            self._height,
            0,
            None,
            DI_NORMAL,
        ):
            return None
        frame[crop_top:crop_bottom, crop_left:crop_right] = self._array[
            dib_y : dib_y + copy_h, dib_x : dib_x + copy_w
        ]
        return (crop_top, crop_bottom, crop_left, crop_right)

    def draw(self, frame: np.ndarray, region: "CaptureRegion") -> np.ndarray:
        """把当前鼠标指针画到帧上（原地修改并返回）。"""

        self._draw_core(frame, region, save_backup=False)
        return frame

    def draw_patch(
        self, frame: np.ndarray, region: "CaptureRegion"
    ) -> tuple[int, int, int, int] | None:
        """原地绘制鼠标指针并备份被覆盖的小区域，供后续 ``restore_patch`` 恢复。"""

        return self._draw_core(frame, region, save_backup=True)

    def restore_patch(
        self, frame: np.ndarray, patch: tuple[int, int, int, int] | None
    ) -> None:
        """恢复 ``draw_patch`` 覆盖的像素，避免整帧拷贝。"""

        if patch is None:
            return
        crop_top, crop_bottom, crop_left, crop_right = patch
        copy_h = crop_bottom - crop_top
        copy_w = crop_right - crop_left
        frame[crop_top:crop_bottom, crop_left:crop_right] = self._backup[
            :copy_h, :copy_w
        ]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._old_bitmap:
            self._gdi32.SelectObject(self._memory_dc, self._old_bitmap)
        if self._dib:
            self._gdi32.DeleteObject(self._dib)
        if self._memory_dc:
            self._gdi32.DeleteDC(self._memory_dc)
        if self._screen_dc:
            self._user32.ReleaseDC(None, self._screen_dc)
