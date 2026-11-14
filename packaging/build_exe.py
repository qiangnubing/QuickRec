"""构建 Windows 可执行程序（单文件或目录模式）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe packaging\\build_exe.py --mode onedir
    .venv\\Scripts\\python.exe packaging\\build_exe.py --mode onefile

``onedir`` 生成 ``dist\\QuickRec\\`` 文件夹，首次启动无需解压，推荐直接分发；
``onefile`` 生成单个 ``dist\\QuickRec.exe``。脚本会先运行单元测试，再调用
PyInstaller，并启动成品执行内置自检，确认依赖、图标、屏幕采集和 FFmpeg
均可使用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
ONEFILE_SPEC = ROOT / "packaging" / "short_video_capture.spec"
ONEDIR_SPEC = ROOT / "packaging" / "short_video_capture_onedir.spec"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_NAME = "QuickRec"
ONEFILE_EXE = DIST / f"{APP_NAME}.exe"
ONEDIR_DIR = DIST / APP_NAME
ONEDIR_EXE = ONEDIR_DIR / f"{APP_NAME}.exe"
SELF_TEST_FLAG = "--package-self-test"

BuildMode = Literal["onefile", "onedir"]


def _print_command(command: list[object]) -> None:
    parts = []
    for part in command:
        text = str(part)
        parts.append(f'"{text}"' if " " in text else text)
    print("+ " + " ".join(parts), flush=True)


def _run(
    command: list[object],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    _print_command(command)
    return subprocess.run(
        [str(part) for part in command],
        cwd=ROOT,
        env=env,
        timeout=timeout,
        check=False,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remove_readonly(func: object, path: str, _error: object) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)  # type: ignore[operator]
    except OSError:
        print(f"警告：无法删除 {path}，已跳过。", flush=True)


def _remove_tree(path: Path) -> None:
    resolved = path.resolve()
    if ROOT.resolve() not in resolved.parents:
        raise RuntimeError(f"拒绝删除项目目录之外的路径：{resolved}")
    if resolved.exists():
        print(f"清理 {resolved}", flush=True)
        # 受限沙箱或杀毒软件可能锁定个别文件；尽量清理，不阻断构建。
        shutil.rmtree(resolved, onexc=_remove_readonly)


def _remove_file(path: Path) -> None:
    resolved = path.resolve()
    if ROOT.resolve() not in resolved.parents:
        raise RuntimeError(f"拒绝删除项目目录之外的路径：{resolved}")
    if resolved.is_file():
        print(f"清理 {resolved}", flush=True)
        try:
            resolved.unlink()
        except OSError as exc:
            print(f"警告：无法删除 {resolved}：{exc}", flush=True)


def _ensure_dependencies() -> None:
    if os.name != "nt":
        raise SystemExit("可执行程序只能在 Windows 上构建。")

    missing: list[str] = []
    for module_name in ("PyInstaller", "mss", "numpy", "soundcard", "imageio_ffmpeg"):
        try:
            __import__(module_name)
        except Exception as exc:
            missing.append(f"{module_name}: {exc}")
    if missing:
        detail = "\n".join(missing)
        raise SystemExit(
            "缺少构建依赖：\n"
            f"{detail}\n"
            "请先执行：.venv\\Scripts\\python.exe -m pip install -e . pyinstaller"
        )

    import PyInstaller

    print(
        f"构建解释器：{sys.executable}\n"
        f"Python {sys.version.split()[0]}，PyInstaller {PyInstaller.__version__}",
        flush=True,
    )


def _smoke_test(exe: Path) -> None:
    print(f"启动成品执行打包自检：{exe}", flush=True)
    report = BUILD / "package-self-test.json"
    report.unlink(missing_ok=True)
    started = time.monotonic()
    process = subprocess.Popen([str(exe), SELF_TEST_FLAG, str(report)], cwd=ROOT)
    try:
        returncode = process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        raise SystemExit(
            "打包自检在 120 秒内未结束。单文件模式在受限沙箱中可能因无法解压"
            "运行库而卡住；可改用 --mode onedir，或加 --skip-smoke-test 后"
            "到普通桌面验证。"
        )
    elapsed = time.monotonic() - started

    if not report.is_file():
        raise SystemExit(
            "打包自检没有生成报告，成品可能启动失败。单文件模式在受限沙箱中"
            "可能无法解压运行库；可改用 --mode onedir，或加 --skip-smoke-test "
            "后到普通桌面验证。"
        )
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"无法读取打包自检报告：{exc}") from exc

    print(f"自检完成，用时 {elapsed:.1f} 秒：", flush=True)
    for check in payload.get("checks", []):
        if check.get("ok"):
            status = "OK"
        elif check.get("fatal", True):
            status = "失败"
        else:
            status = "警告"
        print(f"  [{status}] {check.get('name')}: {check.get('detail')}", flush=True)

    if returncode != 0 or not payload.get("ok"):
        raise SystemExit("打包自检失败，请查看上方输出。")
    print("打包自检通过。", flush=True)


def _build(*, mode: BuildMode, skip_tests: bool, skip_smoke_test: bool) -> None:
    _ensure_dependencies()

    if mode == "onedir":
        spec = ONEDIR_SPEC
        target_exe = ONEDIR_EXE
    else:
        spec = ONEFILE_SPEC
        target_exe = ONEFILE_EXE

    if not skip_tests:
        completed = _run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        )
        if completed.returncode != 0:
            raise SystemExit("单元测试未通过，已停止打包。")

    _remove_tree(BUILD)
    if mode == "onedir":
        _remove_tree(ONEDIR_DIR)
    else:
        _remove_file(ONEFILE_EXE)
    BUILD.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    # 避免向用户配置目录写入 PyInstaller 缓存（受限环境可能不可写）。
    environment["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "pyinstaller-cache")
    completed = _run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(spec)],
        env=environment,
    )
    if completed.returncode != 0:
        raise SystemExit(f"PyInstaller 构建失败，退出码 {completed.returncode}。")

    if not target_exe.is_file():
        raise SystemExit(f"没有找到构建产物：{target_exe}")

    file_count = 0
    total_bytes = 0
    if mode == "onedir":
        internal = ONEDIR_DIR / "_internal"
        if not internal.is_dir():
            raise SystemExit(f"目录模式缺少依赖目录：{internal}")
        files = [item for item in ONEDIR_DIR.rglob("*") if item.is_file()]
        file_count = len(files)
        total_bytes = sum(item.stat().st_size for item in files)

    if not skip_smoke_test:
        _smoke_test(target_exe)

    print("\n构建完成：", flush=True)
    if mode == "onedir":
        print(f"  目录：{ONEDIR_DIR}", flush=True)
        print(f"  启动文件：{target_exe}", flush=True)
        print(f"  文件数：{file_count}", flush=True)
        print(f"  总大小：{total_bytes / (1024 * 1024):.1f} MB", flush=True)
        print("  分发时请保留整个文件夹，exe 与 _internal 必须一起复制。", flush=True)
    else:
        print(f"  文件：{target_exe}", flush=True)
        print(f"  大小：{target_exe.stat().st_size / (1024 * 1024):.1f} MB", flush=True)
    print(f"  SHA-256：{_sha256(target_exe)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="构建 Windows 可执行程序（单文件或目录模式）"
    )
    parser.add_argument(
        "--mode",
        choices=("onefile", "onedir"),
        default="onefile",
        help="onefile=单文件 exe；onedir=目录模式（推荐，启动更快）",
    )
    parser.add_argument("--skip-tests", action="store_true", help="跳过单元测试")
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="跳过成品自检（仅建议在无法启动图形界面时使用）",
    )
    args = parser.parse_args(argv)
    _build(
        mode=args.mode,
        skip_tests=args.skip_tests,
        skip_smoke_test=args.skip_smoke_test,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
