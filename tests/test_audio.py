from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import short_video_capture.audio as audio_module
from short_video_capture.audio import (
    AudioClip,
    AudioDevice,
    AudioSelection,
    list_audio_devices,
    unique_device_labels,
)


class DeviceLabelTests(unittest.TestCase):
    def test_duplicate_names_get_unique_suffixes(self) -> None:
        devices = (
            AudioDevice("1", "扬声器"),
            AudioDevice("2", "扬声器"),
            AudioDevice("3", "耳机"),
        )
        labels = unique_device_labels(devices)
        self.assertEqual(
            labels,
            [("扬声器 (1)", "1"), ("扬声器 (2)", "2"), ("耳机", "3")],
        )

    def test_selection_enabled(self) -> None:
        self.assertFalse(AudioSelection().enabled)
        self.assertTrue(AudioSelection(system_enabled=True).enabled)
        self.assertTrue(AudioSelection(microphone_enabled=True).enabled)


class MuxCommandTests(unittest.TestCase):
    def _clips(self, count: int) -> list[AudioClip]:
        kinds = ("system", "microphone")
        return [
            AudioClip(
                kind=kinds[index % 2],
                path=Path(f"clip{index}.wav"),
                channels=2,
                sample_rate=48_000,
            )
            for index in range(count)
        ]

    def test_single_audio_track(self) -> None:
        from short_video_capture.pipeline import build_mux_command

        command = build_mux_command(
            Path("ffmpeg"),
            Path("video.mp4"),
            Path("output.mp4"),
            self._clips(1),
            duration_seconds=12.5,
            audio_bitrate_kbps=192,
        )
        joined = " ".join(command)
        self.assertIn("-map 0:v:0", joined)
        self.assertIn("-map 1:a:0", joined)
        self.assertIn("-af apad", joined)
        self.assertIn("-t 12.500", joined)
        self.assertIn("-c:v copy", joined)
        self.assertIn("-c:a aac", joined)

    def test_two_audio_tracks_are_mixed(self) -> None:
        from short_video_capture.pipeline import build_mux_command

        command = build_mux_command(
            Path("ffmpeg"),
            Path("video.mp4"),
            Path("output.mp4"),
            self._clips(2),
            duration_seconds=3,
            audio_bitrate_kbps=256,
        )
        joined = " ".join(command)
        self.assertIn("-filter_complex", joined)
        self.assertIn("amix=inputs=2", joined)
        self.assertIn("alimiter=limit=0.95", joined)
        self.assertIn("apad[aout]", joined)
        self.assertIn("-map [aout]", joined)
        self.assertIn("-b:a 256k", joined)
        self.assertIn("-t 3.000", joined)


class _BrokenNameDevice:
    def __init__(self, device_id: str, exc: Exception, *, isloopback: bool = False) -> None:
        self.id = device_id
        self.isloopback = isloopback
        self._exc = exc

    @property
    def name(self) -> str:
        raise self._exc


class AudioEnumerationTests(unittest.TestCase):
    def test_missing_microphone_does_not_break_speakers(self) -> None:
        speaker = SimpleNamespace(id="{spk-1}", name="AMD High Definition Audio Device")
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: [speaker],
            all_microphones=lambda include_loopback=False: [],
            default_speaker=lambda: speaker,
            default_microphone=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNone(devices.error)
        self.assertEqual(
            devices.system,
            (AudioDevice("{spk-1}", "AMD High Definition Audio Device"),),
        )
        self.assertEqual(devices.microphones, ())
        self.assertEqual(devices.default_system_id, "{spk-1}")
        self.assertIsNone(devices.default_microphone_id)

    def test_missing_speaker_does_not_break_microphones(self) -> None:
        mic = SimpleNamespace(id="{mic-1}", name="USB Microphone")
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: [],
            all_microphones=lambda include_loopback=False: [mic],
            default_speaker=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
            default_microphone=lambda: mic,
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNone(devices.error)
        self.assertEqual(devices.system, ())
        self.assertEqual(devices.microphones, (AudioDevice("{mic-1}", "USB Microphone"),))
        self.assertIsNone(devices.default_system_id)
        self.assertEqual(devices.default_microphone_id, "{mic-1}")

    def test_no_audio_devices_at_all_returns_empty_without_error(self) -> None:
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: [],
            all_microphones=lambda include_loopback=False: [],
            default_speaker=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
            default_microphone=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNone(devices.error)
        self.assertEqual(devices.system, ())
        self.assertEqual(devices.microphones, ())
        self.assertIsNone(devices.default_system_id)
        self.assertIsNone(devices.default_microphone_id)

    def test_default_speaker_not_found_still_returns_speakers(self) -> None:
        speaker = SimpleNamespace(id="{spk-1}", name="Speakers")
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: [speaker],
            all_microphones=lambda include_loopback=False: [],
            default_speaker=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
            default_microphone=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80070490")),
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNone(devices.error)
        self.assertEqual(devices.system, (AudioDevice("{spk-1}", "Speakers"),))
        self.assertIsNone(devices.default_system_id)

    def test_broken_device_name_is_handled_per_device(self) -> None:
        good = SimpleNamespace(id="{spk-good}", name="Realtek Audio")
        unnamed = _BrokenNameDevice(
            "{spk-unnamed}", RuntimeError("Property was expected to be a string")
        )
        vanished = _BrokenNameDevice("{spk-gone}", RuntimeError("Error 0x80070490"))
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: [good, unnamed, vanished],
            all_microphones=lambda include_loopback=False: [],
            default_speaker=lambda: good,
            default_microphone=lambda: None,
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNone(devices.error)
        self.assertEqual(len(devices.system), 2)
        self.assertEqual(devices.system[0], AudioDevice("{spk-good}", "Realtek Audio"))
        self.assertEqual(devices.system[1].id, "{spk-unnamed}")
        self.assertTrue(bool(devices.system[1].name))

    def test_real_com_failure_on_both_sides_reports_error(self) -> None:
        fake_soundcard = SimpleNamespace(
            all_speakers=lambda: (_ for _ in ()).throw(RuntimeError("Error 0x80004005")),
            all_microphones=lambda include_loopback=False: (_ for _ in ()).throw(
                RuntimeError("Error 0x80004005")
            ),
            default_speaker=lambda: None,
            default_microphone=lambda: None,
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            devices = list_audio_devices()

        self.assertIsNotNone(devices.error)
        self.assertIn("0x80004005", devices.error or "")

    def test_resolve_recording_device_matches_by_id_without_reading_names(self) -> None:
        broken_other = _BrokenNameDevice(
            "{spk-other}", RuntimeError("Property error"), isloopback=True
        )
        target = _BrokenNameDevice(
            "{spk-target}", RuntimeError("Property error"), isloopback=True
        )
        fake_soundcard = SimpleNamespace(
            all_microphones=lambda include_loopback=False: [broken_other, target],
            get_microphone=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("should not fall back to get_microphone")
            ),
        )
        with patch.object(audio_module, "_soundcard", fake_soundcard):
            resolved = audio_module._resolve_recording_device("{spk-target}", loopback=True)

        self.assertIs(resolved, target)


if __name__ == "__main__":
    unittest.main()
