"""构建 Windows 单文件可执行程序。

用法（项目根目录）：
    .venv\\Scripts\\python.exe packaging\\build_exe.py

脚本会依次运行单元测试、调用 PyInstaller 构建 ``dist\\QuickRec.exe``，
并启动成品执行内置的打包自检，确认依赖、图标、屏幕采集和 FFmpeg 均可使用。
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

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "short_video_capture.spec"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP_NAME = "QuickRec"
EXE = DIST / f"{APP_NAME}.exe"
SELF_TEST_FLAG = "--package-self-test"


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


def _ensure_dependencies() -> None:
    if os.name != "nt":
        raise SystemExit("单文件 exe 只能在 Windows 上构建。")

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


def _smoke_test() -> None:
    print("启动成品执行打包自检……", flush=True)
    report = BUILD / "package-self-test.json"
    report.unlink(missing_ok=True)
    started = time.monotonic()
    process = subprocess.Popen([str(EXE), SELF_TEST_FLAG, str(report)], cwd=ROOT)
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
            "打包自检在 120 秒内未结束。若当前运行在受限沙箱中，单文件程序可能无法"
            "把运行库解压到临时目录；可加 --skip-smoke-test 跳过自检，"
            "并在普通桌面双击 exe 完成验证。"
        )
    elapsed = time.monotonic() - started

    if not report.is_file():
        raise SystemExit(
            "打包自检没有生成报告，成品可能启动失败。若当前运行在受限沙箱中，"
            "可加 --skip-smoke-test 跳过自检，并在普通桌面双击 exe 完成验证。"
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


def _build(*, skip_tests: bool, skip_smoke_test: bool) -> None:
    _ensure_dependencies()

    if not skip_tests:
        completed = _run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        )
        if completed.returncode != 0:
            raise SystemExit("单元测试未通过，已停止打包。")

    _remove_tree(BUILD)
    _remove_tree(DIST)
    BUILD.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    # 避免向用户配置目录写入 PyInstaller 缓存（受限环境可能不可写）。
    environment["PYINSTALLER_CONFIG_DIR"] = str(BUILD / "pyinstaller-cache")
    completed = _run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        env=environment,
    )
    if completed.returncode != 0:
        raise SystemExit(f"PyInstaller 构建失败，退出码 {completed.returncode}。")

    if not EXE.is_file():
        raise SystemExit(f"没有找到构建产物：{EXE}")
    extras = sorted(item.name for item in DIST.iterdir() if item != EXE)
    if extras:
        raise SystemExit(f"输出目录不是单文件，存在额外文件：{extras}")

    if not skip_smoke_test:
        _smoke_test()

    print("\n构建完成：", flush=True)
    print(f"  文件：{EXE}", flush=True)
    print(f"  大小：{EXE.stat().st_size / (1024 * 1024):.1f} MB", flush=True)
    print(f"  SHA-256：{_sha256(EXE)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建单文件 Windows exe")
    parser.add_argument("--skip-tests", action="store_true", help="跳过单元测试")
    parser.add_argument(
        "--skip-smoke-test",
        action="store_true",
        help="跳过成品自检（仅建议在无法启动图形界面时使用）",
    )
    args = parser.parse_args(argv)
    _build(skip_tests=args.skip_tests, skip_smoke_test=args.skip_smoke_test)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
