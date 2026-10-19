"""输出目录、文件名和 Windows 已知文件夹处理。"""

from __future__ import annotations

import ctypes
import os
import secrets
import subprocess
import tempfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

_FILE_PREFIX = "屏幕录制"


class OutputDirectoryError(RuntimeError):
    """输出目录不可用。"""


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


# FOLDERID_Videos: {18989B1D-99B5-455B-841C-AB7C74E4DDFC}
_FOLDERID_VIDEOS = _GUID(
    0x18989B1D,
    0x99B5,
    0x455B,
    (ctypes.c_ubyte * 8)(0x84, 0x1C, 0xAB, 0x7C, 0x74, 0xE4, 0xDD, 0xFC),
)


def default_videos_dir() -> Path:
    """返回 Windows“视频”文件夹，失败时退回用户目录下的 Videos。"""

    if os.name == "nt":
        try:
            return _known_folder(_FOLDERID_VIDEOS)
        except OSError:
            pass
    return Path.home() / "Videos"


def _known_folder(folder_id: _GUID) -> Path:
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    path_ptr = ctypes.c_wchar_p()

    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    result = shell32.SHGetKnownFolderPath(
        ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr)
    )
    if result != 0 or not path_ptr.value:
        raise OSError("无法读取 Windows 视频文件夹")
    try:
        return Path(path_ptr.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(path_ptr, ctypes.c_void_p))


def normalize_output_dir(raw_path: str | os.PathLike[str]) -> Path:
    """展开环境变量并返回绝对路径，但不创建目录。"""

    text = os.fspath(raw_path).strip().strip('"')
    if not text:
        raise OutputDirectoryError("保存路径不能为空。")
    try:
        return Path(os.path.expandvars(os.path.expanduser(text))).resolve()
    except OSError as exc:
        raise OutputDirectoryError(f"保存路径无效：{exc}") from exc


def ensure_output_dir(raw_path: str | os.PathLike[str]) -> Path:
    """创建并验证输出目录可写。"""

    directory = normalize_output_dir(raw_path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OutputDirectoryError(f"无法创建保存目录：{directory}\n{exc}") from exc
    if not directory.is_dir():
        raise OutputDirectoryError(f"保存路径不是文件夹：{directory}")

    _probe_writable(directory)
    return directory


def _probe_writable(directory: Path) -> None:
    """用一次快速的文件创建验证写权限。

    Windows 的受限令牌可能让 ``os.access`` 返回错误结果；而
    ``tempfile.NamedTemporaryFile`` 在只读/沙箱目录中可能长时间阻塞。
    这里直接调用 CreateFileW，权限不足时会立即失败。
    """

    if os.name != "nt":
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix=".write_test_", delete=True):
                pass
        except OSError as exc:
            raise OutputDirectoryError(f"保存目录不可写：{directory}\n{exc}") from exc
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]

    probe = directory / f".screen_recorder_write_{os.getpid()}_{secrets.token_hex(4)}"
    handle = create_file(
        str(probe),
        0x40000000,  # GENERIC_WRITE
        0,
        None,
        1,  # CREATE_NEW
        0x100 | 0x04000000,  # TEMPORARY | DELETE_ON_CLOSE
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle in (None, invalid_handle):
        error = ctypes.get_last_error()
        detail = ctypes.FormatError(error) if error else "权限不足"
        raise OutputDirectoryError(f"保存目录不可写：{directory}\n{detail}")
    close_handle(handle)
    try:
        probe.unlink(missing_ok=True)
    except OSError:
        pass


def fallback_output_dir() -> Path:
    """当默认视频文件夹不可写时，返回一个可用的项目内输出目录。"""

    candidates = [
        Path(__file__).resolve().parents[2] / "output",
        Path.cwd() / "output",
        Path(tempfile.gettempdir()) / "屏幕录制",
    ]
    seen: set[str] = set()
    errors: list[str] = []
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return ensure_output_dir(candidate)
        except OutputDirectoryError as exc:
            errors.append(str(exc))
    detail = "\n".join(errors)
    raise OutputDirectoryError(f"找不到可写的保存目录。\n{detail}")


def build_output_path(directory: Path, when: datetime | None = None) -> Path:
    """生成不会覆盖已有文件的 mp4 路径。"""

    timestamp = (when or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    stem = f"{_FILE_PREFIX}_{timestamp}"
    candidate = directory / f"{stem}.mp4"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}.mp4"
        counter += 1
    return candidate


def format_bytes(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def open_folder(path: Path) -> None:
    """在资源管理器中打开目录。"""

    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])
