from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np

from short_video_capture.capture import (
    CaptureRegion,
    RecordingError,
    record_screen,
)


class FakeSource:
    def __init__(self, region: CaptureRegion, on_grab=None) -> None:
        self.region = region
        self.on_grab = on_grab
        self.closed = False
        self.grabs = 0

    def grab(self) -> np.ndarray:
        self.grabs += 1
        frame = np.zeros((self.region.height, self.region.width, 4), dtype=np.uint8)
        frame[:, :, 3] = 255
        if self.on_grab is not None:
            self.on_grab()
        return frame

    def close(self) -> None:
        self.closed = True


class FakeWriter:
    instances: list["FakeWriter"] = []

    def __init__(self, path: Path, fps: int, region: CaptureRegion) -> None:
        self.path = path
        self.fps = fps
        self.region = region
        self.frames: list[np.ndarray] = []
        self.released = False
        self.is_open = True
        FakeWriter.instances.append(self)

    def isOpened(self) -> bool:
        return self.is_open

    def write(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())

    def release(self) -> None:
        self.released = True
        self.path.write_bytes(b"fake-mp4")


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


class RecordScreenTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeWriter.instances.clear()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_records_frames_and_pads_to_requested_length(self) -> None:
        source_holder: list[FakeSource] = []

        def source_factory(region: CaptureRegion) -> FakeSource:
            source = FakeSource(region)
            source_holder.append(source)
            return source

        output = self.directory / "录 制 测试.mp4"
        result = record_screen(
            CaptureRegion(5, 5, 101, 51),
            output,
            duration_seconds=0.12,
            fps=20,
            source_factory=source_factory,
            writer_factory=FakeWriter,
        )

        self.assertTrue(output.is_file())
        self.assertEqual(result.frame_count, 3)
        self.assertFalse(result.stopped_early)
        writer = FakeWriter.instances[-1]
        self.assertTrue(writer.released)
        self.assertEqual((writer.region.width, writer.region.height), (100, 50))
        self.assertEqual(len(writer.frames), 3)
        self.assertEqual(writer.frames[0].shape, (50, 100, 3))
        self.assertTrue(source_holder[0].closed)

    def test_stop_event_stops_early_without_padding(self) -> None:
        stop_event = threading.Event()

        def source_factory(region: CaptureRegion) -> FakeSource:
            return FakeSource(region, on_grab=stop_event.set)

        output = self.directory / "stopped.mp4"
        started = time.perf_counter()
        result = record_screen(
            CaptureRegion(0, 0, 64, 64),
            output,
            duration_seconds=5,
            fps=20,
            stop_event=stop_event,
            source_factory=source_factory,
            writer_factory=FakeWriter,
        )

        self.assertLess(time.perf_counter() - started, 1.0)
        self.assertTrue(result.stopped_early)
        self.assertEqual(result.frame_count, 1)
        self.assertEqual(len(FakeWriter.instances[-1].frames), 1)

    def test_no_frames_raises_and_removes_partial_file(self) -> None:
        stop_event = threading.Event()
        stop_event.set()
        output = self.directory / "empty.mp4"
        with self.assertRaises(RecordingError):
            record_screen(
                CaptureRegion(0, 0, 64, 64),
                output,
                duration_seconds=1,
                fps=20,
                stop_event=stop_event,
                writer_factory=FakeWriter,
            )
        self.assertFalse(output.exists())

    def test_rejects_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            record_screen(CaptureRegion(0, 0, 64, 64), self.directory / "a.mp4", 0)
        with self.assertRaises(ValueError):
            record_screen(
                CaptureRegion(0, 0, 64, 64),
                self.directory / "b.mp4",
                1,
                fps=0,
            )

    def test_refuses_to_overwrite_existing_file(self) -> None:
        output = self.directory / "existing.mp4"
        output.write_bytes(b"keep-me")
        with self.assertRaises(RecordingError):
            record_screen(
                CaptureRegion(0, 0, 64, 64),
                output,
                duration_seconds=1,
                fps=20,
                writer_factory=FakeWriter,
            )
        self.assertEqual(output.read_bytes(), b"keep-me")


if __name__ == "__main__":
    unittest.main()
