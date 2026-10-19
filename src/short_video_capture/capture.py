"""屏幕采集与 H.264 视频编码核心逻辑，不依赖界面。"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import mss
import numpy as np

from .cursor import CursorRenderer
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


@dataclass(frozen=True, slots=True)
class VideoSettings:
    fps: int = DEFAULT_FPS
    bitrate_kbps: int = DEFAULT_BITRATE_KBPS

    def __post_init__(self) -> None:
        if not MIN_FPS <= self.fps <= MAX_FPS:
            raise ValueError(f"帧率必须在 {MIN_FPS} 到 {MAX_FPS} 之间。")
        if not MIN_BITRATE_KBPS <= self.bitrate_kbps <= MAX_BITRATE_KBPS:
            raise ValueError(
                f"视频码率必须在 {MIN_BITRATE_KBPS} 到 {MAX_BITRATE_KBPS} kbps 之间。"
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
            raise ValueError(f"录制区域至少需要 {MIN_REGION_SIZE}×{MIN_REGION_SIZE} 像素。")

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
            raise ValueError("录制区域太小，无法编码视频。")
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


class _MssSource:
    def __init__(self, region: CaptureRegion) -> None:
        self._screen = mss.MSS()
        self._monitor = region.as_monitor()

    def grab(self) -> np.ndarray:
        return np.asarray(self._screen.grab(self._monitor))

    def close(self) -> None:
        self._screen.close()


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
    duration_seconds: float,
    settings: VideoSettings,
    *,
    stop_event: threading.Event | None = None,
    source_factory: SourceFactory | None = None,
    sink_factory: SinkFactory | None = None,
    on_start: Callable[[], None] | None = None,
    include_cursor: bool = True,
) -> VideoCaptureResult:
    """录制指定区域，阻塞直到录制完成或 ``stop_event`` 被设置。"""

    if not 0 < duration_seconds <= 86400:
        raise ValueError("录制时长必须在 0 到 86400 秒之间。")
    output_path = Path(output_path)
    if output_path.exists():
        raise RecordingError(f"目标视频文件已存在：{output_path}")
    region = region.even_sized()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecordingError(f"无法创建视频保存目录：{output_path.parent}\n{exc}") from exc

    source_factory = source_factory or _MssSource
    sink_factory = sink_factory or _open_ffmpeg_sink
    total_frames = max(1, math.ceil(duration_seconds * settings.fps))
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
                raise RecordingError(f"无法初始化鼠标指针绘制：{exc}") from exc

        def emit(frame: np.ndarray) -> None:
            nonlocal cursor_renderer
            if cursor_renderer is None:
                sink.write(frame)
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
        deadline = started_at + duration_seconds
        last_grab_at = started_at
        while frame_count < total_frames:
            target = started_at + frame_count * frame_period
            if target >= deadline:
                break
            now = time.perf_counter()
            if stop_event is not None and stop_event.is_set():
                stopped_early = True
                break
            if now < target:
                wait_seconds = min(target - now, deadline - now)
                if stop_event is not None:
                    if stop_event.wait(wait_seconds):
                        stopped_early = True
                        break
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
                    raise RecordingError("采集到的画面格式异常（需要 BGRA）。")
                background = np.ascontiguousarray(grabbed)

            emit(background)
            frame_count += 1

        if not stopped_early and background is not None and frame_count < total_frames:
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
        raise RecordingError(f"录制失败：{capture_error}") from capture_error
    if close_error is not None:
        _remove_partial_file(output_path)
        raise RecordingError(f"保存视频失败：{close_error}") from close_error

    if frame_count == 0 or background is None:
        _remove_partial_file(output_path)
        raise RecordingError("没有采集到任何画面，请重新选择录制区域。")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        _remove_partial_file(output_path)
        raise RecordingError("视频文件未生成或内容为空。")

    elapsed = min(time.perf_counter() - started_at, duration_seconds)
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
