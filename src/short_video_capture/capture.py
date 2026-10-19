"""屏幕采集与视频编码核心逻辑，不依赖界面。"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import mss
import numpy as np

LOGGER = logging.getLogger(__name__)
DEFAULT_FPS = 20
MIN_REGION_SIZE = 2


class RecordingError(RuntimeError):
    """录制过程失败。"""


class ScreenSource(Protocol):
    def grab(self) -> np.ndarray: ...

    def close(self) -> None: ...


class VideoSink(Protocol):
    def isOpened(self) -> bool: ...

    def write(self, frame: np.ndarray) -> None: ...

    def release(self) -> None: ...


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
        """H.264/MPEG-4 编码器通常只接受偶数宽高。"""

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
class RecordingResult:
    path: Path
    frame_count: int
    duration_seconds: float
    stopped_early: bool


class _MssSource:
    def __init__(self, region: CaptureRegion) -> None:
        self._screen = mss.MSS()
        self._monitor = region.as_monitor()

    def grab(self) -> np.ndarray:
        return np.asarray(self._screen.grab(self._monitor))

    def close(self) -> None:
        self._screen.close()


def _open_writer(path: Path, fps: int, region: CaptureRegion) -> VideoSink:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(
        str(path), fourcc, float(fps), (region.width, region.height)
    )
    return writer


SourceFactory = Callable[[CaptureRegion], ScreenSource]
WriterFactory = Callable[[Path, int, CaptureRegion], VideoSink]


def record_screen(
    region: CaptureRegion,
    output_path: Path,
    duration_seconds: float,
    *,
    fps: int = DEFAULT_FPS,
    stop_event: threading.Event | None = None,
    source_factory: SourceFactory | None = None,
    writer_factory: WriterFactory | None = None,
) -> RecordingResult:
    """录制指定区域，阻塞直到录制完成或 ``stop_event`` 被设置。

    ``source_factory`` 和 ``writer_factory`` 仅用于测试注入；正常调用无需传入。
    """

    if not 0 < duration_seconds <= 3600:
        raise ValueError("录制时长必须在 0 到 3600 秒之间。")
    if not 1 <= fps <= 60:
        raise ValueError("帧率必须在 1 到 60 之间。")

    output_path = Path(output_path)
    if output_path.exists():
        raise RecordingError(f"目标视频文件已存在：{output_path}")
    region = region.even_sized()
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RecordingError(f"无法创建视频保存目录：{output_path.parent}\n{exc}") from exc

    source_factory = source_factory or _MssSource
    writer_factory = writer_factory or _open_writer
    total_frames = max(1, math.ceil(duration_seconds * fps))
    frame_period = 1.0 / fps

    source: ScreenSource | None = None
    writer: VideoSink | None = None
    frame_count = 0
    last_frame: np.ndarray | None = None
    stopped_early = False
    capture_error: BaseException | None = None
    release_error: BaseException | None = None
    started_at = time.perf_counter()

    try:
        source = source_factory(region)
        writer = writer_factory(output_path, fps, region)
        if not writer.isOpened():
            raise RecordingError("无法初始化视频编码器，请检查 OpenCV 是否安装完整。")

        deadline = started_at + duration_seconds
        next_frame_at = started_at
        while True:
            now = time.perf_counter()
            if stop_event is not None and stop_event.is_set():
                stopped_early = True
                break
            if now >= deadline:
                break
            if now < next_frame_at:
                wait_seconds = min(next_frame_at - now, deadline - now)
                if stop_event is not None:
                    if stop_event.wait(wait_seconds):
                        stopped_early = True
                        break
                else:
                    time.sleep(wait_seconds)
                continue

            grabbed = source.grab()
            if grabbed is None or grabbed.ndim != 3 or grabbed.shape[2] < 3:
                raise RecordingError("采集到的画面格式异常。")
            if grabbed.shape[2] == 4:
                frame = cv2.cvtColor(grabbed, cv2.COLOR_BGRA2BGR)
            else:
                frame = grabbed[:, :, :3]
            writer.write(frame)
            last_frame = frame
            frame_count += 1
            next_frame_at += frame_period

        # 若编码偶发变慢，用最后一帧补齐固定时长，避免视频比所选时长短。
        if not stopped_early and frame_count < total_frames and last_frame is not None:
            for _ in range(total_frames - frame_count):
                writer.write(last_frame)
                frame_count += 1
    except BaseException as exc:
        capture_error = exc
    finally:
        if writer is not None:
            try:
                writer.release()
            except BaseException as exc:
                release_error = exc
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
    if release_error is not None:
        _remove_partial_file(output_path)
        raise RecordingError(f"保存视频失败：{release_error}") from release_error

    if frame_count == 0 or last_frame is None:
        _remove_partial_file(output_path)
        raise RecordingError("没有采集到任何画面，请重新选择录制区域。")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        _remove_partial_file(output_path)
        raise RecordingError("视频文件未生成或内容为空。")

    elapsed = min(time.perf_counter() - started_at, duration_seconds)
    LOGGER.info(
        "录制完成：%s，%d 帧，%.1f 秒%s",
        output_path,
        frame_count,
        elapsed,
        "（提前停止）" if stopped_early else "",
    )
    return RecordingResult(
        path=output_path,
        frame_count=frame_count,
        duration_seconds=elapsed,
        stopped_early=stopped_early,
    )


def _remove_partial_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("无法删除未完成的视频文件：%s", path)
