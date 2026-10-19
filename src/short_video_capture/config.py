"""本地设置读写。"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

LOGGER = logging.getLogger(__name__)
DURATION_OPTIONS = (10, 20, 40)
DEFAULT_DURATION = 10


@dataclass(slots=True)
class AppSettings:
    duration_seconds: int = DEFAULT_DURATION
    output_dir: str = ""

    def normalized(self) -> "AppSettings":
        duration = self.duration_seconds
        if duration not in DURATION_OPTIONS:
            duration = DEFAULT_DURATION
        return AppSettings(duration_seconds=duration, output_dir=self.output_dir.strip())


def settings_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    return root / "ShortVideoCapture" / "settings.json"


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or settings_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> AppSettings:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return AppSettings()
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("读取设置失败，使用默认值：%s", exc)
            return AppSettings()
        if not isinstance(data, dict):
            return AppSettings()

        duration = data.get("duration_seconds", DEFAULT_DURATION)
        output_dir = data.get("output_dir", "")
        if not isinstance(duration, int) or isinstance(duration, bool):
            duration = DEFAULT_DURATION
        if not isinstance(output_dir, str):
            output_dir = ""
        return AppSettings(duration_seconds=duration, output_dir=output_dir).normalized()

    def save(self, settings: AppSettings) -> None:
        normalized = settings.normalized()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(asdict(normalized), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._path)
        except OSError as exc:
            LOGGER.warning("保存设置失败：%s", exc)
