"""一次完整录制：视频编码、音频采集与音视频混流。"""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .audio import AudioClip, AudioRecorder, AudioSelection
from .capture import CaptureRegion, VideoSettings, record_video
from .i18n import tr
from .media import FFmpegError, find_ffmpeg, run_ffmpeg

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordingResult:
    path: Path
    frame_count: int
    duration_seconds: float
    stopped_early: bool
    fps: int
    audio_warnings: tuple[str, ...] = ()


def build_mux_command(
    ffmpeg: Path,
    video_path: Path,
    output_path: Path,
    clips: Sequence[AudioClip],
    duration_seconds: float,
    audio_bitrate_kbps: int,
) -> list[str]:
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
    ]
    for clip in clips:
        command.extend(["-i", str(clip.path)])

    if len(clips) == 1:
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-af",
                "apad",
            ]
        )
    else:
        filters = []
        labels = []
        for index, _clip in enumerate(clips, start=1):
            label = f"a{index}"
            labels.append(f"[{label}]")
            filters.append(
                f"[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo"
                f"[{label}]"
            )
        mixed = (
            "".join(labels)
            + f"amix=inputs={len(clips)}:duration=longest:"
            "dropout_transition=0:normalize=0,"
            "alimiter=limit=0.95,apad[aout]"
        )
        filters.append(mixed)
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
            ]
        )

    command.extend(
        [
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            f"{audio_bitrate_kbps}k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            f"{duration_seconds:.3f}",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def record_session(
    region: CaptureRegion,
    output_path: Path,
    duration_seconds: float | None,
    video_settings: VideoSettings,
    audio_selection: AudioSelection,
    audio_bitrate_kbps: int,
    *,
    stop_event=None,
    on_started: Callable[[], None] | None = None,
    include_cursor: bool = True,
) -> RecordingResult:
    """执行完整录制，返回最终文件信息。"""

    if not 32 <= audio_bitrate_kbps <= 512:
        raise ValueError(
            tr(
                "音频码率必须在 32 到 512 kbps 之间。",
                "Audio bitrate must be between 32 and 512 kbps.",
            )
        )
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(
            tr(
                "目标文件已存在：{path}",
                "The target file already exists: {path}",
                path=output_path,
            )
        )
    ffmpeg = find_ffmpeg()

    try:
        temporary = tempfile.TemporaryDirectory(prefix="screen_recorder_")
    except OSError as exc:
        raise RuntimeError(
            tr(
                "无法创建临时目录：{error}",
                "Cannot create a temporary folder: {error}",
                error=exc,
            )
        ) from exc

    with temporary:
        temp_path = Path(temporary.name)
        temp_video = temp_path / "video.mp4"
        recorder = AudioRecorder(audio_selection, temp_path)
        audio_started = False
        try:
            recorder.start()
            audio_started = True

            def begin_recording() -> None:
                recorder.begin()
                if on_started is not None:
                    on_started()

            video = record_video(
                region,
                temp_video,
                duration_seconds,
                video_settings,
                stop_event=stop_event,
                on_start=begin_recording,
                include_cursor=include_cursor,
            )
            recorder.stop()
            audio_started = False
            clips = [
                clip
                for clip in recorder.clips
                if clip.path.is_file() and clip.path.stat().st_size > 44
            ]
            warnings = list(recorder.warnings)
            if audio_selection.enabled and not clips:
                warnings.append(
                    tr(
                        "没有采集到有效音频，已保存无声视频。",
                        "No usable audio was captured; saved a silent video.",
                    )
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            if clips:
                command = build_mux_command(
                    ffmpeg,
                    video.path,
                    output_path,
                    clips,
                    video.duration_seconds,
                    audio_bitrate_kbps,
                )
                run_ffmpeg(command)
            else:
                shutil.move(str(video.path), str(output_path))

            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError(
                    tr(
                        "视频文件未生成或内容为空。",
                        "The video file was not created or is empty.",
                    )
                )
            return RecordingResult(
                path=output_path,
                frame_count=video.frame_count,
                duration_seconds=video.duration_seconds,
                stopped_early=video.stopped_early,
                fps=video.fps,
                audio_warnings=tuple(warnings),
            )
        except BaseException:
            if audio_started:
                recorder.stop()
            output_path.unlink(missing_ok=True)
            raise


def format_audio_warning(warnings: Sequence[str]) -> str:
    return "\n".join(dict.fromkeys(warnings))


__all__ = [
    "FFmpegError",
    "RecordingResult",
    "build_mux_command",
    "format_audio_warning",
    "record_session",
]
