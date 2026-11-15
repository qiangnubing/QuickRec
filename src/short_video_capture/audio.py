"""Windows WASAPI 系统环回与麦克风采集。"""

from __future__ import annotations

import logging
import threading
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .i18n import tr

LOGGER = logging.getLogger(__name__)
SAMPLE_RATE = 48_000
CHANNELS = 2
BLOCK_FRAMES = 4_800  # 100 ms
SOURCE_GAIN = 0.9

try:
    import soundcard as _soundcard
except Exception as _audio_import_error:  # pragma: no cover - 取决于安装环境
    _soundcard = None
    AUDIO_IMPORT_ERROR: str | None = str(_audio_import_error)
else:
    AUDIO_IMPORT_ERROR = None


class AudioRecordingError(RuntimeError):
    """音频设备不可用或采集启动失败。"""


@dataclass(frozen=True, slots=True)
class AudioDevice:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class AudioDevices:
    system: tuple[AudioDevice, ...]
    microphones: tuple[AudioDevice, ...]
    default_system_id: str | None
    default_microphone_id: str | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AudioSelection:
    system_enabled: bool = False
    system_device_id: str | None = None
    microphone_enabled: bool = False
    microphone_device_id: str | None = None

    @property
    def enabled(self) -> bool:
        return self.system_enabled or self.microphone_enabled


@dataclass(frozen=True, slots=True)
class AudioClip:
    kind: str
    path: Path
    channels: int
    sample_rate: int

    @property
    def label(self) -> str:
        return _source_name(self.kind)


def list_audio_devices() -> AudioDevices:
    """列出系统输出（用于环回）和真实麦克风。"""

    if _soundcard is None:
        return AudioDevices(
            (),
            (),
            None,
            None,
            tr(
                "未安装 soundcard，无法录制音频：{error}",
                "soundcard is not installed, so audio cannot be recorded: {error}",
                error=AUDIO_IMPORT_ERROR,
            ),
        )
    try:
        system = tuple(
            AudioDevice(id=str(speaker.id), name=speaker.name or tr("默认输出设备", "Default output"))
            for speaker in _soundcard.all_speakers()
        )
        microphones = tuple(
            AudioDevice(
                id=str(microphone.id),
                name=microphone.name or tr("默认麦克风", "Default microphone"),
            )
            for microphone in _soundcard.all_microphones(include_loopback=False)
        )
        default_speaker = _soundcard.default_speaker()
        default_microphone = _soundcard.default_microphone()
    except Exception as exc:  # soundcard 内部为 cffi/COM 异常
        LOGGER.warning("枚举音频设备失败：%s", exc)
        return AudioDevices(
            (),
            (),
            None,
            None,
            tr(
                "枚举音频设备失败：{error}",
                "Cannot enumerate audio devices: {error}",
                error=exc,
            ),
        )
    return AudioDevices(
        system=system,
        microphones=microphones,
        default_system_id=str(default_speaker.id) if default_speaker else None,
        default_microphone_id=(
            str(default_microphone.id) if default_microphone else None
        ),
    )


def unique_device_labels(devices: tuple[AudioDevice, ...]) -> list[tuple[str, str]]:
    """生成用于下拉框的唯一显示文本，返回 ``(label, device_id)``。"""

    totals = Counter(device.name for device in devices)
    seen: Counter[str] = Counter()
    labels: list[tuple[str, str]] = []
    for device in devices:
        seen[device.name] += 1
        if totals[device.name] > 1:
            label = f"{device.name} ({seen[device.name]})"
        else:
            label = device.name
        labels.append((label, device.id))
    return labels


class AudioRecorder:
    """同时录制系统环回和麦克风，分别写入临时 WAV。"""

    def __init__(
        self,
        selection: AudioSelection,
        directory: Path,
        *,
        sample_rate: int = SAMPLE_RATE,
        block_frames: int = BLOCK_FRAMES,
    ) -> None:
        self._selection = selection
        self._directory = directory
        self._sample_rate = sample_rate
        self._block_frames = block_frames
        self._begin = threading.Event()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._threads: list[threading.Thread] = []
        self._clips: list[AudioClip] = []
        self._warnings: list[str] = []
        self._lock = threading.Lock()
        self._pending = 0

    @property
    def clips(self) -> tuple[AudioClip, ...]:
        return tuple(self._clips)

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(self._warnings)

    def start(self) -> None:
        if not self._selection.enabled:
            return
        if _soundcard is None:
            raise AudioRecordingError(
                tr(
                    "未安装 soundcard，无法录制音频。请先执行 python -m pip install -e .",
                    "soundcard is not installed. Run python -m pip install -e . first.",
                )
            )
        specs: list[tuple[str, str | None, bool]] = []
        if self._selection.system_enabled:
            specs.append(("system", self._selection.system_device_id, True))
        if self._selection.microphone_enabled:
            specs.append(("microphone", self._selection.microphone_device_id, False))
        if not specs:
            return
        for kind, device_id, _loopback in specs:
            if not device_id:
                raise AudioRecordingError(
                    tr(
                        "没有选择{source}设备。",
                        "No {source} device was selected.",
                        source=_source_name(kind),
                    )
                )

        self._directory.mkdir(parents=True, exist_ok=True)
        self._pending = len(specs)
        self._ready.clear()
        self._stop.clear()
        for kind, device_id, loopback in specs:
            target = self._directory / f"{kind}.wav"
            thread = threading.Thread(
                target=self._capture,
                args=(kind, device_id, loopback, target),
                name=f"audio-{kind}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

        if not self._ready.wait(timeout=6):
            self.stop()
            raise AudioRecordingError(
                tr(
                    "音频设备启动超时，请检查设备是否被其他程序占用。",
                    "Audio device startup timed out. Check whether another app is using it.",
                )
            )
        with self._lock:
            errors = list(self._warnings)
        if errors:
            self.stop()
            raise AudioRecordingError("\n".join(errors))

    def begin(self) -> None:
        self._begin.set()

    def stop(self) -> None:
        self._stop.set()
        self._begin.set()
        for thread in self._threads:
            thread.join(timeout=6)
            if thread.is_alive():
                LOGGER.warning("音频线程未按时退出：%s", thread.name)
        self._threads.clear()

    def _notify_ready(self) -> None:
        with self._lock:
            self._pending -= 1
            if self._pending <= 0:
                self._ready.set()

    def _capture(
        self,
        kind: str,
        device_id: str,
        loopback: bool,
        target: Path,
    ) -> None:
        ready_reported = False
        frames_written = 0
        try:
            device = _soundcard.get_microphone(device_id, include_loopback=loopback)
            if device is None:
                raise AudioRecordingError(
                    tr(
                        "找不到{source}设备。",
                        "Cannot find the {source} device.",
                        source=_source_name(kind),
                    )
                )
            if loopback and not bool(getattr(device, "isloopback", True)):
                raise AudioRecordingError(
                    tr(
                        "所选输出设备不支持系统声音环回。",
                        "The selected output device does not support loopback.",
                    )
                )

            with device.recorder(
                samplerate=self._sample_rate,
                channels=CHANNELS,
                blocksize=self._block_frames,
            ) as recorder:
                with wave.open(str(target), "wb") as wav_file:
                    wav_file.setnchannels(CHANNELS)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(self._sample_rate)
                    self._notify_ready()
                    ready_reported = True
                    if not self._begin.wait(timeout=15) or self._stop.is_set():
                        return
                    while not self._stop.is_set():
                        data = recorder.record(numframes=self._block_frames)
                        if data is None or len(data) == 0:
                            continue
                        frames = self._normalize_frames(data)
                        pcm = np.clip(
                            frames * (SOURCE_GAIN * 32767.0), -32767.0, 32767.0
                        ).astype(np.int16)
                        wav_file.writeframes(memoryview(pcm.ravel()))
                        frames_written += len(frames)
        except Exception as exc:
            if not self._stop.is_set():
                message = tr(
                    "{source}录制失败：{error}",
                    "{source} recording failed: {error}",
                    source=_source_name(kind),
                    error=exc,
                )
                LOGGER.warning(message)
                with self._lock:
                    self._warnings.append(message)
        finally:
            if not ready_reported:
                self._notify_ready()
            if frames_written == 0:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            else:
                with self._lock:
                    self._clips.append(
                        AudioClip(
                            kind=kind,
                            path=target,
                            channels=CHANNELS,
                            sample_rate=self._sample_rate,
                        )
                    )

    def _normalize_frames(self, data: np.ndarray) -> np.ndarray:
        frames = np.asarray(data, dtype=np.float32)
        if frames.ndim == 1:
            frames = frames[:, np.newaxis]
        if frames.ndim != 2:
            raise AudioRecordingError(
                tr("音频数据格式异常。", "Unexpected audio data format.")
            )
        if frames.shape[1] == 1:
            frames = np.repeat(frames, 2, axis=1)
        elif frames.shape[1] > 2:
            frames = frames[:, :2]
        return np.nan_to_num(frames, nan=0.0, posinf=1.0, neginf=-1.0)


def _source_name(kind: str) -> str:
    return tr("系统声音", "System audio") if kind == "system" else tr("麦克风", "Microphone")
