"""无需安装即可启动：``python run.py``。"""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE_DIR = Path(__file__).resolve().parent / "src"
if str(_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(_SOURCE_DIR))

from short_video_capture.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
