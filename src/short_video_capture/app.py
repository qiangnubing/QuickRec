"""主界面与录制流程编排。"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox

from .capture import CaptureRegion, RecordingResult, record_screen
from .config import DURATION_OPTIONS, AppSettings, SettingsStore
from .countdown import CountdownOverlay
from .paths import (
    OutputDirectoryError,
    build_output_path,
    default_videos_dir,
    ensure_output_dir,
    format_bytes,
    open_folder,
)
from .region_selector import RegionSelector
from .tray import TrayCommand, TrayError, TrayIcon
from .win32 import enable_dpi_awareness

LOGGER = logging.getLogger(__name__)

APP_TITLE = "屏幕录制工具"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS_DIR / "app.ico"
ICON_PNG = ASSETS_DIR / "app_64.png"

_BG = "#F1F5F9"
_HEADER_BG = "#0F172A"
_CARD_BG = "#FFFFFF"
_BORDER = "#E2E8F0"
_TEXT = "#0F172A"
_MUTED = "#64748B"
_ACCENT = "#EF4444"
_ACCENT_HOVER = "#DC2626"
_INFO = "#2563EB"
_SUCCESS = "#15803D"
_WARN = "#B91C1C"

_IDLE = "idle"
_SELECTING = "selecting"
_STARTING = "starting"
_RECORDING = "recording"
_FINISHING = "finishing"

WindowEvent = tuple[str, object]


class RecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._settings_store = SettingsStore()
        settings = self._settings_store.load()
        self._initial_directory = settings.output_dir or str(default_videos_dir())

        self._state = _IDLE
        self._selector: RegionSelector | None = None
        self._countdown: CountdownOverlay | None = None
        self._tray: TrayIcon | None = None
        self._worker: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._events: queue.Queue[WindowEvent] = queue.Queue()
        self._poll_job: str | None = None
        self._pending_directory: Path | None = None
        self._output_path: Path | None = None
        self._duration_seconds = settings.duration_seconds
        self._recording_started_at = 0.0
        self._cancel_requested = False
        self._exit_when_done = False

        self.duration_var = tk.IntVar(value=settings.duration_seconds)
        self.output_var = tk.StringVar(value=self._initial_directory)
        self.status_var = tk.StringVar(value="就绪，选择时长和保存位置后开始录制。")

        self._configure_window()
        self._build_ui()
        self._fit_and_center_window()
        self._choose_duration(settings.duration_seconds)
        self._set_status("就绪，选择时长和保存位置后开始录制。", _MUTED)

    def _configure_window(self) -> None:
        self.root.title(APP_TITLE)
        self.root.configure(bg=_BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._on_tk_exception

        try:
            self.root.iconbitmap(str(ICON_ICO))
        except tk.TclError:
            LOGGER.debug("无法设置窗口图标", exc_info=True)
        try:
            self._icon_image = tk.PhotoImage(file=str(ICON_PNG))
            self.root.iconphoto(True, self._icon_image)
        except tk.TclError:
            self._icon_image = None
            LOGGER.debug("无法加载窗口 PNG 图标", exc_info=True)

    def _fit_and_center_window(self) -> None:
        self.root.update_idletasks()
        width = max(660, self.root.winfo_reqwidth())
        height = max(560, self.root.winfo_reqheight() + 8)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        height = min(height, max(520, screen_height - 90))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=_HEADER_BG)
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=_HEADER_BG)
        header_inner.pack(fill="x", padx=28, pady=20)

        if self._icon_image is not None:
            icon_label = tk.Label(
                header_inner,
                image=self._icon_image,
                bg=_HEADER_BG,
                bd=0,
            )
            icon_label.pack(side="left", padx=(0, 16))

        title_area = tk.Frame(header_inner, bg=_HEADER_BG)
        title_area.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_area,
            text="屏幕录制",
            bg=_HEADER_BG,
            fg="#F8FAFC",
            font=("Microsoft YaHei UI", 20, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_area,
            text="框选区域 · 定时录制 · 自动保存",
            bg=_HEADER_BG,
            fg="#94A3B8",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        body = tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True, padx=28, pady=20)

        duration_card = self._make_card(body)
        tk.Label(
            duration_card,
            text="录制时长",
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            duration_card,
            text="到达设定时长后自动停止，也可在托盘菜单中提前停止。",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(3, 10))

        options = tk.Frame(duration_card, bg=_CARD_BG)
        options.pack(fill="x")
        self._duration_buttons: dict[int, tk.Button] = {}
        for seconds in DURATION_OPTIONS:
            button = tk.Button(
                options,
                text=f"{seconds} 秒",
                command=lambda value=seconds: self._choose_duration(value),
                width=10,
                pady=9,
                relief="flat",
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                font=("Microsoft YaHei UI", 10, "bold"),
            )
            button.pack(side="left", padx=(0, 10))
            self._duration_buttons[seconds] = button

        output_card = self._make_card(body)
        output_header = tk.Frame(output_card, bg=_CARD_BG)
        output_header.pack(fill="x")
        tk.Label(
            output_header,
            text="保存位置",
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            output_header,
            text="打开文件夹",
            command=self._open_output_folder,
            bg=_CARD_BG,
            fg=_INFO,
            activebackground=_CARD_BG,
            activeforeground="#1D4ED8",
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right")

        path_row = tk.Frame(
            output_card,
            bg="#F8FAFC",
            highlightbackground=_BORDER,
            highlightthickness=1,
        )
        path_row.pack(fill="x", pady=(9, 6))
        self._path_entry = tk.Entry(
            path_row,
            textvariable=self.output_var,
            bg="#F8FAFC",
            fg=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            insertbackground=_TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        self._path_entry.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        tk.Button(
            path_row,
            text="浏览…",
            command=self._browse_output,
            bg="#E2E8F0",
            fg="#334155",
            activebackground="#CBD5E1",
            activeforeground=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=14,
            pady=5,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=6)

        tk.Label(
            output_card,
            text="视频以 MP4 格式保存，默认为 Windows 视频文件夹。",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x")

        self._start_button = tk.Button(
            body,
            text="开始录制",
            command=self._start_flow,
            bg=_ACCENT,
            fg="#FFFFFF",
            activebackground=_ACCENT_HOVER,
            activeforeground="#FFFFFF",
            disabledforeground="#FEE2E2",
            relief="flat",
            bd=0,
            highlightthickness=0,
            pady=12,
            cursor="hand2",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self._start_button.pack(fill="x", pady=(2, 9))
        self._bind_hover(self._start_button, _ACCENT, _ACCENT_HOVER)

        tk.Label(
            body,
            text="录制时自动隐藏到系统托盘，屏幕右下角显示倒计时。",
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack()

        self.status_label = tk.Label(
            body,
            textvariable=self.status_var,
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            wraplength=590,
        )
        self.status_label.pack(fill="x", pady=(10, 0))

        self._path_entry.bind("<FocusIn>", lambda _event: self._clear_status())
        self._path_entry.bind("<Return>", lambda _event: self._start_flow())

    def _make_card(self, parent: tk.Misc) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=_CARD_BG,
            highlightbackground=_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 14))
        inner = tk.Frame(card, bg=_CARD_BG)
        inner.pack(fill="x", padx=20, pady=15)
        return inner

    @staticmethod
    def _bind_hover(button: tk.Button, normal: str, hover: str) -> None:
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=normal))

    def _choose_duration(self, seconds: int) -> None:
        if seconds not in DURATION_OPTIONS:
            seconds = DURATION_OPTIONS[0]
        self.duration_var.set(seconds)
        self._duration_seconds = seconds
        for value, button in self._duration_buttons.items():
            if value == seconds:
                button.configure(
                    bg=_ACCENT,
                    fg="#FFFFFF",
                    activebackground=_ACCENT_HOVER,
                    activeforeground="#FFFFFF",
                )
            else:
                button.configure(
                    bg="#E2E8F0",
                    fg="#334155",
                    activebackground="#CBD5E1",
                    activeforeground=_TEXT,
                )

    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(fg=color)
        self.root.update_idletasks()

    def _clear_status(self) -> None:
        if self._state == _IDLE:
            self._set_status("就绪，选择时长和保存位置后开始录制。", _MUTED)

    def _browse_output(self) -> None:
        initial = Path(self.output_var.get()).expanduser()
        initial_dir = initial if initial.is_dir() else default_videos_dir()
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择视频保存文件夹",
            initialdir=str(initial_dir),
            mustexist=False,
        )
        if selected:
            self.output_var.set(str(Path(selected)))
            self._set_status(f"保存位置已更新：{selected}", _INFO)

    def _open_output_folder(self) -> None:
        try:
            directory = ensure_output_dir(self.output_var.get())
            open_folder(directory)
        except (OutputDirectoryError, OSError) as exc:
            self._set_status("无法打开保存文件夹。", _WARN)
            messagebox.showerror("无法打开文件夹", str(exc), parent=self.root)

    def _start_flow(self) -> None:
        if self._state != _IDLE:
            return
        try:
            directory = ensure_output_dir(self.output_var.get())
        except OutputDirectoryError as exc:
            self._set_status("保存位置不可用。", _WARN)
            messagebox.showerror("保存位置不可用", str(exc), parent=self.root)
            return

        duration = self.duration_var.get()
        if duration not in DURATION_OPTIONS:
            duration = DURATION_OPTIONS[0]
        self._duration_seconds = duration
        self._pending_directory = directory
        self.output_var.set(str(directory))
        self._save_settings(directory)
        self._state = _SELECTING
        self._set_status("请在全屏遮罩上拖动鼠标，框选录制区域。", _INFO)
        self.root.withdraw()
        self.root.after(80, self._open_selector)

    def _open_selector(self) -> None:
        if self._state != _SELECTING:
            return
        try:
            self._selector = RegionSelector(
                self.root,
                on_confirm=self._on_region_selected,
                on_cancel=self._on_selection_cancelled,
            )
        except Exception as exc:
            LOGGER.exception("打开区域选择界面失败")
            self._selector = None
            self._state = _IDLE
            self._restore_main()
            self._set_status("无法打开框选界面，请重试。", _WARN)
            messagebox.showerror("无法框选", f"打开框选界面失败：\n{exc}", parent=self.root)

    def _on_region_selected(self, region: CaptureRegion) -> None:
        self._selector = None
        if self._state != _SELECTING or self._pending_directory is None:
            return
        self._state = _STARTING
        output_path = build_output_path(self._pending_directory)
        self._begin_recording(region, output_path)

    def _on_selection_cancelled(self) -> None:
        self._selector = None
        if self._state != _SELECTING:
            return
        self._state = _IDLE
        self._restore_main()
        self._set_status("已取消框选，未开始录制。", _MUTED)

    def _begin_recording(self, region: CaptureRegion, output_path: Path) -> None:
        self._output_path = output_path
        self._cancel_requested = False
        self._exit_when_done = False
        self._events = queue.Queue()
        self._stop_event = threading.Event()

        countdown: CountdownOverlay | None = None
        try:
            countdown = CountdownOverlay(self.root, self._duration_seconds)
            countdown.show()
            self._countdown = countdown
        except Exception:
            LOGGER.exception("创建倒计时浮层失败")
            if countdown is not None:
                countdown.close()
            self._countdown = None
            self._abort_start("无法创建录制倒计时浮层。")
            return

        tray = TrayIcon(
            "屏幕录制中 · 右键停止或取消",
            ICON_ICO,
            self._on_tray_command,
        )
        try:
            tray.start()
        except TrayError as exc:
            LOGGER.warning("系统托盘不可用，改为最小化到任务栏：%s", exc)
            tray.stop(timeout=0.5)
            self._tray = None
            self._minimize_to_taskbar()
        else:
            self._tray = tray

        self._state = _RECORDING
        self._recording_started_at = time.perf_counter()
        if self._countdown is not None:
            self._countdown.set_remaining(self._duration_seconds)
        worker = threading.Thread(
            target=self._record_worker,
            args=(region, output_path),
            name="screen-recording",
            daemon=True,
        )
        self._worker = worker
        try:
            worker.start()
        except RuntimeError:
            LOGGER.exception("启动录制线程失败")
            self._worker = None
            self._abort_start("无法启动录制线程，请重试。")
            return
        self._poll_job = self.root.after(80, self._poll_recording)

    def _record_worker(self, region: CaptureRegion, output_path: Path) -> None:
        try:
            result = record_screen(
                region,
                output_path,
                self._duration_seconds,
                stop_event=self._stop_event,
            )
        except Exception as exc:
            LOGGER.exception("录制失败")
            self._events.put(("error", exc))
        else:
            self._events.put(("done", result))

    def _minimize_to_taskbar(self) -> None:
        try:
            self.root.deiconify()
            self.root.update_idletasks()
            self.root.iconify()
        except tk.TclError:
            LOGGER.debug("最小化主窗口失败", exc_info=True)

    def _on_tray_command(self, command: TrayCommand) -> None:
        self._events.put(("tray", command))

    def _poll_recording(self) -> None:
        self._poll_job = None
        if self._state != _RECORDING:
            return
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "tray":
                self._handle_tray_command(payload)
            elif kind == "done":
                self._handle_recording_done(payload)
                return
            elif kind == "error":
                self._handle_recording_error(payload)
                return

        if self._state == _RECORDING:
            if self._countdown is not None:
                elapsed = time.perf_counter() - self._recording_started_at
                self._countdown.set_remaining(self._duration_seconds - elapsed)
            self._poll_job = self.root.after(100, self._poll_recording)

    def _handle_tray_command(self, payload: object) -> None:
        if not isinstance(payload, str) or self._stop_event is None:
            return
        if payload == "stop":
            LOGGER.info("用户从托盘请求提前停止录制")
            self._stop_event.set()
        elif payload == "cancel":
            LOGGER.info("用户从托盘取消录制")
            self._cancel_requested = True
            self._stop_event.set()
        elif payload == "exit":
            LOGGER.info("用户从托盘退出程序")
            self._cancel_requested = True
            self._exit_when_done = True
            self._stop_event.set()

    def _handle_recording_done(self, payload: object) -> None:
        if not isinstance(payload, RecordingResult):
            self._handle_recording_error(RuntimeError("录制结果无效。"))
            return
        cancelled = self._cancel_requested
        exit_after = self._exit_when_done
        self._finish_recording_ui()
        if cancelled:
            try:
                payload.path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("删除已取消的视频失败：%s", payload.path)
            if exit_after:
                self._destroy()
                return
            self._restore_main()
            self._state = _IDLE
            self._set_status("已取消录制，视频未保存。", _MUTED)
            return

        self._restore_main()
        self._state = _IDLE
        self._set_status(f"录制完成：{payload.path.name}", _SUCCESS)
        self._show_completion_dialog(payload)

    def _handle_recording_error(self, payload: object) -> None:
        error = payload if isinstance(payload, BaseException) else RuntimeError(str(payload))
        cancelled = self._cancel_requested
        exit_after = self._exit_when_done
        self._finish_recording_ui()
        if exit_after:
            self._destroy()
            return
        self._restore_main()
        self._state = _IDLE
        if cancelled:
            self._set_status("已取消录制，视频未保存。", _MUTED)
            return
        LOGGER.error("录制失败：%s", error)
        self._set_status("录制失败，请检查保存位置后重试。", _WARN)
        messagebox.showerror("录制失败", str(error), parent=self.root)

    def _finish_recording_ui(self) -> None:
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        if self._countdown is not None:
            self._countdown.close()
            self._countdown = None
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        if self._worker is not None:
            self._worker.join(timeout=1.0)
            self._worker = None
        self._stop_event = None
        self._state = _FINISHING

    def _show_completion_dialog(self, result: RecordingResult) -> None:
        try:
            size_text = format_bytes(result.path.stat().st_size)
        except OSError:
            size_text = "未知"
        duration_text = f"{result.duration_seconds:.0f} 秒"
        stop_note = "\n已按请求提前停止。" if result.stopped_early else ""
        message = (
            "录制完成\n\n"
            f"文件：{result.path.name}\n"
            f"时长：{duration_text}\n"
            f"大小：{size_text}\n"
            f"位置：{result.path.parent}{stop_note}\n\n"
            "是否打开所在文件夹？"
        )
        LOGGER.info("录制完成：%s", result.path)
        open_now = messagebox.askyesno("录制完成", message, parent=self.root)
        if open_now:
            try:
                open_folder(result.path.parent)
            except OSError as exc:
                messagebox.showerror("无法打开文件夹", str(exc), parent=self.root)

    def _abort_start(self, message: str) -> None:
        self._finish_recording_ui()
        self._restore_main()
        self._state = _IDLE
        self._set_status(message, _WARN)
        messagebox.showerror("无法开始录制", message, parent=self.root)

    def _restore_main(self) -> None:
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.update_idletasks()
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)
            self.root.after(300, self._drop_topmost)
        except tk.TclError:
            LOGGER.debug("恢复主窗口失败", exc_info=True)

    def _drop_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", False)
        except tk.TclError:
            pass

    def _save_settings(self, directory: Path | None = None) -> None:
        output_dir = str(directory) if directory is not None else self.output_var.get().strip()
        self._settings_store.save(
            AppSettings(
                duration_seconds=self.duration_var.get(),
                output_dir=output_dir,
            )
        )

    def _on_close(self) -> None:
        if self._state == _RECORDING:
            should_exit = messagebox.askyesno(
                "正在录制",
                "当前正在录制，退出将取消本次录制。\n确定要退出吗？",
                parent=self.root,
            )
            if not should_exit:
                return
            self._cancel_requested = True
            self._exit_when_done = True
            if self._stop_event is not None:
                self._stop_event.set()
            self.root.withdraw()
            return

        if self._state in (_SELECTING, _STARTING):
            if self._selector is not None:
                self._selector.cancel()
            self.root.after(80, self._destroy)
            return

        self._destroy()

    def _destroy(self) -> None:
        self._save_settings()
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _on_tk_exception(
        self,
        exception_type: type[BaseException],
        exception: BaseException,
        traceback: object,
    ) -> None:
        LOGGER.error(
            "界面回调发生未处理异常",
            exc_info=(exception_type, exception, traceback),
        )
        try:
            messagebox.showerror(
                "程序错误",
                f"界面发生未处理错误：\n{exception}",
                parent=self.root,
            )
        except tk.TclError:
            pass


def _configure_logging() -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        base = os.environ.get("LOCALAPPDATA")
        log_dir = (Path(base) if base else Path.home()) / "ShortVideoCapture"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=1_000_000,
                backupCount=2,
                encoding="utf-8",
            )
        )
    except OSError:
        pass
    if not handlers:
        handlers.append(logging.NullHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> int:
    _configure_logging()
    if os.name != "nt":
        LOGGER.error("本工具仅支持 Windows。")
        return 1
    enable_dpi_awareness()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        LOGGER.error("无法创建图形界面：%s", exc)
        return 1
    try:
        RecorderApp(root)
        root.mainloop()
    except Exception:
        LOGGER.exception("程序异常退出")
        return 1
    return 0
