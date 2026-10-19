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
from tkinter import filedialog, messagebox, ttk

from .audio import (
    AudioDevice,
    AudioDevices,
    AudioSelection,
    list_audio_devices,
    unique_device_labels,
)
from .capture import CaptureRegion, VideoSettings
from .config import (
    AUDIO_BITRATE_OPTIONS,
    DURATION_OPTIONS,
    AppSettings,
    SettingsStore,
    parse_audio_bitrate_kbps,
    parse_duration,
    parse_fps,
    parse_video_bitrate_mbps,
)
from .countdown import CountdownOverlay
from .media import FFmpegError, find_ffmpeg
from .paths import (
    OutputDirectoryError,
    build_output_path,
    default_videos_dir,
    ensure_output_dir,
    format_bytes,
    open_folder,
)
from .pipeline import RecordingResult, format_audio_warning, record_session
from .region_selector import RegionSelector
from .tray import TrayCommand, TrayError, TrayIcon, TrayMenuItem
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
_FIELD_BG = "#F8FAFC"
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

_RECORDING_MENU: tuple[TrayMenuItem | None, ...] = (
    ("停止并保存", "stop"),
    ("取消录制", "cancel"),
    None,
    ("退出程序（放弃录制）", "exit"),
)
_IDLE_MENU: tuple[TrayMenuItem | None, ...] = (
    ("显示主界面", "show"),
    None,
    ("退出程序", "exit"),
)
_IDLE_STATUS = "就绪，选择时长和保存位置后开始录制。"


class RecorderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._settings_store = SettingsStore()
        settings = self._settings_store.load()
        initial_directory = settings.output_dir or str(default_videos_dir())

        self._state = _IDLE
        self._selector: RegionSelector | None = None
        self._countdown: CountdownOverlay | None = None
        self._tray: TrayIcon | None = None
        self._tray_mode: str | None = None
        self._worker: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._events: queue.Queue[WindowEvent] = queue.Queue()
        self._poll_job: str | None = None
        self._hide_job: str | None = None
        self._destroyed = False
        self._pending_directory: Path | None = None
        self._duration_seconds = settings.duration_seconds
        self._video_settings = VideoSettings(
            fps=settings.fps,
            bitrate_kbps=round(settings.video_bitrate_mbps * 1000),
        )
        self._audio_bitrate_kbps = settings.audio_bitrate_kbps
        self._audio_selection = AudioSelection()
        self._recording_started_at = 0.0
        self._cancel_requested = False
        self._exit_when_done = False
        self._tray_hide_failed = False
        self._audio_devices = AudioDevices((), (), None, None)
        self._system_device_map: dict[str, str] = {}
        self._microphone_device_map: dict[str, str] = {}

        self.duration_text = tk.StringVar(value=str(settings.duration_seconds))
        self.output_var = tk.StringVar(value=initial_directory)
        self.status_var = tk.StringVar(value=_IDLE_STATUS)
        self.fps_var = tk.StringVar(value=str(settings.fps))
        self.video_bitrate_var = tk.StringVar(
            value=f"{settings.video_bitrate_mbps:g}"
        )
        self.audio_bitrate_var = tk.StringVar(value=str(settings.audio_bitrate_kbps))
        self.system_audio_var = tk.BooleanVar(value=settings.system_audio_enabled)
        self.microphone_var = tk.BooleanVar(value=settings.microphone_enabled)
        self.system_device_var = tk.StringVar()
        self.microphone_device_var = tk.StringVar()

        self._configure_window()
        self._build_ui()
        self._fit_and_center_window()
        self.duration_text.trace_add("write", self._on_duration_text_changed)
        self._choose_duration(settings.duration_seconds)
        self._on_duration_text_changed()
        self._set_status(_IDLE_STATUS, _MUTED)
        self._refresh_audio_devices(
            preferred_system_id=settings.system_audio_device or None,
            preferred_microphone_id=settings.microphone_device or None,
        )
        self._fit_and_center_window()
        self.root.bind("<Unmap>", self._on_root_unmap, add="+")
        self._poll_job = self.root.after(100, self._poll_events)

    # ------------------------------------------------------------------
    # 窗口与主界面
    # ------------------------------------------------------------------
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

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Recorder.TCombobox",
            fieldbackground=_FIELD_BG,
            background="#E2E8F0",
            foreground=_TEXT,
            arrowcolor="#475569",
            padding=4,
            font=("Microsoft YaHei UI", 9),
        )

    def _fit_and_center_window(self) -> None:
        self.root.update_idletasks()
        width = max(760, self.root.winfo_reqwidth())
        height = max(700, self.root.winfo_reqheight() + 8)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        height = min(height, max(620, screen_height - 80))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        header = tk.Frame(self.root, bg=_HEADER_BG)
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=_HEADER_BG)
        header_inner.pack(fill="x", padx=30, pady=18)

        if self._icon_image is not None:
            tk.Label(
                header_inner, image=self._icon_image, bg=_HEADER_BG, bd=0
            ).pack(side="left", padx=(0, 16))

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
            text="框选区域 · 自定义时长 · 画面与声音",
            bg=_HEADER_BG,
            fg="#94A3B8",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        ).pack(fill="x", pady=(4, 0))

        body = tk.Frame(self.root, bg=_BG)
        body.pack(fill="both", expand=True, padx=30, pady=18)

        self._build_duration_card(body)
        self._build_quality_card(body)
        self._build_output_card(body)

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
            pady=11,
            cursor="hand2",
            font=("Microsoft YaHei UI", 12, "bold"),
        )
        self._start_button.pack(fill="x", pady=(0, 8))
        self._bind_hover(self._start_button, _ACCENT, _ACCENT_HOVER)

        tk.Label(
            body,
            text="录制时自动隐藏到系统托盘；最小化窗口也会隐藏到托盘。",
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
            wraplength=690,
        )
        self.status_label.pack(fill="x", pady=(8, 0))

        self._path_entry.bind("<FocusIn>", lambda _event: self._clear_status())
        self._path_entry.bind("<Return>", lambda _event: self._start_flow())

    def _build_duration_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        title_row = tk.Frame(card, bg=_CARD_BG)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text="录制时长",
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            title_row,
            text="1–86400 秒",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right")

        row = tk.Frame(card, bg=_CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        self._duration_buttons: dict[int, tk.Button] = {}
        for seconds in DURATION_OPTIONS:
            button = tk.Button(
                row,
                text=f"{seconds} 秒",
                command=lambda value=seconds: self._choose_duration(value),
                width=9,
                pady=8,
                relief="flat",
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                font=("Microsoft YaHei UI", 10, "bold"),
            )
            button.pack(side="left", padx=(0, 8))
            self._duration_buttons[seconds] = button

        tk.Label(
            row,
            text="自定义",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(8, 6))
        tk.Spinbox(
            row,
            from_=1,
            to=86400,
            increment=1,
            width=7,
            textvariable=self.duration_text,
            bg=_FIELD_BG,
            fg=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_INFO,
            buttonbackground="#E2E8F0",
            font=("Consolas", 10),
            justify="center",
        ).pack(side="left", ipady=5)
        tk.Label(
            row,
            text="秒",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(6, 0))

    def _build_quality_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        title_row = tk.Frame(card, bg=_CARD_BG)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text="画面与声音",
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            title_row,
            text="刷新设备",
            command=self._refresh_audio_devices,
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

        quality = tk.Frame(card, bg=_CARD_BG)
        quality.pack(fill="x", pady=(8, 6))
        self._field_group(quality, "帧率", self.fps_var, "FPS", width=5).pack(
            side="left", padx=(0, 22)
        )
        self._field_group(
            quality, "视频码率", self.video_bitrate_var, "Mbps", width=6
        ).pack(side="left", padx=(0, 22))
        audio_group = tk.Frame(quality, bg=_CARD_BG)
        audio_group.pack(side="left")
        tk.Label(
            audio_group,
            text="音频码率",
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(anchor="w")
        audio_value_row = tk.Frame(audio_group, bg=_CARD_BG)
        audio_value_row.pack(anchor="w", pady=(3, 0))
        self._audio_bitrate_combo = ttk.Combobox(
            audio_value_row,
            textvariable=self.audio_bitrate_var,
            values=[str(value) for value in AUDIO_BITRATE_OPTIONS],
            state="readonly",
            width=6,
            style="Recorder.TCombobox",
            font=("Microsoft YaHei UI", 9),
        )
        self._audio_bitrate_combo.pack(side="left")
        tk.Label(
            audio_value_row,
            text="kbps",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=(4, 0))

        tk.Frame(card, bg=_BORDER, height=1).pack(fill="x", pady=(4, 7))

        system_row = tk.Frame(card, bg=_CARD_BG)
        system_row.pack(fill="x", pady=(0, 5))
        self._system_check = tk.Checkbutton(
            system_row,
            text="系统声音",
            variable=self.system_audio_var,
            command=self._sync_audio_controls,
            bg=_CARD_BG,
            activebackground=_CARD_BG,
            selectcolor=_CARD_BG,
            fg=_TEXT,
            activeforeground=_TEXT,
            anchor="w",
            width=9,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 9),
        )
        self._system_check.pack(side="left")
        self._system_combo = ttk.Combobox(
            system_row,
            textvariable=self.system_device_var,
            state="readonly",
            width=45,
            style="Recorder.TCombobox",
            font=("Microsoft YaHei UI", 9),
        )
        self._system_combo.pack(side="left", fill="x", expand=True)

        microphone_row = tk.Frame(card, bg=_CARD_BG)
        microphone_row.pack(fill="x", pady=(0, 5))
        self._microphone_check = tk.Checkbutton(
            microphone_row,
            text="麦克风",
            variable=self.microphone_var,
            command=self._sync_audio_controls,
            bg=_CARD_BG,
            activebackground=_CARD_BG,
            selectcolor=_CARD_BG,
            fg=_TEXT,
            activeforeground=_TEXT,
            anchor="w",
            width=9,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 9),
        )
        self._microphone_check.pack(side="left")
        self._microphone_combo = ttk.Combobox(
            microphone_row,
            textvariable=self.microphone_device_var,
            state="readonly",
            width=45,
            style="Recorder.TCombobox",
            font=("Microsoft YaHei UI", 9),
        )
        self._microphone_combo.pack(side="left", fill="x", expand=True)

        self._audio_note = tk.Label(
            card,
            text="系统声音通过扬声器环回录制；音频会混为一条 AAC 音轨。",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
            justify="left",
            wraplength=680,
        )
        self._audio_note.pack(fill="x", pady=(2, 0))

    def _field_group(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        unit: str,
        *,
        width: int,
    ) -> tk.Frame:
        group = tk.Frame(parent, bg=_CARD_BG)
        tk.Label(
            group,
            text=label,
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(anchor="w")
        value_row = tk.Frame(group, bg=_CARD_BG)
        value_row.pack(anchor="w", pady=(3, 0))
        tk.Spinbox(
            value_row,
            from_=5 if label == "帧率" else 0.5,
            to=60 if label == "帧率" else 50,
            increment=1 if label == "帧率" else 0.5,
            width=width,
            textvariable=variable,
            bg=_FIELD_BG,
            fg=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_INFO,
            buttonbackground="#E2E8F0",
            font=("Consolas", 9),
            justify="center",
        ).pack(side="left", ipady=3)
        tk.Label(
            value_row,
            text=unit,
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=(4, 0))
        return group

    def _build_output_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        output_header = tk.Frame(card, bg=_CARD_BG)
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
            card,
            bg=_FIELD_BG,
            highlightbackground=_BORDER,
            highlightthickness=1,
        )
        path_row.pack(fill="x", pady=(7, 5))
        self._path_entry = tk.Entry(
            path_row,
            textvariable=self.output_var,
            bg=_FIELD_BG,
            fg=_TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            insertbackground=_TEXT,
            font=("Microsoft YaHei UI", 9),
        )
        self._path_entry.pack(side="left", fill="x", expand=True, padx=12, pady=9)
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
            pady=4,
            cursor="hand2",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=5)

        tk.Label(
            card,
            text="视频保存为 MP4（H.264），默认使用 Windows 视频文件夹。",
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(fill="x")

    def _make_card(self, parent: tk.Misc) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=_CARD_BG,
            highlightbackground=_BORDER,
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 11))
        inner = tk.Frame(card, bg=_CARD_BG)
        inner.pack(fill="x", padx=18, pady=12)
        return inner

    @staticmethod
    def _bind_hover(button: tk.Button, normal: str, hover: str) -> None:
        button.bind("<Enter>", lambda _event: button.configure(bg=hover))
        button.bind("<Leave>", lambda _event: button.configure(bg=normal))

    # ------------------------------------------------------------------
    # 时长、画质与设备选择
    # ------------------------------------------------------------------
    def _choose_duration(self, seconds: int) -> None:
        self.duration_text.set(str(seconds))

    def _on_duration_text_changed(self, *_args: object) -> None:
        try:
            seconds = parse_duration(self.duration_text.get())
        except ValueError:
            seconds = None
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

    def _refresh_audio_devices(
        self,
        preferred_system_id: str | None = None,
        preferred_microphone_id: str | None = None,
    ) -> None:
        if preferred_system_id is None:
            preferred_system_id = self._selected_device_id(
                self._system_device_map, self.system_device_var.get()
            )
        if preferred_microphone_id is None:
            preferred_microphone_id = self._selected_device_id(
                self._microphone_device_map, self.microphone_device_var.get()
            )
        devices = list_audio_devices()
        self._audio_devices = devices
        self._populate_device_combo(
            self._system_combo,
            self.system_device_var,
            devices.system,
            devices.default_system_id,
            preferred_system_id,
            system=True,
        )
        self._populate_device_combo(
            self._microphone_combo,
            self.microphone_device_var,
            devices.microphones,
            devices.default_microphone_id,
            preferred_microphone_id,
            system=False,
        )
        if devices.error:
            self._audio_note.configure(text=device_error_text(devices.error), fg=_WARN)
        elif not devices.system and not devices.microphones:
            self._audio_note.configure(
                text="未检测到可用的音频设备；仍可录制无声视频。", fg=_WARN
            )
        elif not devices.system:
            self._audio_note.configure(
                text="未检测到系统声音输出设备；可录制麦克风或无声视频。",
                fg=_WARN,
            )
        elif not devices.microphones:
            self._audio_note.configure(
                text="未检测到麦克风；系统声音仍可正常录制。", fg=_MUTED
            )
        else:
            self._audio_note.configure(
                text="系统声音通过扬声器环回录制；音频会混为一条 AAC 音轨。",
                fg=_MUTED,
            )
        if not devices.error:
            self.system_audio_var.set(
                self.system_audio_var.get() and bool(devices.system)
            )
            self.microphone_var.set(
                self.microphone_var.get() and bool(devices.microphones)
            )
        self._sync_audio_controls()

    def _populate_device_combo(
        self,
        combo: ttk.Combobox,
        variable: tk.StringVar,
        devices: tuple[AudioDevice, ...],
        default_id: str | None,
        preferred_id: str | None,
        *,
        system: bool,
    ) -> None:
        labels = unique_device_labels(devices)
        label_map = dict(labels)
        id_to_label = {device_id: label for label, device_id in labels}
        if system:
            self._system_device_map = label_map
        else:
            self._microphone_device_map = label_map

        combo.configure(values=[label for label, _ in labels])
        if not labels:
            variable.set("")
            combo.configure(state="disabled")
            return
        chosen_id = preferred_id if preferred_id in id_to_label else default_id
        if chosen_id not in id_to_label:
            chosen_id = labels[0][1]
        variable.set(id_to_label[chosen_id])
        combo.configure(state="readonly")

    def _sync_audio_controls(self) -> None:
        self._system_combo.configure(
            state=(
                "readonly"
                if self.system_audio_var.get() and self._audio_devices.system
                else "disabled"
            )
        )
        self._microphone_combo.configure(
            state=(
                "readonly"
                if self.microphone_var.get() and self._audio_devices.microphones
                else "disabled"
            )
        )
        if not self._audio_devices.system and not self._audio_devices.error:
            self._system_check.configure(state="disabled")
        else:
            self._system_check.configure(state="normal")
        if not self._audio_devices.microphones and not self._audio_devices.error:
            self._microphone_check.configure(state="disabled")
        else:
            self._microphone_check.configure(state="normal")

    @staticmethod
    def _selected_device_id(mapping: dict[str, str], label: str) -> str | None:
        return mapping.get(label)

    def _audio_selection_from_ui(self) -> AudioSelection:
        system_id = self._selected_device_id(
            self._system_device_map, self.system_device_var.get()
        )
        microphone_id = self._selected_device_id(
            self._microphone_device_map, self.microphone_device_var.get()
        )
        system_enabled = bool(self.system_audio_var.get() and system_id)
        microphone_enabled = bool(self.microphone_var.get() and microphone_id)
        return AudioSelection(
            system_enabled=system_enabled,
            system_device_id=system_id,
            microphone_enabled=microphone_enabled,
            microphone_device_id=microphone_id,
        )

    # ------------------------------------------------------------------
    # 普通操作
    # ------------------------------------------------------------------
    def _set_status(self, text: str, color: str) -> None:
        self.status_var.set(text)
        self.status_label.configure(fg=color)
        self.root.update_idletasks()

    def _clear_status(self) -> None:
        if self._state == _IDLE:
            self._set_status(_IDLE_STATUS, _MUTED)

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
            duration = parse_duration(self.duration_text.get())
            fps = parse_fps(self.fps_var.get())
            bitrate_mbps = parse_video_bitrate_mbps(self.video_bitrate_var.get())
            audio_bitrate = parse_audio_bitrate_kbps(
                self.audio_bitrate_var.get()
            )
            find_ffmpeg()
        except (OutputDirectoryError, ValueError, FFmpegError) as exc:
            self._set_status(str(exc), _WARN)
            messagebox.showerror("设置无效", str(exc), parent=self.root)
            return

        audio_selection = self._audio_selection_from_ui()
        if self.system_audio_var.get() and not audio_selection.system_enabled:
            self._set_status("系统声音已开启但没有可用设备。", _WARN)
            messagebox.showerror(
                "音频设备不可用",
                "系统声音已开启，但没有选择可用设备。\n"
                "请刷新设备、选择设备，或关闭系统声音。",
                parent=self.root,
            )
            return
        if self.microphone_var.get() and not audio_selection.microphone_enabled:
            self._set_status("麦克风已开启但没有可用设备。", _WARN)
            messagebox.showerror(
                "音频设备不可用",
                "麦克风已开启，但没有选择可用设备。\n"
                "请刷新设备、选择设备，或关闭麦克风。",
                parent=self.root,
            )
            return

        self._duration_seconds = duration
        self._video_settings = VideoSettings(
            fps=fps,
            bitrate_kbps=round(bitrate_mbps * 1000),
        )
        self._audio_bitrate_kbps = audio_bitrate
        self._audio_selection = audio_selection
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

    # ------------------------------------------------------------------
    # 录制流程
    # ------------------------------------------------------------------
    def _begin_recording(self, region: CaptureRegion, output_path: Path) -> None:
        self._cancel_requested = False
        self._exit_when_done = False
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

        if not self._start_tray("recording"):
            LOGGER.warning("系统托盘不可用，改为最小化到任务栏")
            self._minimize_to_taskbar()

        self._state = _RECORDING
        self._recording_started_at = 0.0
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

    def _record_worker(self, region: CaptureRegion, output_path: Path) -> None:
        try:
            result = record_session(
                region,
                output_path,
                self._duration_seconds,
                self._video_settings,
                self._audio_selection,
                self._audio_bitrate_kbps,
                stop_event=self._stop_event,
                on_started=lambda: self._events.put(
                    ("started", time.perf_counter())
                ),
            )
        except Exception as exc:
            LOGGER.exception("录制失败")
            self._events.put(("error", exc))
        else:
            self._events.put(("done", result))

    def _start_tray(self, mode: str) -> bool:
        if self._tray is not None:
            if self._tray_mode == mode:
                return True
            self._stop_tray()
        items = _RECORDING_MENU if mode == "recording" else _IDLE_MENU
        tooltip = (
            "屏幕录制中 · 右键停止或取消"
            if mode == "recording"
            else "屏幕录制工具 · 右键显示或退出"
        )
        tray = TrayIcon(tooltip, ICON_ICO, self._on_tray_command, items)
        try:
            tray.start()
        except TrayError as exc:
            LOGGER.warning("系统托盘不可用：%s", exc)
            tray.stop(timeout=0.5)
            return False
        self._tray = tray
        self._tray_mode = mode
        return True

    def _stop_tray(self) -> None:
        if self._tray is not None:
            self._tray.stop()
            self._tray = None
            self._tray_mode = None

    def _minimize_to_taskbar(self) -> None:
        try:
            self.root.deiconify()
            self.root.update_idletasks()
            self.root.iconify()
        except tk.TclError:
            LOGGER.debug("最小化主窗口失败", exc_info=True)

    def _on_tray_command(self, command: TrayCommand) -> None:
        self._events.put(("tray", command))

    def _poll_events(self) -> None:
        self._poll_job = None
        if self._destroyed:
            return
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "tray":
                self._handle_tray_command(payload)
            elif kind == "started":
                if isinstance(payload, float):
                    self._recording_started_at = payload
            elif kind == "done":
                self._handle_recording_done(payload)
                break
            elif kind == "error":
                self._handle_recording_error(payload)
                break
        if self._destroyed:
            return
        if (
            self._state == _RECORDING
            and self._countdown is not None
            and self._recording_started_at > 0
        ):
            elapsed = time.perf_counter() - self._recording_started_at
            self._countdown.set_remaining(self._duration_seconds - elapsed)
        try:
            self._poll_job = self.root.after(100, self._poll_events)
        except tk.TclError:
            self._poll_job = None

    def _handle_tray_command(self, payload: object) -> None:
        if not isinstance(payload, str):
            return
        if payload == "show":
            self._show_from_tray()
            return
        if payload == "exit":
            if self._state == _RECORDING:
                LOGGER.info("用户从托盘退出程序")
                self._cancel_requested = True
                self._exit_when_done = True
                if self._stop_event is not None:
                    self._stop_event.set()
            else:
                self._destroy()
            return
        if self._state != _RECORDING or self._stop_event is None:
            return
        if payload == "stop":
            LOGGER.info("用户从托盘请求提前停止录制")
            self._stop_event.set()
        elif payload == "cancel":
            LOGGER.info("用户从托盘取消录制")
            self._cancel_requested = True
            self._stop_event.set()

    def _show_from_tray(self) -> None:
        if self._state != _IDLE:
            return
        self._stop_tray()
        self._restore_main()
        self._set_status("已从系统托盘恢复。", _MUTED)

    def _on_root_unmap(self, event: tk.Event) -> None:
        if self._destroyed or event.widget is not self.root:
            return
        if self._state != _IDLE or self._tray is not None or self._tray_hide_failed:
            return
        if self._hide_job is not None:
            try:
                self.root.after_cancel(self._hide_job)
            except tk.TclError:
                pass
        self._hide_job = self.root.after(80, self._hide_to_tray_if_minimized)

    def _hide_to_tray_if_minimized(self) -> None:
        self._hide_job = None
        if self._state != _IDLE or self._tray is not None:
            return
        try:
            if self.root.state() != "iconic":
                return
        except tk.TclError:
            return
        if not self._start_tray("idle"):
            self._tray_hide_failed = True
            LOGGER.warning("托盘不可用，窗口保留在任务栏。")
            return
        try:
            if self.root.state() != "iconic":
                self._stop_tray()
                return
            self.root.withdraw()
        except tk.TclError:
            self._stop_tray()

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
        self._set_status("录制失败，请检查设置后重试。", _WARN)
        messagebox.showerror("录制失败", str(error), parent=self.root)

    def _finish_recording_ui(self) -> None:
        if self._countdown is not None:
            self._countdown.close()
            self._countdown = None
        self._stop_tray()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
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
        audio_note = self._completion_audio_note(result)
        message = (
            "录制完成\n\n"
            f"文件：{result.path.name}\n"
            f"时长：{duration_text}\n"
            f"大小：{size_text}\n"
            f"画质：{result.fps} FPS · {self._video_settings.bitrate_mbps:g} Mbps\n"
            f"位置：{result.path.parent}{stop_note}{audio_note}\n\n"
            "是否打开所在文件夹？"
        )
        LOGGER.info("录制完成：%s", result.path)
        open_now = messagebox.askyesno("录制完成", message, parent=self.root)
        if open_now:
            try:
                open_folder(result.path.parent)
            except OSError as exc:
                messagebox.showerror("无法打开文件夹", str(exc), parent=self.root)

    def _completion_audio_note(self, result: RecordingResult) -> str:
        sources = []
        if self._audio_selection.system_enabled:
            sources.append("系统声音")
        if self._audio_selection.microphone_enabled:
            sources.append("麦克风")
        note = "\n音频：" + (" + ".join(sources) if sources else "无")
        warning = format_audio_warning(result.audio_warnings)
        if warning:
            note += f"\n注意：{warning}"
        return note

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

    # ------------------------------------------------------------------
    # 设置持久化与退出
    # ------------------------------------------------------------------
    def _save_settings(self, directory: Path | None = None) -> None:
        try:
            self._duration_seconds = parse_duration(self.duration_text.get())
        except ValueError:
            pass
        try:
            self._video_settings = VideoSettings(
                fps=parse_fps(self.fps_var.get()),
                bitrate_kbps=round(
                    parse_video_bitrate_mbps(self.video_bitrate_var.get()) * 1000
                ),
            )
        except ValueError:
            pass
        try:
            self._audio_bitrate_kbps = parse_audio_bitrate_kbps(
                self.audio_bitrate_var.get()
            )
        except ValueError:
            pass
        output_dir = str(directory) if directory is not None else self.output_var.get().strip()
        system_id = self._selected_device_id(
            self._system_device_map, self.system_device_var.get()
        )
        microphone_id = self._selected_device_id(
            self._microphone_device_map, self.microphone_device_var.get()
        )
        self._settings_store.save(
            AppSettings(
                duration_seconds=self._duration_seconds,
                output_dir=output_dir,
                fps=self._video_settings.fps,
                video_bitrate_mbps=self._video_settings.bitrate_mbps,
                audio_bitrate_kbps=self._audio_bitrate_kbps,
                system_audio_enabled=bool(self.system_audio_var.get()),
                microphone_enabled=bool(self.microphone_var.get()),
                system_audio_device=system_id or "",
                microphone_device=microphone_id or "",
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
        if self._destroyed:
            return
        self._destroyed = True
        if self._poll_job is not None:
            try:
                self.root.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        self._save_settings()
        self._stop_tray()
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


def device_error_text(error: str) -> str:
    if "soundcard" in error:
        return (
            "音频组件未安装：请执行 python -m pip install -e .，"
            "或使用项目 .venv\\Scripts\\python run.py 启动。"
        )
    return f"音频设备不可用：{error}"


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
