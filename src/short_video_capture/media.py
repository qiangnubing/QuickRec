"""FFmpeg 视频编码与字幕无关的媒体工具。"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import threading
from collections import deque
from collections.abc import Sequence
from ctypes import wintypes
from functools import lru_cache
from pathlib import Path

import numpy as np

from .i18n import tr

LOGGER = logging.getLogger(__name__)
DEFAULT_VIDEO_ENCODER = "libx264"
_MIN_HW_ENCODER_SIZE = 128
_HW_ENCODER_CANDIDATES = ("h264_nvenc", "h264_qsv", "h264_amf")
_VALID_VIDEO_ENCODERS = (DEFAULT_VIDEO_ENCODER, *_HW_ENCODER_CANDIDATES)
_PIPE_BUFFER_BYTES = 1024 * 1024


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
        raise FFmpegError(
            tr(
                "指定的 FFmpeg 不存在：{path}",
                "The specified FFmpeg does not exist: {path}",
                path=candidate,
            )
        )

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
        tr(
            "未找到 FFmpeg。请安装 imageio-ffmpeg"
            "（python -m pip install imageio-ffmpeg），或把 ffmpeg.exe 加入 PATH。",
            "FFmpeg was not found. Install imageio-ffmpeg "
            "(python -m pip install imageio-ffmpeg), or add ffmpeg.exe to PATH.",
        )
    )


def creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


def _x264_thread_count() -> int:
    # 限制 x264 线程数可避免高核数 CPU 上的过度线程同步开销。
    return min(4, max(2, (os.cpu_count() or 4) // 2))


def _filter_thread_count() -> int:
    return min(2, max(1, os.cpu_count() or 2))


def _display_adapter_candidates() -> tuple[str, ...]:
    """根据当前显示适配器厂商优先排序可能的硬件 H.264 编码器。"""

    if os.name != "nt":
        return ()
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumDisplayDevicesW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(_DISPLAY_DEVICEW),
            wintypes.DWORD,
        ]
        user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    except (AttributeError, OSError):
        return ()

    matched: list[str] = []
    index = 0
    while index < 32:
        device = _DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(_DISPLAY_DEVICEW)
        if not user32.EnumDisplayDevicesW(None, index, ctypes.byref(device), 0):
            break
        index += 1
        signature = f"{device.DeviceID} {device.DeviceString}".upper()
        if "VEN_10DE" in signature or "NVIDIA" in signature:
            matched.append("h264_nvenc")
        elif "VEN_8086" in signature or "INTEL" in signature:
            matched.append("h264_qsv")
        elif (
            "VEN_1002" in signature
            or "AMD" in signature
            or "RADEON" in signature
            or "ATI" in signature
        ):
            matched.append("h264_amf")

    return tuple(
        candidate for candidate in _HW_ENCODER_CANDIDATES if candidate in matched
    )


def _encoder_codec_args(
    encoder: str, bitrate_kbps: int, maxrate: int, bufsize: int
) -> list[str]:
    if encoder == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-tune",
            "hq",
            "-rc",
            "vbr",
            "-b:v",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{maxrate}k",
            "-bufsize",
            f"{bufsize}k",
            "-pix_fmt",
            "nv12",
        ]
    if encoder == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-preset",
            "veryfast",
            "-b:v",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{maxrate}k",
            "-bufsize",
            f"{bufsize}k",
            "-pix_fmt",
            "nv12",
        ]
    if encoder == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-usage",
            "transcoding",
            "-quality",
            "speed",
            "-rc",
            "vbr_peak",
            "-b:v",
            f"{bitrate_kbps}k",
            "-maxrate",
            f"{maxrate}k",
            "-bufsize",
            f"{bufsize}k",
            "-pix_fmt",
            "nv12",
        ]
    return [
        "-c:v",
        DEFAULT_VIDEO_ENCODER,
        "-preset",
        "veryfast",
        "-threads",
        str(_x264_thread_count()),
        "-b:v",
        f"{bitrate_kbps}k",
        "-maxrate",
        f"{maxrate}k",
        "-bufsize",
        f"{bufsize}k",
        "-pix_fmt",
        "yuv420p",
    ]


@lru_cache(maxsize=4)
def _detect_hardware_encoder(ffmpeg: Path) -> str | None:
    """探测当前环境可用的硬件 H.264 编码器，失败时返回 ``None``。"""

    candidates = _display_adapter_candidates()
    if not candidates:
        return None
    for encoder in candidates:
        codec_args = _encoder_codec_args(encoder, 4000, 5000, 8000)
        probe_cmd = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=128x128:d=0.04",
            "-frames:v",
            "1",
            *codec_args,
            "-f",
            "null",
            "-",
        ]
        try:
            completed = subprocess.run(
                probe_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=creation_flags(),
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            LOGGER.info("启用硬件视频编码器：%s", encoder)
            return encoder
    return None


def select_video_encoder(ffmpeg: Path, width: int, height: int) -> str:
    """为当前录制分辨率选择最省 CPU 的 H.264 编码器。"""

    override = os.environ.get("QUICKREC_VIDEO_ENCODER", "").strip().lower()
    if override in _VALID_VIDEO_ENCODERS:
        return override
    if width < _MIN_HW_ENCODER_SIZE or height < _MIN_HW_ENCODER_SIZE:
        return DEFAULT_VIDEO_ENCODER
    return _detect_hardware_encoder(ffmpeg) or DEFAULT_VIDEO_ENCODER


def build_video_encode_command(
    ffmpeg: Path,
    output_path: Path,
    width: int,
    height: int,
    fps: int,
    bitrate_kbps: int,
    *,
    encoder: str = DEFAULT_VIDEO_ENCODER,
    input_pix_fmt: str = "bgra",
) -> list[str]:
    """构造从 stdin 接收 BGRA/BGR0 原始帧、编码为 H.264 MP4 的命令。"""

    maxrate = max(bitrate_kbps + 1, int(bitrate_kbps * 1.25))
    bufsize = max(maxrate, bitrate_kbps * 2)
    codec_args = _encoder_codec_args(encoder, bitrate_kbps, maxrate, bufsize)
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        input_pix_fmt,
        "-s",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "pipe:0",
        "-an",
        "-filter_threads",
        str(_filter_thread_count()),
        "-sws_flags",
        "fast_bilinear",
        *codec_args,
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
        self._output_path = output_path
        self._width = width
        self._height = height
        self._fps = fps
        self._bitrate_kbps = bitrate_kbps
        self._closed = False
        self._frames_written = 0
        self._fallback_attempted = False
        self._stderr: deque[str] = deque(maxlen=80)
        self._ffmpeg = find_ffmpeg()
        self._encoder = select_video_encoder(self._ffmpeg, width, height)
        self._start_process(self._encoder)

    def _start_process(self, encoder: str) -> None:
        self._encoder = encoder
        self._stderr.clear()
        command = build_video_encode_command(
            self._ffmpeg,
            self._output_path,
            self._width,
            self._height,
            self._fps,
            self._bitrate_kbps,
            encoder=encoder,
            input_pix_fmt="bgr0",
        )
        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=_PIPE_BUFFER_BYTES,
                creationflags=creation_flags(),
            )
        except OSError as exc:
            raise FFmpegError(
                tr(
                    "无法启动视频编码进程：{error}",
                    "Cannot start the video encoder: {error}",
                    error=exc,
                )
            ) from exc
        self._reader = threading.Thread(
            target=self._read_stderr, name="ffmpeg-stderr", daemon=True
        )
        self._reader.start()

    def _restart_with_software_encoder(self) -> None:
        self._fallback_attempted = True
        LOGGER.warning("硬件编码器 %s 写入首帧失败，回退到 libx264", self._encoder)
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
        self._reader.join(timeout=1)
        self._start_process(DEFAULT_VIDEO_ENCODER)

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
                tr(
                    "视频帧尺寸异常：需要 {width}×{height} BGRA，实际为 {shape}",
                    "Unexpected frame size: expected {width}×{height} BGRA, got {shape}",
                    width=self._width,
                    height=self._height,
                    shape=frame.shape,
                )
            )
        if self._closed:
            raise FFmpegError(
                tr("视频编码器已经关闭。", "The video encoder is already closed.")
            )
        stream = self._process.stdin
        if stream is None:
            raise FFmpegError(
                self._diagnostic(
                    tr("视频编码器管道不可用。", "The video encoder pipe is unavailable.")
                )
            )
        contiguous = frame if frame.flags.c_contiguous else np.ascontiguousarray(frame)
        payload = memoryview(contiguous.ravel())
        try:
            stream.write(payload)
        except (BrokenPipeError, OSError) as exc:
            if (
                self._frames_written == 0
                and self._encoder != DEFAULT_VIDEO_ENCODER
                and not self._fallback_attempted
            ):
                self._restart_with_software_encoder()
                retry_stream = self._process.stdin
                if retry_stream is not None:
                    try:
                        retry_stream.write(payload)
                        self._frames_written += 1
                        return
                    except (BrokenPipeError, OSError):
                        pass
            raise FFmpegError(
                self._diagnostic(
                    tr(
                        "视频编码进程意外退出。",
                        "The video encoder exited unexpectedly.",
                    )
                )
            ) from exc
        self._frames_written += 1

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
            raise FFmpegError(
                tr(
                    "视频编码超时，文件可能不完整。",
                    "Video encoding timed out; the file may be incomplete.",
                )
            ) from exc
        finally:
            self._reader.join(timeout=2)
        if return_code != 0:
            raise FFmpegError(
                self._diagnostic(
                    tr("视频编码失败。", "Video encoding failed.")
                )
            )

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
        raise FFmpegError(
            tr(
                "FFmpeg 处理超时（{seconds:g} 秒）。",
                "FFmpeg timed out after {seconds:g} seconds.",
                seconds=timeout,
            )
        ) from exc
    except OSError as exc:
        raise FFmpegError(
            tr("无法启动 FFmpeg：{error}", "Cannot start FFmpeg: {error}", error=exc)
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise FFmpegError(
            tr(
                "FFmpeg 处理失败（退出码 {code}）：\n{detail}",
                "FFmpeg failed (exit code {code}):\n{detail}",
                code=completed.returncode,
                detail=detail,
            )
        )
