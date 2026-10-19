"""启动入口。

优先使用当前 Python；若当前解释器缺少音视频依赖、而项目内存在可用的
``.venv``，则自动切换到该虚拟环境启动，避免主界面出现“音频组件未安装”。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SOURCE_DIR = _ROOT / "src"
_RELAUNCH_ENV = "SHORT_VIDEO_CAPTURE_RELAUNCHED"


def _runtime_dependencies_available() -> bool:
    try:
        import imageio_ffmpeg  # noqa: F401
        import soundcard  # noqa: F401
    except Exception:
        return False
    return True


def _venv_pythonw() -> Path | None:
    candidate = _ROOT / ".venv" / "Scripts" / "pythonw.exe"
    return candidate if candidate.is_file() else None


def _already_in_project_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == (_ROOT / ".venv").resolve()
    except OSError:
        return False


def _maybe_relaunch_in_venv() -> None:
    """当前解释器缺依赖时，切换到项目内 .venv 的无控制台解释器。"""

    if os.environ.get(_RELAUNCH_ENV) == "1":
        return
    if _runtime_dependencies_available():
        return
    if _already_in_project_venv():
        return
    pythonw = _venv_pythonw()
    if pythonw is None:
        return

    environment = os.environ.copy()
    environment[_RELAUNCH_ENV] = "1"
    try:
        subprocess.Popen(
            [str(pythonw), str(Path(__file__).resolve()), *sys.argv[1:]],
            cwd=str(_ROOT),
            env=environment,
            close_fds=True,
        )
    except OSError:
        return
    raise SystemExit(0)


if str(_SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(_SOURCE_DIR))

_maybe_relaunch_in_venv()

from short_video_capture.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
