from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from short_video_capture.config import (
    AppSettings,
    SettingsStore,
    parse_audio_bitrate_kbps,
    parse_duration,
    parse_fps,
    parse_video_bitrate_mbps,
    settings_path,
)
from short_video_capture.paths import application_dir


class ValidationTests(unittest.TestCase):
    def test_duration_bounds(self) -> None:
        self.assertEqual(parse_duration("1"), 1)
        self.assertEqual(parse_duration("3599"), 3599)
        self.assertEqual(parse_duration("3600"), 3600)
        with self.assertRaises(ValueError):
            parse_duration("0")
        with self.assertRaises(ValueError):
            parse_duration("3601")
        with self.assertRaises(ValueError):
            parse_duration("1.5")
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_fps_bounds(self) -> None:
        self.assertEqual(parse_fps("5"), 5)
        self.assertEqual(parse_fps("60"), 60)
        with self.assertRaises(ValueError):
            parse_fps("4")
        with self.assertRaises(ValueError):
            parse_fps("abc")

    def test_video_bitrate(self) -> None:
        self.assertEqual(parse_video_bitrate_mbps("8"), 8.0)
        self.assertEqual(parse_video_bitrate_mbps("6.5 Mbps"), 6.5)
        with self.assertRaises(ValueError):
            parse_video_bitrate_mbps("0.4")
        with self.assertRaises(ValueError):
            parse_video_bitrate_mbps("51")
        with self.assertRaises(ValueError):
            parse_video_bitrate_mbps("nan")

    def test_audio_bitrate(self) -> None:
        self.assertEqual(parse_audio_bitrate_kbps("192"), 192)
        self.assertEqual(parse_audio_bitrate_kbps("128 kbps"), 128)
        with self.assertRaises(ValueError):
            parse_audio_bitrate_kbps("31")
        with self.assertRaises(ValueError):
            parse_audio_bitrate_kbps("513")


class SettingsPathTests(unittest.TestCase):
    def test_settings_file_lives_next_to_app(self) -> None:
        self.assertEqual(settings_path(), application_dir() / "settings.json")


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / "config" / "settings.json"
        self.store = SettingsStore(self.path)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_round_trip(self) -> None:
        settings = AppSettings(
            duration_seconds=95,
            output_dir=r"C:\我的视频\录制",
            fps=60,
            video_bitrate_mbps=12.5,
            audio_bitrate_kbps=256,
            system_audio_enabled=True,
            microphone_enabled=True,
            system_audio_device="speaker-id",
            microphone_device="mic-id",
            record_cursor=False,
        )
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded, settings)
        self.assertFalse(loaded.record_cursor)

    def test_max_duration_and_language_round_trip(self) -> None:
        settings = AppSettings(duration_seconds=0, language="en")
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded.duration_seconds, 0)
        self.assertEqual(loaded.language, "en")

    def test_unknown_language_falls_back_to_chinese(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"language": "fr", "duration_seconds": 10}),
            encoding="utf-8",
        )
        self.assertEqual(self.store.load().language, "zh")

    def test_invalid_duration_falls_back(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "duration_seconds": 99999,
                    "output_dir": " C:/videos ",
                    "fps": 100,
                    "video_bitrate_mbps": 100,
                    "audio_bitrate_kbps": 1000,
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded.duration_seconds, 10)
        self.assertEqual(loaded.output_dir, "C:/videos")
        self.assertEqual(loaded.fps, 30)
        self.assertEqual(loaded.video_bitrate_mbps, 8.0)
        self.assertEqual(loaded.audio_bitrate_kbps, 192)

    def test_corrupt_file_falls_back(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded, AppSettings())

    def test_bool_is_not_accepted_as_number(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "duration_seconds": True,
                    "output_dir": 123,
                    "fps": True,
                    "video_bitrate_mbps": False,
                    "audio_bitrate_kbps": True,
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(self.store.load(), AppSettings())

    def test_unconfigured_audio_defaults_to_system_sound(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps(
                {
                    "duration_seconds": 10,
                    "system_audio_enabled": False,
                    "microphone_enabled": False,
                    "system_audio_device": "",
                    "microphone_device": "",
                }
            ),
            encoding="utf-8",
        )
        self.assertTrue(self.store.load().system_audio_enabled)

    def test_explicit_audio_off_is_preserved(self) -> None:
        settings = AppSettings(
            system_audio_enabled=False,
            microphone_enabled=False,
            system_audio_device="speaker-id",
            microphone_device="mic-id",
        )
        self.store.save(settings)
        loaded = self.store.load()
        self.assertFalse(loaded.system_audio_enabled)
        self.assertFalse(loaded.microphone_enabled)

    def test_explicit_audio_off_without_devices_is_preserved_after_save(self) -> None:
        settings = AppSettings(system_audio_enabled=False, microphone_enabled=False)
        self.store.save(settings)
        loaded = self.store.load()
        self.assertFalse(loaded.system_audio_enabled)
        self.assertFalse(loaded.microphone_enabled)


if __name__ == "__main__":
    unittest.main()
