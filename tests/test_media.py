from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.media import (
    FFmpegVideoSink,
    build_video_encode_command,
    select_video_encoder,
)


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

    def test_hardware_encoder_commands_use_nv12_and_bgr0(self) -> None:
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            command = build_video_encode_command(
                Path("ffmpeg"),
                Path("out.mp4"),
                width=1280,
                height=720,
                fps=30,
                bitrate_kbps=6_000,
                encoder=encoder,
                input_pix_fmt="bgr0",
            )
            joined = " ".join(command)
            self.assertIn("-pix_fmt bgr0", joined)
            self.assertIn(f"-c:v {encoder}", joined)
            self.assertIn("-pix_fmt nv12", joined)
            self.assertIn("-sws_flags fast_bilinear", joined)

    def test_select_video_encoder_uses_libx264_for_tiny_regions(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUICKREC_VIDEO_ENCODER", None)
            with patch(
                "short_video_capture.media._detect_hardware_encoder",
                return_value="h264_amf",
            ):
                self.assertEqual(
                    select_video_encoder(Path("ffmpeg"), 64, 64), "libx264"
                )
                self.assertEqual(
                    select_video_encoder(Path("ffmpeg"), 1920, 1080), "h264_amf"
                )

    def test_select_video_encoder_respects_override(self) -> None:
        with patch.dict(os.environ, {"QUICKREC_VIDEO_ENCODER": "libx264"}):
            self.assertEqual(
                select_video_encoder(Path("ffmpeg"), 1920, 1080), "libx264"
            )

    def test_ffmpeg_sink_falls_back_to_libx264_on_first_frame_pipe_error(self) -> None:
        import numpy as np

        class BrokenFirstPipe:
            def write(self, _data: object) -> int:
                raise BrokenPipeError("init failed")

            def close(self) -> None:
                pass

        class WorkingPipe:
            def __init__(self) -> None:
                self.writes = 0
                self.closed = False

            def write(self, data: object) -> int:
                self.writes += 1
                return len(data)  # type: ignore[arg-type]

            def close(self) -> None:
                self.closed = True

        class FakeProcess:
            def __init__(self, stdin: object, returncode: int | None = None) -> None:
                self.stdin = stdin
                self.stderr = None
                self._returncode = returncode

            def poll(self) -> int | None:
                return self._returncode

            def kill(self) -> None:
                self._returncode = -9

            def wait(self, timeout: float | None = None) -> int:
                return 0 if self._returncode is None else self._returncode

        working_pipe = WorkingPipe()
        processes = [
            FakeProcess(BrokenFirstPipe(), returncode=1),
            FakeProcess(working_pipe, returncode=None),
        ]
        commands: list[list[str]] = []

        def fake_popen(cmd, **_kwargs):
            commands.append(cmd)
            return processes[len(commands) - 1]

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "fallback.mp4"
            with (
                patch("short_video_capture.media.find_ffmpeg", return_value=Path("ffmpeg")),
                patch(
                    "short_video_capture.media.select_video_encoder",
                    return_value="h264_amf",
                ),
                patch("short_video_capture.media.subprocess.Popen", side_effect=fake_popen),
            ):
                sink = FFmpegVideoSink(out, width=128, height=128, fps=30, bitrate_kbps=4000)
                frame = np.zeros((128, 128, 4), dtype=np.uint8)
                sink.write(frame)
                sink.close()

        self.assertEqual(len(commands), 2)
        self.assertIn("h264_amf", commands[0])
        self.assertIn("libx264", commands[1])
        self.assertEqual(working_pipe.writes, 1)
        self.assertTrue(working_pipe.closed)


if __name__ == "__main__":
    unittest.main()
