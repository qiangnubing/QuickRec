"""PyInstaller 单文件打包入口。

正常启动时直接进入图形界面；``--package-self-test <报告路径>`` 只供构建
流程验证冻结后的依赖、图标、屏幕采集与 FFmpeg 是否完整，普通用户无需使用。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SELF_TEST_FLAG = "--package-self-test"


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(command: list[object], *, timeout: float = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [str(part) for part in command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_creation_flags(),
        timeout=timeout,
        check=False,
    )


def _check(name: str, func: object, *, fatal: bool = True) -> dict[str, object]:
    try:
        detail = func()  # type: ignore[operator]
    except Exception as exc:  # 自检需要把所有失败收集到报告里
        return {
            "name": name,
            "ok": False,
            "fatal": fatal,
            "detail": f"{type(exc).__name__}: {exc}",
        }
    return {"name": name, "ok": True, "fatal": fatal, "detail": detail}


def _imports() -> dict[str, object]:
    import cffi
    import mss
    import numpy
    import soundcard
    import tkinter

    import short_video_capture.app  # noqa: F401
    import short_video_capture.audio  # noqa: F401
    import short_video_capture.capture  # noqa: F401
    import short_video_capture.dialogs  # noqa: F401
    import short_video_capture.media  # noqa: F401
    import short_video_capture.pipeline  # noqa: F401
    import short_video_capture.region_selector  # noqa: F401
    import short_video_capture.tray  # noqa: F401
    import short_video_capture.win32  # noqa: F401

    return {
        "python": sys.version.split()[0],
        "tk": tkinter.TkVersion,
        "numpy": numpy.__version__,
        "mss": mss.__version__,
        "soundcard": soundcard.__file__,
        "cffi": cffi.__version__,
    }


def _assets() -> dict[str, object]:
    from short_video_capture.app import ICON_ICO, ICON_PNG

    sizes = {"ico": ICON_ICO.stat().st_size, "png": ICON_PNG.stat().st_size}
    if min(sizes.values()) <= 0:
        raise RuntimeError("应用图标文件为空")
    return {"ico": str(ICON_ICO), "png": str(ICON_PNG), "bytes": sizes}


def _ffmpeg() -> dict[str, object]:
    from short_video_capture.media import find_ffmpeg

    ffmpeg = find_ffmpeg()
    version = _run([ffmpeg, "-version"], timeout=30)
    if version.returncode != 0:
        detail = version.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg -version 失败：{detail}")
    first_line = version.stdout.decode("utf-8", errors="replace").splitlines()[0]

    encoders = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=30)
    encoder_text = (encoders.stdout + encoders.stderr).decode("utf-8", errors="replace")
    for codec in ("libx264", "aac"):
        if codec not in encoder_text:
            raise RuntimeError(f"内置 FFmpeg 缺少编码器：{codec}")

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "自检.mp4"
        encoded = _run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=64x64:d=0.2",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(target),
            ],
            timeout=60,
        )
        if encoded.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            detail = encoded.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"内置 FFmpeg 无法编码 H.264：{detail}")
        video_bytes = target.stat().st_size

    return {
        "path": str(ffmpeg),
        "version": first_line,
        "codecs": ["libx264", "aac"],
        "test_video_bytes": video_bytes,
    }


def _tk() -> dict[str, object]:
    import tkinter as tk

    from short_video_capture.app import ICON_ICO, ICON_PNG

    root = tk.Tk()
    try:
        root.withdraw()
        root.iconbitmap(str(ICON_ICO))
        image = tk.PhotoImage(file=str(ICON_PNG))
        root.update_idletasks()
        return {
            "patchlevel": root.tk.call("info", "patchlevel"),
            "icon_width": image.width(),
            "icon_height": image.height(),
        }
    finally:
        root.destroy()


def _screen() -> dict[str, object]:
    import mss

    from short_video_capture.win32 import virtual_screen_rect

    rect = virtual_screen_rect()
    with mss.MSS() as sct:
        shot = sct.grab({"left": 0, "top": 0, "width": 1, "height": 1})
    return {
        "left": rect.left,
        "top": rect.top,
        "width": rect.width,
        "height": rect.height,
        "pixel": list(shot.pixel(0, 0)),
    }


def _audio() -> dict[str, object]:
    from short_video_capture.audio import AUDIO_IMPORT_ERROR, list_audio_devices

    if AUDIO_IMPORT_ERROR:
        raise RuntimeError(AUDIO_IMPORT_ERROR)
    devices = list_audio_devices()
    if devices.error:
        raise RuntimeError(devices.error)
    return {
        "system": len(devices.system),
        "microphones": len(devices.microphones),
        "default_system": devices.default_system_id,
        "default_microphone": devices.default_microphone_id,
    }


def _frozen() -> dict[str, object]:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("当前进程不是 PyInstaller 冻结程序")
    return {
        "executable": sys.executable,
        "meipass": getattr(sys, "_MEIPASS", None),
    }


def _run_package_self_test(report_path: str | None) -> int:
    checks = [
        _check("frozen", _frozen),
        _check("imports", _imports),
        _check("assets", _assets),
        _check("tk", _tk),
        _check("screen", _screen),
        _check("ffmpeg", _ffmpeg),
        # 声卡枚举依赖运行机器的硬件与驱动，失败只作为警告。
        _check("audio", _audio, fatal=False),
    ]
    failed = [check for check in checks if not check["ok"] and check["fatal"]]
    payload = {
        "ok": not failed,
        "executable": sys.executable,
        "checks": checks,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if report_path:
        target = Path(report_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        print(text)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == SELF_TEST_FLAG:
        return _run_package_self_test(args[1] if len(args) > 1 else None)

    from short_video_capture.app import main as run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
