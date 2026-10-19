from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.audio import (
    AudioClip,
    AudioDevice,
    AudioSelection,
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


if __name__ == "__main__":
    unittest.main()
