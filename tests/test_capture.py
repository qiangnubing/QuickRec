from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from short_video_capture.capture import (
    CaptureRegion,
    RecordingError,
    VideoSettings,
    record_video,
)


class FakeSource:
    def __init__(
        self, region: CaptureRegion, on_grab=None, grab_delay: float = 0.0
    ) -> None:
        self.region = region
        self.on_grab = on_grab
        self.grab_delay = grab_delay
        self.closed = False
        self.grabs = 0

    def grab(self) -> np.ndarray:
        self.grabs += 1
        if self.grab_delay:
            time.sleep(self.grab_delay)
        frame = np.zeros((self.region.height, self.region.width, 4), dtype=np.uint8)
        frame[:, :, 3] = 255
        frame[0, 0, 0] = self.grabs % 251
        if self.on_grab is not None:
            self.on_grab()
        return frame

    def close(self) -> None:
        self.closed = True


class FakeSink:
    instances: list["FakeSink"] = []

    def __init__(
        self,
        path: Path,
        region: CaptureRegion,
        fps: int,
        bitrate_kbps: int,
    ) -> None:
        self.path = path
        self.region = region
        self.fps = fps
        self.bitrate_kbps = bitrate_kbps
        self.frames: list[np.ndarray] = []
        self.closed = False
        FakeSink.instances.append(self)

    def write(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())

    def close(self) -> None:
        self.closed = True
        self.path.write_bytes(b"fake-mp4")

    def abort(self) -> None:
        self.closed = True


class CaptureRegionTests(unittest.TestCase):
    def test_rejects_tiny_region(self) -> None:
        with self.assertRaises(ValueError):
            CaptureRegion(0, 0, 1, 100)

    def test_even_sized_region(self) -> None:
        region = CaptureRegion(10, 20, 101, 51).even_sized()
        self.assertEqual((region.width, region.height), (100, 50))
        self.assertEqual(
            region.as_monitor(),
            {"left": 10, "top": 20, "width": 100, "height": 50},
        )


class VideoSettingsTests(unittest.TestCase):
    def test_valid_settings(self) -> None:
        settings = VideoSettings(30, 8_000)
        self.assertEqual(settings.bitrate_mbps, 8.0)

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            VideoSettings(fps=4)
        with self.assertRaises(ValueError):
            VideoSettings(fps=61)
        with self.assertRaises(ValueError):
            VideoSettings(bitrate_kbps=100)
        with self.assertRaises(ValueError):
            VideoSettings(bitrate_kbps=60_000)


class RecordVideoTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSink.instances.clear()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_records_frames_with_requested_settings(self) -> None:
        source_holder: list[FakeSource] = []

        def source_factory(region: CaptureRegion) -> FakeSource:
            source = FakeSource(region)
            source_holder.append(source)
            return source

        output = self.directory / "录 制 测试.mp4"
        result = record_video(
            CaptureRegion(5, 5, 101, 51),
            output,
            duration_seconds=0.12,
            settings=VideoSettings(20, 4_000),
            source_factory=source_factory,
            sink_factory=FakeSink,
            include_cursor=False,
        )

        self.assertTrue(output.is_file())
        self.assertEqual(result.frame_count, 3)
        self.assertAlmostEqual(result.duration_seconds, 0.15)
        self.assertFalse(result.stopped_early)
        sink = FakeSink.instances[-1]
        self.assertTrue(sink.closed)
        self.assertEqual((sink.region.width, sink.region.height), (100, 50))
        self.assertEqual((sink.fps, sink.bitrate_kbps), (20, 4_000))
        self.assertEqual(len(sink.frames), 3)
        self.assertEqual(sink.frames[0].shape, (50, 100, 4))
        self.assertTrue(source_holder[0].closed)

    def test_cursor_renderer_draws_on_each_output_frame(self) -> None:
        class FakeCursorRenderer:
            instance = None

            def __init__(self) -> None:
                self.draw_calls = 0
                self.closed = False
                FakeCursorRenderer.instance = self

            def draw(self, frame: np.ndarray, region: CaptureRegion) -> np.ndarray:
                self.draw_calls += 1
                frame[:, :, 0] = 17
                return frame

            def close(self) -> None:
                self.closed = True

        output = self.directory / "cursor.mp4"
        with patch(
            "short_video_capture.capture.CursorRenderer", FakeCursorRenderer
        ):
            result = record_video(
                CaptureRegion(0, 0, 64, 64),
                output,
                duration_seconds=0.1,
                settings=VideoSettings(10, 2_000),
                source_factory=FakeSource,
                sink_factory=FakeSink,
                include_cursor=True,
            )
        renderer = FakeCursorRenderer.instance
        self.assertIsNotNone(renderer)
        self.assertEqual(renderer.draw_calls, result.frame_count)
        self.assertTrue(renderer.closed)

    def test_high_fps_does_not_freeze_at_end(self) -> None:
        def source_factory(region: CaptureRegion) -> FakeSource:
            return FakeSource(region, grab_delay=0.03)

        output = self.directory / "high-fps.mp4"
        started = time.perf_counter()
        result = record_video(
            CaptureRegion(0, 0, 64, 64),
            output,
            duration_seconds=0.2,
            settings=VideoSettings(60, 8_000),
            source_factory=source_factory,
            sink_factory=FakeSink,
            include_cursor=False,
        )

        self.assertEqual(result.frame_count, 12)
        self.assertLess(time.perf_counter() - started, 0.8)
        frames = FakeSink.instances[-1].frames
        self.assertEqual(len(frames), 12)
        self.assertGreater(len({frame.tobytes() for frame in frames}), 1)

    def test_stop_event_stops_early_without_padding(self) -> None:
        stop_event = threading.Event()

        def source_factory(region: CaptureRegion) -> FakeSource:
            return FakeSource(region, on_grab=stop_event.set)

        output = self.directory / "stopped.mp4"
        started = time.perf_counter()
        result = record_video(
            CaptureRegion(0, 0, 64, 64),
            output,
            duration_seconds=5,
            settings=VideoSettings(20, 4_000),
            stop_event=stop_event,
            source_factory=source_factory,
            sink_factory=FakeSink,
            include_cursor=False,
        )

        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(result.stopped_early)
        self.assertEqual(result.frame_count, 1)
        self.assertEqual(len(FakeSink.instances[-1].frames), 1)

    def test_max_duration_runs_until_stop(self) -> None:
        stop_event = threading.Event()

        def source_factory(region: CaptureRegion) -> FakeSource:
            return FakeSource(region, on_grab=stop_event.set)

        output = self.directory / "max.mp4"
        result = record_video(
            CaptureRegion(0, 0, 64, 64),
            output,
            duration_seconds=None,
            settings=VideoSettings(20, 4_000),
            stop_event=stop_event,
            source_factory=source_factory,
            sink_factory=FakeSink,
            include_cursor=False,
        )

        self.assertTrue(output.is_file())
        self.assertTrue(result.stopped_early)
        self.assertEqual(result.frame_count, 1)
        self.assertEqual(len(FakeSink.instances[-1].frames), 1)

    def test_no_frames_raises_and_removes_partial_file(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        output = self.directory / "empty.mp4"
        with self.assertRaises(RecordingError):
            record_video(
                CaptureRegion(0, 0, 64, 64),
                output,
                duration_seconds=1,
                settings=VideoSettings(20, 4_000),
                stop_event=stop_event,
                sink_factory=FakeSink,
            include_cursor=False,
            )
        self.assertFalse(output.exists())

    def test_rejects_invalid_duration(self) -> None:
        with self.assertRaises(ValueError):
            record_video(
                CaptureRegion(0, 0, 64, 64),
                self.directory / "a.mp4",
                duration_seconds=0,
                settings=VideoSettings(),
                sink_factory=FakeSink,
            include_cursor=False,
            )

    def test_refuses_to_overwrite_existing_file(self) -> None:
        output = self.directory / "existing.mp4"
        output.write_bytes(b"keep-me")
        with self.assertRaises(RecordingError):
            record_video(
                CaptureRegion(0, 0, 64, 64),
                output,
                duration_seconds=1,
                settings=VideoSettings(),
                sink_factory=FakeSink,
            include_cursor=False,
            )
        self.assertEqual(output.read_bytes(), b"keep-me")


if __name__ == "__main__":
    unittest.main()
