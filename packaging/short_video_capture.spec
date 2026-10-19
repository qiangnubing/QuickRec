# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 单文件打包配置。

构建命令（项目根目录）：
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean packaging\\short_video_capture.spec
或直接运行 packaging/build_exe.py。
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"
ASSETS = SRC / "short_video_capture" / "assets"
APP_NAME = "屏幕录制工具"

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[(str(ASSETS), "short_video_capture/assets")],
    hiddenimports=[
        "mss.windows",
        "soundcard.mediafoundation",
        "imageio_ffmpeg.binaries",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL",
        "cv2",
        "lxml",
        "docx",
        "pptx",
        "openpyxl",
        "pymodbus",
        "serial",
        "ttkbootstrap",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "app.ico"),
    version=str(ROOT / "packaging" / "version_info.txt"),
)
