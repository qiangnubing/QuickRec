from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from short_video_capture.config import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / "config" / "settings.json"
        self.store = SettingsStore(self.path)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_round_trip(self) -> None:
        settings = AppSettings(duration_seconds=40, output_dir=r"C:\我的视频\录制")
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded.duration_seconds, 40)
        self.assertEqual(loaded.output_dir, r"C:\我的视频\录制")

    def test_invalid_duration_falls_back(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"duration_seconds": 99, "output_dir": " C:/videos "}),
            encoding="utf-8",
        )
        loaded = self.store.load()
        self.assertEqual(loaded.duration_seconds, 10)
        self.assertEqual(loaded.output_dir, "C:/videos")

    def test_corrupt_file_falls_back(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json", encoding="utf-8")
        loaded = self.store.load()
        self.assertEqual(loaded, AppSettings())

    def test_bool_is_not_accepted_as_duration(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            json.dumps({"duration_seconds": True, "output_dir": 123}),
            encoding="utf-8",
        )
        self.assertEqual(self.store.load(), AppSettings())


if __name__ == "__main__":
    unittest.main()
