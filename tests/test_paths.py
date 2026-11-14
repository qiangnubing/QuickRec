from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from short_video_capture.paths import (
    OutputDirectoryError,
    application_dir,
    build_output_path,
    ensure_output_dir,
    fallback_output_dir,
    format_bytes,
    normalize_output_dir,
)


class OutputDirectoryTests(unittest.TestCase):
    def test_creates_directory_with_chinese_and_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "我的 视频" / "录制"
            created = ensure_output_dir(target)
            self.assertTrue(created.is_dir())

    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(OutputDirectoryError):
            normalize_output_dir("   ")

    def test_fallback_directory_is_writable(self) -> None:
        directory = fallback_output_dir()
        self.assertTrue(directory.is_dir())

    def test_application_dir_is_project_root_in_source_mode(self) -> None:
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertEqual(application_dir(), Path(__file__).resolve().parents[1])

    def test_application_dir_follows_executable_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "工具" / "屏幕录制工具.exe"
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(application_dir(), executable.parent)


class OutputPathTests(unittest.TestCase):
    def test_uses_timestamp_and_avoids_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            when = datetime(2026, 10, 19, 8, 30, 5)
            first = build_output_path(directory, when)
            self.assertEqual(first.name, "QuickRec_2026-10-19_08-30-05.mp4")
            first.write_bytes(b"x")

            second = build_output_path(directory, when)
            self.assertEqual(second.name, "QuickRec_2026-10-19_08-30-05_2.mp4")
            second.write_bytes(b"x")
            third = build_output_path(directory, when)
            self.assertEqual(third.name, "QuickRec_2026-10-19_08-30-05_3.mp4")

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(2 * 1024 * 1024), "2.0 MB")


if __name__ == "__main__":
    unittest.main()
