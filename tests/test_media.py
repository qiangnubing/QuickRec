from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.media import build_video_encode_command


class VideoCommandTests(unittest.TestCase):
    def test_command_uses_requested_quality(self) -> None:
        command = build_video_encode_command(
            Path("ffmpeg"),
            Path("out.mp4"),
            width=1920,
            height=1080,
            fps=60,
            bitrate_kbps=12_000,
        )
        joined = " ".join(command)
        self.assertIn("-f rawvideo", joined)
        self.assertIn("-pix_fmt bgra", joined)
        self.assertIn("-s 1920x1080", joined)
        self.assertIn("-framerate 60", joined)
        self.assertIn("-c:v libx264", joined)
        self.assertIn("-b:v 12000k", joined)
        self.assertIn("-maxrate 15000k", joined)
        self.assertIn("-pix_fmt yuv420p", joined)


if __name__ == "__main__":
    unittest.main()
