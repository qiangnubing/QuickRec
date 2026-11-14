"""本地设置读写与输入校验。"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .i18n import normalize_language, tr

LOGGER = logging.getLogger(__name__)
DURATION_OPTIONS = (10, 20, 40, 60)
MAX_DURATION_SENTINEL = 0
AUDIO_BITRATE_OPTIONS = (64, 96, 128, 160, 192, 256, 320)
DEFAULT_DURATION = 10
MIN_DURATION = 1
MAX_DURATION = 86400
MIN_FPS = 5
MAX_FPS = 60
MIN_VIDEO_BITRATE_MBPS = 0.5
MAX_VIDEO_BITRATE_MBPS = 50.0
MIN_AUDIO_BITRATE_KBPS = 32
MAX_AUDIO_BITRATE_KBPS = 512
DEFAULT_FPS = 30
DEFAULT_VIDEO_BITRATE_MBPS = 8.0
DEFAULT_AUDIO_BITRATE_KBPS = 192
SETTINGS_VERSION = 3


def parse_duration(text: str) -> int:
    value = text.strip()
    if not value:
        raise ValueError(tr("请输入录制时长。", "Enter a recording duration."))
    try:
        seconds = int(value)
    except ValueError as exc:
        raise ValueError(tr("录制时长必须是整数秒。", "Duration must be a whole number of seconds.")) from exc
    if not MIN_DURATION <= seconds <= MAX_DURATION:
        raise ValueError(
            tr(
                "录制时长需在 {low} 到 {high} 秒之间。",
                "Duration must be between {low} and {high} seconds.",
                low=MIN_DURATION,
                high=MAX_DURATION,
            )
        )
    return seconds


def parse_fps(text: str) -> int:
    value = text.strip()
    if not value:
        raise ValueError(tr("请输入帧率。", "Enter a frame rate."))
    try:
        fps = int(value)
    except ValueError as exc:
        raise ValueError(tr("帧率必须是整数。", "Frame rate must be a whole number.")) from exc
    if not MIN_FPS <= fps <= MAX_FPS:
        raise ValueError(
            tr(
                "帧率需在 {low} 到 {high} FPS 之间。",
                "Frame rate must be between {low} and {high} FPS.",
                low=MIN_FPS,
                high=MAX_FPS,
            )
        )
    return fps


def parse_video_bitrate_mbps(text: str) -> float:
    value = text.strip().lower().removesuffix("mbps").strip()
    if not value:
        raise ValueError(tr("请输入视频码率。", "Enter a video bitrate."))
    try:
        bitrate = float(value)
    except ValueError as exc:
        raise ValueError(tr("视频码率必须是数字。", "Video bitrate must be a number.")) from exc
    if not math.isfinite(bitrate):
        raise ValueError(tr("视频码率必须是有限数字。", "Video bitrate must be a finite number."))
    if not MIN_VIDEO_BITRATE_MBPS <= bitrate <= MAX_VIDEO_BITRATE_MBPS:
        raise ValueError(
            tr(
                "视频码率需在 {low:g} 到 {high:g} Mbps 之间。",
                "Video bitrate must be between {low:g} and {high:g} Mbps.",
                low=MIN_VIDEO_BITRATE_MBPS,
                high=MAX_VIDEO_BITRATE_MBPS,
            )
        )
    return bitrate


def parse_audio_bitrate_kbps(text: str) -> int:
    value = text.strip().lower().removesuffix("kbps").strip()
    if not value:
        raise ValueError(tr("请输入音频码率。", "Enter an audio bitrate."))
    try:
        bitrate = int(value)
    except ValueError as exc:
        raise ValueError(tr("音频码率必须是整数。", "Audio bitrate must be a whole number.")) from exc
    if not MIN_AUDIO_BITRATE_KBPS <= bitrate <= MAX_AUDIO_BITRATE_KBPS:
        raise ValueError(
            tr(
                "音频码率需在 {low} 到 {high} kbps 之间。",
                "Audio bitrate must be between {low} and {high} kbps.",
                low=MIN_AUDIO_BITRATE_KBPS,
                high=MAX_AUDIO_BITRATE_KBPS,
            )
        )
    return bitrate


@dataclass(slots=True)
class AppSettings:
    duration_seconds: int = DEFAULT_DURATION
    output_dir: str = ""
    fps: int = DEFAULT_FPS
    video_bitrate_mbps: float = DEFAULT_VIDEO_BITRATE_MBPS
    audio_bitrate_kbps: int = DEFAULT_AUDIO_BITRATE_KBPS
    system_audio_enabled: bool = True
    microphone_enabled: bool = False
    system_audio_device: str = ""
    microphone_device: str = ""
    record_cursor: bool = True
    language: str = "zh"

    def normalized(self) -> "AppSettings":
        if self.duration_seconds == MAX_DURATION_SENTINEL:
            duration = MAX_DURATION_SENTINEL
        else:
            duration = _int_in_range(
                self.duration_seconds, DEFAULT_DURATION, MIN_DURATION, MAX_DURATION
            )
        fps = _int_in_range(self.fps, DEFAULT_FPS, MIN_FPS, MAX_FPS)
        video_bitrate = _float_in_range(
            self.video_bitrate_mbps,
            DEFAULT_VIDEO_BITRATE_MBPS,
            MIN_VIDEO_BITRATE_MBPS,
            MAX_VIDEO_BITRATE_MBPS,
        )
        audio_bitrate = _int_in_range(
            self.audio_bitrate_kbps,
            DEFAULT_AUDIO_BITRATE_KBPS,
            MIN_AUDIO_BITRATE_KBPS,
            MAX_AUDIO_BITRATE_KBPS,
        )
        system_audio_device = self.system_audio_device.strip()
        microphone_device = self.microphone_device.strip()
        return AppSettings(
            duration_seconds=duration,
            output_dir=self.output_dir.strip(),
            fps=fps,
            video_bitrate_mbps=video_bitrate,
            audio_bitrate_kbps=audio_bitrate,
            system_audio_enabled=bool(self.system_audio_enabled),
            microphone_enabled=bool(self.microphone_enabled),
            system_audio_device=system_audio_device,
            microphone_device=microphone_device,
            record_cursor=bool(self.record_cursor),
            language=normalize_language(self.language),
        )


def _int_in_range(value: object, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if low <= value <= high else default


def _float_in_range(
    value: object, default: float, low: float, high: float
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    numeric = float(value)
    if not math.isfinite(numeric) or not low <= numeric <= high:
        return default
    return numeric


def settings_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "QuickRec" / "settings.json"


def legacy_settings_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "ShortVideoCapture" / "settings.json"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_path()
        self._legacy_path = None if path is not None else legacy_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        path = self._path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if self._legacy_path is None or not self._legacy_path.is_file():
                return AppSettings()
            try:
                data = json.loads(self._legacy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("读取设置失败，使用默认值：%s", exc)
                return AppSettings()
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("读取设置失败，使用默认值：%s", exc)
            return AppSettings()
        if not isinstance(data, dict):
            return AppSettings()

        loaded = AppSettings(
            duration_seconds=_as_int(data.get("duration_seconds"), DEFAULT_DURATION),
            output_dir=_as_str(data.get("output_dir")),
            fps=_as_int(data.get("fps"), DEFAULT_FPS),
            video_bitrate_mbps=_as_float(
                data.get("video_bitrate_mbps"), DEFAULT_VIDEO_BITRATE_MBPS
            ),
            audio_bitrate_kbps=_as_int(
                data.get("audio_bitrate_kbps"), DEFAULT_AUDIO_BITRATE_KBPS
            ),
            system_audio_enabled=_as_bool(data.get("system_audio_enabled"), True),
            microphone_enabled=_as_bool(data.get("microphone_enabled"), False),
            system_audio_device=_as_str(data.get("system_audio_device")),
            microphone_device=_as_str(data.get("microphone_device")),
            record_cursor=_as_bool(data.get("record_cursor"), True),
            language=normalize_language(data.get("language")),
        ).normalized()
        schema_version = _as_int(data.get("schema_version"), 1)
        if (
            schema_version < 2
            and not loaded.system_audio_enabled
            and not loaded.microphone_enabled
            and not loaded.system_audio_device
            and not loaded.microphone_device
        ):
            # 旧版本在缺少音频依赖时可能写入“两路都关闭、无设备”的配置，
            # 这更可能是失败残留而不是有意选择；升级后恢复默认系统声音。
            loaded.system_audio_enabled = True
        return loaded

    def save(self, settings: AppSettings) -> None:
        normalized = settings.normalized()
        payload = asdict(normalized)
        payload["schema_version"] = SETTINGS_VERSION
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as exc:
            LOGGER.warning("保存设置失败：%s", exc)


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""
