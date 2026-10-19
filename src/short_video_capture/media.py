"""FFmpeg 视频编码与字幕无关的媒体工具。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    """FFmpeg 不可用或执行失败。"""


@lru_cache(maxsize=1)
def find_ffmpeg() -> Path:
    """优先使用 imageio-ffmpeg 自带的完整构建，其次使用 PATH 中的 FFmpeg。"""

    override = os.environ.get("SCREEN_RECORDER_FFMPEG") or os.environ.get("FFMPEG_BINARY")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise FFmpegError(f"指定的 FFmpeg 不存在：{candidate}")

    try:
        import imageio_ffmpeg

        bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if bundled.is_file():
            return bundled
    except (ImportError, OSError, RuntimeError):
        pass

    on_path = shutil.which("ffmpeg")
    if on_path:
        return Path(on_path)

    raise FFmpegError(
        "未找到 FFmpeg。请安装 imageio-ffmpeg（python -m pip install imageio-ffmpeg），"
        "或把 ffmpeg.exe 加入 PATH。"
    )


def creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def build_video_encode_command(
    ffmpeg: Path,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    bitrate_kbps: int,
) -> list[str]:
    """构造从 stdin 接收 BGRA 原始帧、编码为 H.264 MP4 的命令。"""

    maxrate = max(bitrate_kbps + 1, int(bitrate_kbps * 1.25))
    bufsize = max(maxrate, bitrate_kbps * 2)
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgra",
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        f"{bitrate_kbps}k",
        "-maxrate",
        f"{maxrate}k",
        "-bufsize",
        f"{bufsize}k",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_path),
    ]


class FFmpegVideoSink:
    """把 numpy BGRA 帧写入 ffmpeg 的 stdin，由 H.264 实时编码。"""

    def __init__(
        self,
        output_path: Path,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int,
    ) -> None:
        self._width = width
        self._height = height
        self._closed = False
        self._stderr: deque[str] = deque(maxlen=80)
        command = build_video_encode_command(
            find_ffmpeg(), output_path, width, height, fps, bitrate_kbps
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creation_flags(),
            )
        except OSError as exc:
            raise FFmpegError(f"无法启动视频编码进程：{exc}") from exc
        self._reader = threading.Thread(
            target=self._read_stderr, name="ffmpeg-stderr", daemon=True
        )
        self._reader.start()

    def _read_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        try:
            for raw_line in stream:
                self._stderr.append(raw_line.decode("utf-8", errors="replace").strip())
        except (OSError, ValueError):
            pass

    def isOpened(self) -> bool:
        return self._process.poll() is None

    def write(self, frame: np.ndarray) -> None:
        if frame.shape != (self._height, self._width, 4):
            raise FFmpegError(
                f"视频帧尺寸异常：需要 {self._width}×{self._height} BGRA，实际为 {frame.shape}"
            )
        if self._closed:
            raise FFmpegError("视频编码器已经关闭。")
        stream = self._process.stdin
        if stream is None:
            raise FFmpegError(self._diagnostic("视频编码器管道不可用。"))
        try:
            stream.write(frame.tobytes())
        except (BrokenPipeError, OSError) as exc:
            raise FFmpegError(self._diagnostic("视频编码进程意外退出。")) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = self._process.stdin
        if stream is not None:
            try:
                stream.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            return_code = self._process.wait(timeout=180)
        except subprocess.TimeoutExpired as exc:
            self._process.kill()
            self._process.wait(timeout=10)
            raise FFmpegError("视频编码超时，文件可能不完整。") from exc
        finally:
            self._reader.join(timeout=2)
        if return_code != 0:
            raise FFmpegError(self._diagnostic("视频编码失败。"))

    def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    def _diagnostic(self, prefix: str) -> str:
        detail = "\n".join(line for line in self._stderr if line)
        if detail:
            return f"{prefix}\n{detail}"
        return prefix


def run_ffmpeg(args: Sequence[str], timeout: float = 300) -> None:
    """运行一次 ffmpeg，失败时抛出带 stderr 摘要的异常。"""

    completed = None
    try:
        completed = subprocess.run(
            [str(arg) for arg in args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags(),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"FFmpeg 处理超时（{timeout:g} 秒）。") from exc
    except OSError as exc:
        raise FFmpegError(f"无法启动 FFmpeg：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FFmpegError(f"FFmpeg 处理失败（退出码 {completed.returncode}）：\n{detail}")
