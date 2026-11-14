from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from short_video_capture.i18n import (
    get_language,
    language_name,
    normalize_language,
    set_language,
    tr,
)


class I18nTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_language("zh")

    def test_defaults_to_chinese(self) -> None:
        set_language("zh")
        self.assertEqual(get_language(), "zh")
        self.assertEqual(tr("中文", "English"), "中文")

    def test_english_selection(self) -> None:
        self.assertEqual(set_language("en"), "en")
        self.assertEqual(tr("中文", "English"), "English")
        self.assertEqual(language_name("en"), "English")

    def test_unknown_language_falls_back(self) -> None:
        self.assertEqual(normalize_language("fr"), "zh")
        self.assertEqual(normalize_language(None), "zh")

    def test_formats_placeholders(self) -> None:
        set_language("en")
        self.assertEqual(tr("{n} 秒", "{n} s", n=10), "10 s")


if __name__ == "__main__":
    unittest.main()
