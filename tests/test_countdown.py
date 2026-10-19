from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.countdown import CountdownOverlay


class CountdownFormatTests(unittest.TestCase):
    def test_formats_seconds_as_minutes_and_seconds(self) -> None:
        self.assertEqual(CountdownOverlay._format(40), "00:40")
        self.assertEqual(CountdownOverlay._format(10), "00:10")
        self.assertEqual(CountdownOverlay._format(9.01), "00:10")
        self.assertEqual(CountdownOverlay._format(0), "00:00")
        self.assertEqual(CountdownOverlay._format(61), "01:01")
        self.assertEqual(CountdownOverlay._format(-1), "00:00")


if __name__ == "__main__":
    unittest.main()
