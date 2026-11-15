"""屏幕采集与 H.264 视频编码核心逻辑，不依赖界面。"""

from __future__ import annotations

import ctypes
import logging
import math
import os
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import mss
import numpy as np

from .config import MAX_DURATION
from .cursor import BI_RGB, BITMAPINFO, BITMAPINFOHEADER, DIB_RGB_COLORS, CursorRenderer
from .i18n import tr
from .media import FFmpegVideoSink

LOGGER = logging.getLogger(__name__)
DEFAULT_FPS = 30
DEFAULT_BITRATE_KBPS = 8_000
MIN_REGION_SIZE = 2
MIN_FPS = 5
MAX_FPS = 60
MIN_BITRATE_KBPS = 500
MAX_BITRATE_KBPS = 50_000


class RecordingError(RuntimeError):
    """录制过程失败。"""


class NoFramesError(RecordingError):
    """停止时还没有采集到第一帧。"""


@dataclass(frozen=True, slots=True)
class VideoSettings:
    fps: int = DEFAULT_FPS
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS

    def __post_init__(self) -> None:
        if not MIN_FPS <= self.fps <= MAX_FPS:
            raise ValueError(
                tr(
                    "帧率必须在 {low} 到 {high} 之间。",
                    "Frame rate must be between {low} and {high}.",
                    low=MIN_FPS,
                    high=MAX_FPS,
                )
            )
        if not MIN_BITRATE_KBPS <= self.bitrate_kbps <= MAX_BITRATE_KBPS:
            raise ValueError(
                tr(
                    "视频码率必须在 {low} 到 {high} kbps 之间。",
                    "Video bitrate must be between {low} and {high} kbps.",
                    low=MIN_BITRATE_KBPS,
                    high=MAX_BITRATE_KBPS,
                )
            )

    @property
    def bitrate_mbps(self) -> float:
        return self.bitrate_kbps / 1000.0


class ScreenSource(Protocol):
    def grab(self) -> np.ndarray: ...

    def close(self) -> None: ...


class VideoSink(Protocol):
    def write(self, frame: np.ndarray) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < MIN_REGION_SIZE or self.height < MIN_REGION_SIZE:
            raise ValueError(
                tr(
                    "录制区域至少需要 {size}×{size} 像素。",
                    "Recording area must be at least {size}×{size} pixels.",
                    size=MIN_REGION_SIZE,
                )
            )

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def even_sized(self) -> "CaptureRegion":
        """编码器通常只接受偶数宽高。"""

        width = self.width - self.width % 2
        height = self.height - self.height % 2
        if width < MIN_REGION_SIZE or height < MIN_REGION_SIZE:
            raise ValueError(
                tr(
                    "录制区域太小，无法编码视频。",
                    "Recording area is too small to encode.",
                )
            )
        return CaptureRegion(self.left, self.top, width, height)

    def as_monitor(self) -> dict[str, int]:
        region = self.even_sized()
        return {
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        }


@dataclass(frozen=True, slots=True)
class VideoCaptureResult:
    path: Path
    frame_count: int
    elapsed_seconds: float
    stopped_early: bool
    fps: int

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps


_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000


class _Win32GdiSource:
    """复用单个 DIBSection，通过 BitBlt 零分配抓取屏幕区域。"""

    def __init__(self, region: CaptureRegion) -> None:
        region = region.even_sized()
        self._left = region.left
        self._top = region.top
        self._width = region.width
        self._height = region.height
        self._closed = False
        self._has_frame = False
        self._warned_transient = False

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._setup_api()

        self._screen_dc = self._user32.GetDC(None)
        if not self._screen_dc:
            raise OSError("无法获取屏幕 DC。")
        self._memory_dc = self._gdi32.CreateCompatibleDC(self._screen_dc)
        if not self._memory_dc:
            self._user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None
            raise OSError("无法创建兼容内存 DC。")

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
            self._gdi32.DeleteDC(self._memory_dc)
            self._user32.ReleaseDC(None, self._screen_dc)
            self._memory_dc = None
            self._screen_dc = None
            raise OSError("无法创建屏幕采集缓冲区。")
        self._old_bitmap = self._gdi32.SelectObject(self._memory_dc, self._dib)
        buffer_type = ctypes.c_ubyte * (self._width * self._height * 4)
        self._buffer = buffer_type.from_address(bits.value)
        self._array = np.ctypeslib.as_array(self._buffer).reshape(
            self._height, self._width, 4
        )

    def _setup_api(self) -> None:
        user32 = self._user32
        gdi32 = self._gdi32
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
        gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.BitBlt.restype = wintypes.BOOL

    def grab(self) -> np.ndarray:
        ok = self._gdi32.BitBlt(
            self._memory_dc,
            0,
            0,
            self._width,
            self._height,
            self._screen_dc,
            self._left,
            self._top,
            _SRCCOPY | _CAPTUREBLT,
        )
        if not ok:
            error = ctypes.get_last_error()
            if not self._has_frame:
                raise RecordingError(
                    tr(
                        "屏幕采集失败（错误码 {code}）。",
                        "Screen capture failed (error {code}).",
                        code=error,
                    )
                )
            if not self._warned_transient:
                self._warned_transient = True
                LOGGER.warning("屏幕采集暂时返回错误码 %s，沿用上一帧", error)
            return self._array
        self._has_frame = True
        return self._array

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if getattr(self, "_old_bitmap", None) and self._memory_dc:
            self._gdi32.SelectObject(self._memory_dc, self._old_bitmap)
            self._old_bitmap = None
        if getattr(self, "_dib", None):
            self._gdi32.DeleteObject(self._dib)
            self._dib = None
        if getattr(self, "_memory_dc", None):
            self._gdi32.DeleteDC(self._memory_dc)
            self._memory_dc = None
        if getattr(self, "_screen_dc", None):
            self._user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None


class _MssSource:
    def __init__(self, region: CaptureRegion) -> None:
        self._screen = mss.MSS()
        self._monitor = region.as_monitor()

    def grab(self) -> np.ndarray:
        return np.asarray(self._screen.grab(self._monitor))

    def close(self) -> None:
        self._screen.close()


def _default_source(region: CaptureRegion) -> ScreenSource:
    if os.name == "nt":
        try:
            return _Win32GdiSource(region)
        except OSError as exc:
            LOGGER.warning("GDI 屏幕采集初始化失败，回退到 mss：%s", exc)
    return _MssSource(region)


SourceFactory = Callable[[CaptureRegion], ScreenSource]
SinkFactory = Callable[[Path, CaptureRegion, int, int], VideoSink]


def _open_ffmpeg_sink(
    path: Path, region: CaptureRegion, fps: int, bitrate_kbps: int
) -> VideoSink:
    return FFmpegVideoSink(
        path,
        width=region.width,
        height=region.height,
        fps=fps,
        bitrate_kbps=bitrate_kbps,
    )


def record_video(
    region: CaptureRegion,
    output_path: Path,
    duration_seconds: float | None,
    settings: VideoSettings,
    *,
    stop_event: threading.Event | None = None,
    source_factory: SourceFactory | None = None,
    sink_factory: SinkFactory | None = None,
    on_start: Callable[[], None] | None = None,
    include_cursor: bool = True,
) -> VideoCaptureResult:
    """录制指定区域，阻塞直到录制完成或 ``stop_event`` 被设置。"""

    if duration_seconds is not None and not 0 < duration_seconds <= MAX_DURATION:
        raise ValueError(
            tr(
                "录制时长必须在 1 到 {high} 秒之间，或为 MAX。",
                "Duration must be between 1 and {high} seconds, or MAX.",
                high=MAX_DURATION,
            )
        )
    output_path = Path(output_path)
    if output_path.exists():
        raise RecordingError(
            tr(
                "目标视频文件已存在：{path}",
                "The target video already exists: {path}",
                path=output_path,
            )
        )
    region = region.even_sized()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecordingError(
            tr(
                "无法创建视频保存目录：{path}\n{error}",
                "Cannot create the video folder: {path}\n{error}",
                path=output_path.parent,
                error=exc,
            )
        ) from exc

    source_factory = source_factory or _default_source
    sink_factory = sink_factory or _open_ffmpeg_sink
    total_frames = (
        None
        if duration_seconds is None
        else max(1, math.ceil(duration_seconds * settings.fps))
    )
    frame_period = 1.0 / settings.fps

    source: ScreenSource | None = None
    sink: VideoSink | None = None
    cursor_renderer: CursorRenderer | None = None
    frame_count = 0
    background: np.ndarray | None = None
    stopped_early = False
    capture_error: BaseException | None = None
    close_error: BaseException | None = None
    started_at = time.perf_counter()

    try:
        source = source_factory(region)
        sink = sink_factory(
            output_path,
            region,
            settings.fps,
            settings.bitrate_kbps,
        )
        if include_cursor:
            try:
                cursor_renderer = CursorRenderer()
            except Exception as exc:
                raise RecordingError(
                    tr(
                        "无法初始化鼠标指针绘制：{error}",
                        "Cannot initialize cursor drawing: {error}",
                        error=exc,
                    )
                ) from exc

        supports_patch_cursor = (
            cursor_renderer is not None
            and hasattr(cursor_renderer, "draw_patch")
            and hasattr(cursor_renderer, "restore_patch")
        )

        def emit(frame: np.ndarray) -> None:
            nonlocal cursor_renderer
            if cursor_renderer is None:
                sink.write(frame)
                return
            if supports_patch_cursor:
                patch: tuple[int, int, int, int] | None = None
                try:
                    patch = cursor_renderer.draw_patch(frame, region)
                except Exception:
                    LOGGER.exception("绘制鼠标指针失败，本次录制将不再绘制鼠标")
                    cursor_renderer.close()
                    cursor_renderer = None
                    sink.write(frame)
                    return
                try:
                    sink.write(frame)
                finally:
                    if cursor_renderer is not None and patch is not None:
                        cursor_renderer.restore_patch(frame, patch)
                return

            output = frame.copy()
            try:
                cursor_renderer.draw(output, region)
            except Exception:
                LOGGER.exception("绘制鼠标指针失败，本次录制将不再绘制鼠标")
                cursor_renderer.close()
                cursor_renderer = None
                output = frame
            sink.write(output)

        if on_start is not None:
            on_start()
        started_at = time.perf_counter()
        deadline = (
            None if duration_seconds is None else started_at + duration_seconds
        )
        last_grab_at = started_at
        while total_frames is None or frame_count < total_frames:
            target = started_at + frame_count * frame_period
            if deadline is not None and target >= deadline:
                break
            now = time.perf_counter()
            if stop_event is not None and stop_event.is_set():
                stopped_early = True
                break
            if now < target:
                wait_seconds = target - now
                if deadline is not None:
                    wait_seconds = min(wait_seconds, deadline - now)
                if stop_event is not None:
                    # Windows 上 Event.wait 会对齐到 15.6ms 系统时钟周期；
                    # Python 3.12 的 time.sleep 使用高精度等待计时器，分片上限 50ms 兼顾精度与响应。
                    time.sleep(min(wait_seconds, 0.05))
                else:
                    time.sleep(wait_seconds)
                continue

            # 只在距离上一帧足够久、且没有明显落后于时间轴时抓取新画面；
            # 编码输出仍按时隙逐帧推进，高帧率下用上一帧补齐。
            too_late = now - target > frame_period * 0.5
            grab_due = (
                background is None
                or (now - last_grab_at >= frame_period * 0.85 and not too_late)
            )
            if grab_due:
                last_grab_at = time.perf_counter()
                grabbed = source.grab()
                if grabbed is None or grabbed.ndim != 3 or grabbed.shape[2] != 4:
                    raise RecordingError(
                        tr(
                            "采集到的画面格式异常（需要 BGRA）。",
                            "Captured frame format is invalid (BGRA required).",
                        )
                    )
                background = (
                    grabbed
                    if grabbed.flags.c_contiguous
                    else np.ascontiguousarray(grabbed)
                )

            emit(background)
            frame_count += 1

        if (
            not stopped_early
            and total_frames is not None
            and background is not None
            and frame_count < total_frames
        ):
            for _ in range(total_frames - frame_count):
                emit(background)
                frame_count += 1
    except BaseException as exc:
        capture_error = exc
    finally:
        if cursor_renderer is not None:
            try:
                cursor_renderer.close()
            except Exception:
                LOGGER.exception("关闭鼠标指针绘制器失败")
        if sink is not None:
            try:
                if capture_error is not None and hasattr(sink, "abort"):
                    sink.abort()  # type: ignore[attr-defined]
                else:
                    sink.close()
            except BaseException as exc:
                close_error = exc
                LOGGER.exception("关闭视频编码器失败")
        if source is not None:
            try:
                source.close()
            except Exception:
                LOGGER.exception("关闭屏幕采集器失败")

    if capture_error is not None:
        _remove_partial_file(output_path)
        if isinstance(capture_error, (KeyboardInterrupt, SystemExit)):
            raise capture_error
        if isinstance(capture_error, RecordingError):
            raise capture_error
        raise RecordingError(
            tr("录制失败：{error}", "Recording failed: {error}", error=capture_error)
        ) from capture_error
    if close_error is not None:
        _remove_partial_file(output_path)
        raise RecordingError(
            tr("保存视频失败：{error}", "Cannot save the video: {error}", error=close_error)
        ) from close_error

    if frame_count == 0 or background is None:
        _remove_partial_file(output_path)
        raise NoFramesError(
            tr(
                "没有采集到任何画面，请重新选择录制区域。",
                "No frames were captured. Select the recording area again.",
            )
        )

    if not output_path.is_file() or output_path.stat().st_size == 0:
        _remove_partial_file(output_path)
        raise RecordingError(
            tr(
                "视频文件未生成或内容为空。",
                "The video file was not created or is empty.",
            )
        )

    elapsed = time.perf_counter() - started_at
    if duration_seconds is not None:
        elapsed = min(elapsed, duration_seconds)
    LOGGER.info(
        "视频录制完成：%s，%d 帧，%.1f 秒，%d FPS，%d kbps%s",
        output_path,
        frame_count,
        elapsed,
        settings.fps,
        settings.bitrate_kbps,
        "（提前停止）" if stopped_early else "",
    )
    return VideoCaptureResult(
        path=output_path,
        frame_count=frame_count,
        elapsed_seconds=elapsed,
        stopped_early=stopped_early,
        fps=settings.fps,
    )


def _remove_partial_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("无法删除未完成的视频文件：%s", path)
