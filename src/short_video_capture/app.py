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
from tkinter import messagebox

from .audio import (
    AudioDevice,
    AudioDevices,
    AudioSelection,
    list_audio_devices,
    unique_device_labels,
)
from .capture import CaptureRegion, NoFramesError, VideoSettings
from .config import (
    AUDIO_BITRATE_OPTIONS,
    DURATION_OPTIONS,
    MAX_DURATION,
    MAX_DURATION_SENTINEL,
    MAX_FPS,
    MAX_VIDEO_BITRATE_MBPS,
    MIN_DURATION,
    MIN_FPS,
    MIN_VIDEO_BITRATE_MBPS,
    AppSettings,
    SettingsStore,
    parse_audio_bitrate_kbps,
    parse_duration,
    parse_fps,
    parse_video_bitrate_mbps,
)
from .countdown import CountdownOverlay
from .directory_picker import ask_directory
from .i18n import LANGUAGES, get_language, language_name, set_language, tr
from .media import FFmpegError, find_ffmpeg
from .paths import (
    OutputDirectoryError,
    build_output_path,
    default_videos_dir,
    ensure_output_dir,
    fallback_output_dir,
    format_bytes,
    open_folder,
)
from .pipeline import RecordingResult, format_audio_warning, record_session
from .region_selector import RegionSelector
from .tray import TrayCommand, TrayError, TrayIcon, TrayMenuItem
from .widgets import (
    RoundedButton,
    RoundedCard,
    RoundedCheckbox,
    RoundedDropdown,
    RoundedEntry,
    load_scaled_icon_png,
)
from .win32 import enable_dpi_awareness

LOGGER = logging.getLogger(__name__)

APP_TITLE = "QuickRec"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS_DIR / "app.ico"
ICON_PNG = ASSETS_DIR / "app_64.png"

_BG = "#F1F5F9"
_HEADER_BG = "#0F172A"
_CARD_BG = "#FFFFFF"
_BORDER = "#E2E8F0"
_INPUT_BORDER = "#D8E0EA"
_FIELD_BG = "#F8FAFC"
_BTN_NEUTRAL = "#E4EAF3"
_BTN_NEUTRAL_HOVER = "#D6DFEC"
_BTN_NEUTRAL_ACTIVE = "#C8D4E5"
_TEXT = "#0F172A"
_MUTED = "#64748B"
_ACCENT = "#F55342"
_ACCENT_HOVER = "#E54332"
_ACCENT_ACTIVE = "#D63726"
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
        self._language = set_language(settings.language)
        initial_directory, initial_notice, notice_color = self._resolve_initial_directory(
            settings
        )
        self._initial_notice = initial_notice
        self._initial_notice_color = notice_color

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
        self._stop_requested = False
        self._exit_when_done = False
        self._tray_hide_failed = False
        self._audio_devices = AudioDevices((), (), None, None)
        self._system_device_map: dict[str, str] = {}
        self._microphone_device_map: dict[str, str] = {}

        self.duration_text = tk.StringVar(
            value=(
                "MAX"
                if settings.duration_seconds == MAX_DURATION_SENTINEL
                else str(settings.duration_seconds)
            )
        )
        self.language_var = tk.StringVar(value=language_name(self._language))
        self.output_var = tk.StringVar(value=initial_directory)
        self.status_var = tk.StringVar(value=self._idle_status())
        self.fps_var = tk.StringVar(value=str(settings.fps))
        self.video_bitrate_var = tk.StringVar(
            value=self._format_bitrate_mbps(settings.video_bitrate_mbps)
        )
        self.audio_bitrate_var = tk.StringVar(value=str(settings.audio_bitrate_kbps))
        self.system_audio_var = tk.BooleanVar(value=settings.system_audio_enabled)
        self.microphone_var = tk.BooleanVar(value=settings.microphone_enabled)
        self.record_cursor_var = tk.BooleanVar(value=settings.record_cursor)
        self.system_device_var = tk.StringVar()
        self.microphone_device_var = tk.StringVar()
        self._include_cursor = settings.record_cursor
        self._dpi_scale = 1.0
        self._header_icon_image: tk.PhotoImage | None = None
        self._audio_note_visible = False

        self._configure_window()
        self._build_ui()
        self._fit_and_center_window()
        self.duration_text.trace_add("write", self._on_duration_text_changed)
        self._choose_duration(settings.duration_seconds)
        self._on_duration_text_changed()
        self._set_status(self._idle_status(), _MUTED)
        self._refresh_audio_devices(
            preferred_system_id=settings.system_audio_device or None,
            preferred_microphone_id=settings.microphone_device or None,
        )
        self._fit_and_center_window()
        if self._initial_notice:
            self._set_status(self._initial_notice, self._initial_notice_color)
        # 首次启动也立即生成程序目录下的 settings.json，用户无需关闭程序
        # 就能看到并备份自己的选项。
        self._save_settings()
        self.root.bind("<Unmap>", self._on_root_unmap, add="+")
        self._poll_job = self.root.after(100, self._poll_events)

    @staticmethod
    def _format_bitrate_mbps(value: float) -> str:
        rounded = round(value, 1)
        if abs(value - rounded) < 1e-6:
            return f"{rounded:.1f}"
        return f"{value:g}"

    def _s(self, px: int) -> int:
        return max(1, round(px * self._dpi_scale))

    @staticmethod
    def _idle_status() -> str:
        return tr(
            "录制时自动隐藏到系统托盘；最小化窗口也会隐藏到托盘。",
            "Recording hides to the system tray; minimizing also hides it.",
        )

    @staticmethod
    def _resolve_initial_directory(settings: AppSettings) -> tuple[str, str, str]:
        """默认视频文件夹不可写时，自动切换到程序目录下的可写输出目录。"""

        default_directory = default_videos_dir()
        saved = settings.output_dir.strip()
        uses_default = not saved
        if saved:
            try:
                uses_default = (
                    str(Path(saved).resolve()).lower()
                    == str(default_directory.resolve()).lower()
                )
            except OSError:
                uses_default = False
        if not uses_default:
            return saved, "", _MUTED

        try:
            return str(ensure_output_dir(default_directory)), "", _MUTED
        except OutputDirectoryError as exc:
            try:
                fallback = fallback_output_dir()
            except OutputDirectoryError:
                return str(default_directory), str(exc), _WARN
            return (
                str(fallback),
                tr(
                    "默认视频文件夹不可写，已切换到：{path}",
                    "The default Videos folder is not writable. Switched to: {path}",
                    path=fallback,
                ),
                _INFO,
            )

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
            self._dpi_scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            self._dpi_scale = 1.0

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
        try:
            scaled_png = load_scaled_icon_png(ICON_PNG, self._s(48))
            self._header_icon_image = (
                tk.PhotoImage(master=self.root, data=scaled_png)
                if scaled_png is not None
                else self._icon_image
            )
        except tk.TclError:
            self._header_icon_image = self._icon_image

    def _fit_and_center_window(self) -> None:
        self.root.update_idletasks()
        width = max(self._s(668), self.root.winfo_reqwidth())
        height = self.root.winfo_reqheight() + self._s(2)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        height = min(height, max(self._s(480), screen_height - 80))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _build_ui(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()
        self._audio_note_visible = False

        header = tk.Frame(self.root, bg=_HEADER_BG)
        header.pack(fill="x")
        header_inner = tk.Frame(header, bg=_HEADER_BG)
        header_inner.pack(fill="x", padx=self._s(20), pady=self._s(12))

        if self._header_icon_image is not None:
            tk.Label(
                header_inner, image=self._header_icon_image, bg=_HEADER_BG, bd=0
            ).pack(side="left", padx=(0, self._s(14)))

        language_box = tk.Frame(header_inner, bg=_HEADER_BG)
        language_box.pack(side="right", padx=(self._s(12), 0))
        tk.Label(
            language_box,
            text=tr("语言", "Language"),
            bg=_HEADER_BG,
            fg="#94A3B8",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(8)))
        self._language_combo = RoundedDropdown(
            language_box,
            textvariable=self.language_var,
            values=[label for _code, label in LANGUAGES],
            on_select=self._on_language_selected,
            parent_bg=_HEADER_BG,
            field_bg="#FFFFFF",
            border=None,
            hover_border=None,
            active_border=_INFO,
            fg=_TEXT,
            arrow_color=_MUTED,
            font=("Microsoft YaHei UI", 9),
            width=self._s(78),
            height=self._s(30),
            radius=self._s(6),
            pad_x=self._s(10),
        )
        self._language_combo.pack(side="left")

        title_area = tk.Frame(header_inner, bg=_HEADER_BG)
        title_area.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_area,
            text="QuickRec",
            bg=_HEADER_BG,
            fg="#F8FAFC",
            font=("Microsoft YaHei UI", 18, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_area,
            text=tr(
                "框选区域 · 自定义时长 · 画面与声音",
                "Select an area · Custom duration · Video & audio",
            ),
            bg=_HEADER_BG,
            fg="#94A3B8",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill="x", pady=(self._s(2), 0))

        body = tk.Frame(self.root, bg=_BG)
        body.pack(
            fill="both",
            expand=True,
            padx=self._s(18),
            pady=(self._s(12), self._s(10)),
        )

        self._build_duration_card(body)
        self._build_quality_card(body)
        self._build_output_card(body)

        self._start_button = RoundedButton(
            body,
            text=tr("开始录制", "Start recording"),
            command=self._start_flow,
            parent_bg=_BG,
            fill=_ACCENT,
            hover_fill=_ACCENT_HOVER,
            active_fill=_ACCENT_ACTIVE,
            fg="#FFFFFF",
            font=("Microsoft YaHei UI", 12, "bold"),
            height=self._s(47),
            radius=self._s(9),
            shadow_color=_ACCENT,
            shadow_alpha=0.22,
            shadow_blur=4.5,
            shadow_offset_y=2.0,
            pad_x=1,
            pad_top=1,
            pad_bottom=6,
        )
        self._start_button.pack(fill="x", pady=(self._s(2), self._s(4)))

        self.status_label = tk.Label(
            body,
            textvariable=self.status_var,
            bg=_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="center",
            justify="center",
            wraplength=self._s(620),
        )
        self.status_label.pack(fill="x", pady=(0, self._s(2)))

        self._path_entry.bind("<FocusIn>", lambda _event: self._clear_status())
        self._path_entry.bind("<Return>", lambda _event: self._start_flow())

    def _build_duration_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        title_row = tk.Frame(card, bg=_CARD_BG)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text=tr("录制时长", "Duration"),
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            title_row,
            text=tr(
                f"1–{MAX_DURATION} 秒 / MAX",
                f"1–{MAX_DURATION} sec / MAX",
            ),
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right")

        row = tk.Frame(card, bg=_CARD_BG)
        row.pack(fill="x", pady=(self._s(8), 0))
        self._duration_buttons: dict[int, RoundedButton] = {}
        presets = [(0, "MAX")] + [
            (seconds, tr(f"{seconds} 秒", f"{seconds}s"))
            for seconds in DURATION_OPTIONS
        ]
        for seconds, label in presets:
            button = RoundedButton(
                row,
                text=label,
                command=lambda value=seconds: self._choose_duration(value),
                parent_bg=_CARD_BG,
                fill=_BTN_NEUTRAL,
                hover_fill=_BTN_NEUTRAL_HOVER,
                active_fill=_BTN_NEUTRAL_ACTIVE,
                fg=_TEXT,
                font=("Microsoft YaHei UI", 10, "bold"),
                width=self._s(88),
                height=self._s(40),
                radius=self._s(8),
                shadow_blur=3.8,
                shadow_offset_y=1.8,
                pad_x=4,
                pad_top=1,
                pad_bottom=5,
            )
            button.pack(side="left", padx=(0, self._s(1)))
            self._duration_buttons[seconds] = button

        custom_group = tk.Frame(row, bg=_CARD_BG)
        custom_group.pack(side="right", pady=(0, self._s(3)))
        tk.Label(
            custom_group,
            text=tr("自定义", "Custom"),
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(6)))
        duration_input = RoundedEntry(
            custom_group,
            textvariable=self.duration_text,
            parent_bg=_CARD_BG,
            field_bg=_FIELD_BG,
            border=_INPUT_BORDER,
            focus_border=_INFO,
            fg=_TEXT,
            font=("Consolas", 10),
            width=self._s(54),
            height=self._s(32),
            radius=self._s(6),
            justify="center",
            pad_x=self._s(6),
            step_callback=self._step_duration,
        )
        duration_input.pack(side="left")
        tk.Label(
            custom_group,
            text=tr("秒", "sec"),
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(self._s(6), 0))

    def _build_quality_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        title_row = tk.Frame(card, bg=_CARD_BG)
        title_row.pack(fill="x")
        tk.Label(
            title_row,
            text=tr("画面与声音", "Video & audio"),
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            title_row,
            text=tr("刷新设备", "Refresh devices"),
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
        quality.pack(fill="x", pady=(self._s(8), self._s(10)))
        self._inline_field(
            quality,
            tr("帧率", "FPS"),
            self.fps_var,
            tr("FPS", ""),
            width=self._s(44),
            step_callback=self._step_fps,
        ).pack(side="left", padx=(0, self._s(14)))
        self._inline_field(
            quality,
            tr("视频码率", "Bitrate"),
            self.video_bitrate_var,
            "Mbps",
            width=self._s(52),
            step_callback=self._step_video_bitrate,
        ).pack(side="left", padx=(0, self._s(14)))
        self._inline_field(
            quality,
            tr("音频", "Audio"),
            self.audio_bitrate_var,
            "kbps",
            width=self._s(48),
            step_callback=self._step_audio_bitrate,
        ).pack(side="left")

        self._cursor_check = RoundedCheckbox(
            quality,
            text=tr("录制鼠标指针", "Record cursor"),
            variable=self.record_cursor_var,
            bg=_CARD_BG,
            fg=_TEXT,
            disabled_fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            box_size=self._s(16),
            radius=self._s(4),
        )
        self._cursor_check.pack(side="right")

        check_width = self._s(104 if self._language == "zh" else 118)

        system_row = tk.Frame(card, bg=_CARD_BG)
        system_row.pack(fill="x", pady=(0, self._s(8)))
        self._system_check = RoundedCheckbox(
            system_row,
            text=tr("系统声音", "System audio"),
            variable=self.system_audio_var,
            command=self._sync_audio_controls,
            bg=_CARD_BG,
            fg=_TEXT,
            disabled_fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            box_size=self._s(16),
            radius=self._s(4),
            width=check_width,
        )
        self._system_check.pack(side="left")
        self._system_combo = RoundedDropdown(
            system_row,
            textvariable=self.system_device_var,
            parent_bg=_CARD_BG,
            field_bg="#FFFFFF",
            disabled_bg=_BG,
            border=_INPUT_BORDER,
            hover_border="#CBD5E1",
            active_border=_INFO,
            fg=_TEXT,
            disabled_fg="#94A3B8",
            arrow_color=_MUTED,
            font=("Microsoft YaHei UI", 9),
            height=self._s(32),
            radius=self._s(6),
            pad_x=self._s(10),
        )
        self._system_combo.pack(side="left", fill="x", expand=True)

        microphone_row = tk.Frame(card, bg=_CARD_BG)
        microphone_row.pack(fill="x")
        self._microphone_check = RoundedCheckbox(
            microphone_row,
            text=tr("麦克风", "Microphone"),
            variable=self.microphone_var,
            command=self._sync_audio_controls,
            bg=_CARD_BG,
            fg=_TEXT,
            disabled_fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
            box_size=self._s(16),
            radius=self._s(4),
            width=check_width,
        )
        self._microphone_check.pack(side="left")
        self._microphone_combo = RoundedDropdown(
            microphone_row,
            textvariable=self.microphone_device_var,
            parent_bg=_CARD_BG,
            field_bg="#FFFFFF",
            disabled_bg=_BG,
            border=_INPUT_BORDER,
            hover_border="#CBD5E1",
            active_border=_INFO,
            fg=_TEXT,
            disabled_fg="#94A3B8",
            arrow_color=_MUTED,
            font=("Microsoft YaHei UI", 9),
            height=self._s(32),
            radius=self._s(6),
            pad_x=self._s(10),
        )
        self._microphone_combo.pack(side="left", fill="x", expand=True)

        self._audio_note = tk.Label(
            card,
            text="",
            bg=_CARD_BG,
            fg=_WARN,
            font=("Microsoft YaHei UI", 8),
            anchor="w",
            justify="left",
            wraplength=self._s(590),
        )

    def _inline_field(
        self,
        parent: tk.Misc,
        label: str,
        variable: tk.StringVar,
        unit: str,
        *,
        width: int,
        step_callback: object = None,
    ) -> tk.Frame:
        group = tk.Frame(parent, bg=_CARD_BG)
        tk.Label(
            group,
            text=label,
            bg=_CARD_BG,
            fg=_MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=(0, self._s(5)))
        RoundedEntry(
            group,
            textvariable=variable,
            parent_bg=_CARD_BG,
            field_bg=_FIELD_BG,
            border=_INPUT_BORDER,
            focus_border=_INFO,
            fg=_TEXT,
            font=("Consolas", 9),
            width=width,
            height=self._s(28),
            radius=self._s(6),
            justify="center",
            pad_x=self._s(5),
            step_callback=step_callback if callable(step_callback) else None,
        ).pack(side="left")
        if unit:
            tk.Label(
                group,
                text=unit,
                bg=_CARD_BG,
                fg=_MUTED,
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left", padx=(self._s(5), 0))
        return group

    def _build_output_card(self, parent: tk.Misc) -> None:
        card = self._make_card(parent)
        output_header = tk.Frame(card, bg=_CARD_BG)
        output_header.pack(fill="x")
        tk.Label(
            output_header,
            text=tr("保存位置", "Save location"),
            bg=_CARD_BG,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            output_header,
            text=tr("打开文件夹", "Open folder"),
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

        path_row = tk.Frame(card, bg=_CARD_BG)
        path_row.pack(fill="x", pady=(self._s(8), 0))
        path_box = RoundedEntry(
            path_row,
            textvariable=self.output_var,
            parent_bg=_CARD_BG,
            field_bg=_FIELD_BG,
            border=_INPUT_BORDER,
            focus_border=_INFO,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9),
            height=self._s(34),
            radius=self._s(6),
            justify="left",
            pad_x=self._s(10),
        )
        path_box.pack(side="left", fill="x", expand=True)
        self._path_entry = path_box.entry

        RoundedButton(
            path_row,
            text=tr("浏览...", "Browse..."),
            command=self._browse_output,
            parent_bg=_CARD_BG,
            fill=_BTN_NEUTRAL,
            hover_fill=_BTN_NEUTRAL_HOVER,
            active_fill=_BTN_NEUTRAL_ACTIVE,
            fg=_TEXT,
            font=("Microsoft YaHei UI", 9, "bold"),
            width=self._s(80),
            height=self._s(34),
            radius=self._s(6),
        ).pack(side="right", padx=(self._s(8), 0))

    def _make_card(self, parent: tk.Misc) -> tk.Frame:
        card = RoundedCard(
            parent,
            parent_bg=_BG,
            card_bg=_CARD_BG,
            border=_BORDER,
            radius=self._s(10),
            pad_x=self._s(16),
            pad_y=(self._s(10), self._s(12)),
            shadow=True,
        )
        card.pack(fill="x", pady=(0, self._s(8)))
        return card.inner

    def _step_duration(self, delta: int) -> None:
        raw = self.duration_text.get().strip()
        if raw.upper() == "MAX":
            current = 60 if delta < 0 else 1
        else:
            try:
                current = int(raw) + delta
            except ValueError:
                current = self._duration_seconds or 10
        current = max(MIN_DURATION, min(MAX_DURATION, current))
        self.duration_text.set(str(current))

    def _step_fps(self, delta: int) -> None:
        try:
            current = int(self.fps_var.get().strip()) + delta
        except ValueError:
            current = self._video_settings.fps
        current = max(MIN_FPS, min(MAX_FPS, current))
        self.fps_var.set(str(current))

    def _step_video_bitrate(self, delta: int) -> None:
        try:
            current = float(self.video_bitrate_var.get().strip()) + delta * 0.5
        except ValueError:
            current = self._video_settings.bitrate_mbps
        current = max(MIN_VIDEO_BITRATE_MBPS, min(MAX_VIDEO_BITRATE_MBPS, current))
        self.video_bitrate_var.set(self._format_bitrate_mbps(current))

    def _step_audio_bitrate(self, delta: int) -> None:
        try:
            current = int(self.audio_bitrate_var.get().strip())
        except ValueError:
            current = self._audio_bitrate_kbps
        options = list(AUDIO_BITRATE_OPTIONS)
        if current in options:
            idx = options.index(current) + delta
        else:
            idx = sum(1 for val in options if val < current) + (0 if delta > 0 else -1)
        idx = max(0, min(len(options) - 1, idx))
        self.audio_bitrate_var.set(str(options[idx]))

    # ------------------------------------------------------------------
    # 时长、画质与设备选择
    # ------------------------------------------------------------------
    def _choose_duration(self, seconds: int) -> None:
        self.duration_text.set("MAX" if seconds == MAX_DURATION_SENTINEL else str(seconds))

    def _duration_from_input(self) -> int:
        text = self.duration_text.get().strip()
        if text.upper() == "MAX":
            return MAX_DURATION_SENTINEL
        return parse_duration(text)

    def _is_max_duration(self) -> bool:
        return self._duration_seconds == MAX_DURATION_SENTINEL

    def _on_language_selected(self, _event: object = None) -> None:
        if self._state != _IDLE:
            self.language_var.set(language_name(self._language))
            return
        selected = self.language_var.get()
        code = next(
            (value for value, label in LANGUAGES if label == selected),
            self._language,
        )
        if code == self._language:
            return
        system_id = self._selected_device_id(
            self._system_device_map, self.system_device_var.get()
        )
        microphone_id = self._selected_device_id(
            self._microphone_device_map, self.microphone_device_var.get()
        )
        try:
            duration = self._duration_from_input()
        except ValueError:
            duration = self._duration_seconds
        self._language = set_language(code)
        self.language_var.set(language_name(self._language))
        self._duration_seconds = duration
        self._build_ui()
        self._choose_duration(duration)
        self._on_duration_text_changed()
        self._fit_and_center_window()
        self._refresh_audio_devices(system_id, microphone_id)
        self._fit_and_center_window()
        self._set_status(self._idle_status(), _MUTED)
        self._save_settings()

    def _on_duration_text_changed(self, *_args: object) -> None:
        raw = self.duration_text.get().strip()
        if raw.upper() == "MAX":
            seconds = MAX_DURATION_SENTINEL
        else:
            try:
                seconds = parse_duration(raw)
            except ValueError:
                seconds = None
        for value, button in self._duration_buttons.items():
            if value == seconds:
                button.set_style(
                    fill=_ACCENT,
                    hover_fill=_ACCENT_HOVER,
                    active_fill=_ACCENT_ACTIVE,
                    fg="#FFFFFF",
                    shadow_color=_ACCENT,
                    shadow_alpha=0.22,
                )
            else:
                button.set_style(
                    fill=_BTN_NEUTRAL,
                    hover_fill=_BTN_NEUTRAL_HOVER,
                    active_fill=_BTN_NEUTRAL_ACTIVE,
                    fg=_TEXT,
                    shadow_color=None,
                    shadow_alpha=0.0,
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
        warning_text = ""
        if devices.error:
            warning_text = device_error_text(devices.error)
        elif not devices.system and not devices.microphones:
            warning_text = tr(
                "未检测到可用的音频设备；仍可录制无声视频。",
                "No audio devices found; you can still record a silent video.",
            )
        self._set_audio_note(warning_text)
        if not devices.error:
            self.system_audio_var.set(
                self.system_audio_var.get() and bool(devices.system)
            )
            self.microphone_var.set(
                self.microphone_var.get() and bool(devices.microphones)
            )
        self._sync_audio_controls()

    def _set_audio_note(self, text: str) -> None:
        should_show = bool(text)
        if should_show:
            self._audio_note.configure(text=text, fg=_WARN)
            if not self._audio_note_visible:
                self._audio_note.pack(fill="x", pady=(self._s(6), 0))
                self._audio_note_visible = True
                self._fit_and_center_window()
        elif self._audio_note_visible:
            self._audio_note.pack_forget()
            self._audio_note_visible = False
            self._fit_and_center_window()

    def _populate_device_combo(
        self,
        combo: RoundedDropdown,
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
            self._set_status(self._idle_status(), _MUTED)

    def _browse_output(self) -> None:
        initial = Path(self.output_var.get()).expanduser()
        initial_dir = initial if initial.is_dir() else default_videos_dir()
        selected = ask_directory(self.root, initial_dir)
        if selected is None:
            return
        try:
            directory = ensure_output_dir(selected)
        except OutputDirectoryError as exc:
            self._set_status(tr("所选文件夹不可写。", "The selected folder is not writable."), _WARN)
            messagebox.showerror(
                tr("文件夹不可写", "Folder not writable"), str(exc), parent=self.root
            )
            return
        self.output_var.set(str(directory))
        self._set_status(
            tr("保存位置已更新：{path}", "Save location updated: {path}", path=directory),
            _INFO,
        )

    def _open_output_folder(self) -> None:
        try:
            directory = ensure_output_dir(self.output_var.get())
            open_folder(directory)
        except (OutputDirectoryError, OSError) as exc:
            self._set_status(
                tr("无法打开保存文件夹。", "Cannot open the save folder."), _WARN
            )
            messagebox.showerror(
                tr("无法打开文件夹", "Cannot open folder"), str(exc), parent=self.root
            )

    def _start_flow(self) -> None:
        if self._state != _IDLE:
            return
        try:
            directory = ensure_output_dir(self.output_var.get())
            duration = self._duration_from_input()
            fps = parse_fps(self.fps_var.get())
            bitrate_mbps = parse_video_bitrate_mbps(self.video_bitrate_var.get())
            audio_bitrate = parse_audio_bitrate_kbps(
                self.audio_bitrate_var.get()
            )
            find_ffmpeg()
        except (OutputDirectoryError, ValueError, FFmpegError) as exc:
            self._set_status(str(exc), _WARN)
            messagebox.showerror(
                tr("设置无效", "Invalid settings"), str(exc), parent=self.root
            )
            return

        audio_selection = self._audio_selection_from_ui()
        if self.system_audio_var.get() and not audio_selection.system_enabled:
            self._set_status(
                tr(
                    "系统声音已开启但没有可用设备。",
                    "System audio is enabled but no device is available.",
                ),
                _WARN,
            )
            messagebox.showerror(
                tr("音频设备不可用", "Audio device unavailable"),
                tr(
                    "系统声音已开启，但没有选择可用设备。\n"
                    "请刷新设备、选择设备，或关闭系统声音。",
                    "System audio is enabled but no device is selected.\n"
                    "Refresh devices, pick a device, or turn system audio off.",
                ),
                parent=self.root,
            )
            return
        if self.microphone_var.get() and not audio_selection.microphone_enabled:
            self._set_status(
                tr(
                    "麦克风已开启但没有可用设备。",
                    "The microphone is enabled but no device is available.",
                ),
                _WARN,
            )
            messagebox.showerror(
                tr("音频设备不可用", "Audio device unavailable"),
                tr(
                    "麦克风已开启，但没有选择可用设备。\n"
                    "请刷新设备、选择设备，或关闭麦克风。",
                    "The microphone is enabled but no device is selected.\n"
                    "Refresh devices, pick a device, or turn the microphone off.",
                ),
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
        self._set_status(
            tr(
                "请在全屏遮罩上拖动鼠标，框选录制区域。",
                "Drag on the full-screen overlay to select the recording area.",
            ),
            _INFO,
        )
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
            self._set_status(
                tr("无法打开框选界面，请重试。", "Cannot open the region selector. Try again."),
                _WARN,
            )
            messagebox.showerror(
                tr("无法框选", "Cannot select a region"),
                tr(
                    "打开框选界面失败：\n{error}",
                    "Cannot open the region selector:\n{error}",
                    error=exc,
                ),
                parent=self.root,
            )

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
        self._set_status(
            tr("已取消框选，未开始录制。", "Selection cancelled. Recording did not start."),
            _MUTED,
        )

    # ------------------------------------------------------------------
    # 录制流程
    # ------------------------------------------------------------------
    def _begin_recording(self, region: CaptureRegion, output_path: Path) -> None:
        self._cancel_requested = False
        self._stop_requested = False
        self._exit_when_done = False
        self._include_cursor = bool(self.record_cursor_var.get())
        self._stop_event = threading.Event()

        countdown: CountdownOverlay | None = None
        try:
            countdown = CountdownOverlay(
                self.root,
                None if self._is_max_duration() else self._duration_seconds,
                on_stop=self._stop_recording_from_overlay,
            )
            countdown.show()
            self._countdown = countdown
        except Exception:
            LOGGER.exception("创建倒计时浮层失败")
            if countdown is not None:
                countdown.close()
            self._countdown = None
            self._abort_start(tr("无法创建录制倒计时浮层。", "Cannot create the countdown overlay."))
            return

        if not self._start_tray("recording"):
            LOGGER.warning("系统托盘不可用，改为最小化到任务栏")
            self._minimize_to_taskbar()

        self._state = _RECORDING
        self._recording_started_at = 0.0
        if self._countdown is not None:
            if self._is_max_duration():
                self._countdown.set_elapsed(0)
            else:
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
            self._abort_start(tr("无法启动录制线程，请重试。", "Cannot start the recording thread. Try again."))
            return

    def _record_worker(self, region: CaptureRegion, output_path: Path) -> None:
        try:
            result = record_session(
                region,
                output_path,
                None if self._is_max_duration() else self._duration_seconds,
                self._video_settings,
                self._audio_selection,
                self._audio_bitrate_kbps,
                stop_event=self._stop_event,
                on_started=lambda: self._events.put(
                    ("started", time.perf_counter())
                ),
                include_cursor=self._include_cursor,
            )
        except Exception as exc:
            LOGGER.exception("录制失败")
            self._events.put(("error", exc))
        else:
            self._events.put(("done", result))

    def _recording_menu(self) -> tuple[TrayMenuItem | None, ...]:
        return (
            (tr("停止并保存", "Stop and save"), "stop"),
            (tr("取消录制", "Cancel recording"), "cancel"),
            None,
            (tr("退出程序（放弃录制）", "Exit (discard recording)"), "exit"),
        )

    def _idle_menu(self) -> tuple[TrayMenuItem | None, ...]:
        return (
            (tr("显示主界面", "Show QuickRec"), "show"),
            None,
            (tr("退出程序", "Exit"), "exit"),
        )

    def _start_tray(self, mode: str) -> bool:
        if self._tray is not None:
            if self._tray_mode == mode:
                return True
            self._stop_tray()
        items = (
            self._recording_menu() if mode == "recording" else self._idle_menu()
        )
        tooltip = (
            tr("QuickRec 录制中 · 右键停止或取消", "QuickRec recording · right-click to stop")
            if mode == "recording"
            else tr("QuickRec · 右键显示或退出", "QuickRec · right-click to show or exit")
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
            if self._is_max_duration():
                self._countdown.set_elapsed(elapsed)
            else:
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
                if self._countdown is not None:
                    self._countdown.mark_stopping()
                if self._stop_event is not None:
                    self._stop_event.set()
            else:
                self._destroy()
            return
        if self._state != _RECORDING or self._stop_event is None:
            return
        if payload == "stop":
            LOGGER.info("用户从托盘请求提前停止录制")
            self._stop_requested = True
            if self._countdown is not None:
                self._countdown.mark_stopping()
            self._stop_event.set()
        elif payload == "cancel":
            LOGGER.info("用户从托盘取消录制")
            self._cancel_requested = True
            if self._countdown is not None:
                self._countdown.mark_stopping()
            self._stop_event.set()

    def _show_from_tray(self) -> None:
        if self._state != _IDLE:
            return
        self._stop_tray()
        self._restore_main()
        self._set_status(
            tr("已从系统托盘恢复。", "Restored from the system tray."), _MUTED
        )

    def _stop_recording_from_overlay(self) -> None:
        if self._state != _RECORDING or self._stop_event is None:
            return
        LOGGER.info("用户点击倒计时中的停止按钮")
        self._stop_requested = True
        self._stop_event.set()
        if self._countdown is not None:
            self._countdown.mark_stopping()

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
            self._handle_recording_error(RuntimeError(tr("录制结果无效。", "Invalid recording result.")))
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
            self._set_status(
                tr("已取消录制，视频未保存。", "Recording cancelled. No video was saved."),
                _MUTED,
            )
            return

        self._restore_main()
        self._state = _IDLE
        self._set_status(
            tr("录制完成：{name}", "Recording complete: {name}", name=payload.path.name),
            _SUCCESS,
        )
        self._show_completion_dialog(payload)

    def _handle_recording_error(self, payload: object) -> None:
        error = payload if isinstance(payload, BaseException) else RuntimeError(str(payload))
        cancelled = self._cancel_requested
        stop_requested = self._stop_requested
        exit_after = self._exit_when_done
        self._finish_recording_ui()
        if exit_after:
            self._destroy()
            return
        self._restore_main()
        self._state = _IDLE
        if cancelled:
            self._set_status(
                tr("已取消录制，视频未保存。", "Recording cancelled. No video was saved."),
                _MUTED,
            )
            return
        if stop_requested and isinstance(error, NoFramesError):
            self._set_status(
                tr(
                    "录制时间太短，未生成视频。",
                    "Recording was too short. No video was created.",
                ),
                _MUTED,
            )
            return
        LOGGER.error("录制失败：%s", error)
        self._set_status(
            tr(
                "录制失败，请检查设置后重试。",
                "Recording failed. Check the settings and try again.",
            ),
            _WARN,
        )
        messagebox.showerror(
            tr("录制失败", "Recording failed"), str(error), parent=self.root
        )

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
            size_text = tr("未知", "Unknown")
        duration_text = tr(
            "{seconds:.0f} 秒", "{seconds:.0f} s", seconds=result.duration_seconds
        )
        stop_note = (
            tr("\n已按请求提前停止。", "\nStopped early as requested.")
            if result.stopped_early
            else ""
        )
        audio_note = self._completion_audio_note(result)
        message = tr(
            "录制完成\n\n"
            "文件：{name}\n"
            "时长：{duration}\n"
            "大小：{size}\n"
            "画质：{fps} FPS · {bitrate:g} Mbps\n"
            "位置：{parent}{stop}{audio}\n\n"
            "是否打开所在文件夹？",
            "Recording complete\n\n"
            "File: {name}\n"
            "Duration: {duration}\n"
            "Size: {size}\n"
            "Quality: {fps} FPS · {bitrate:g} Mbps\n"
            "Location: {parent}{stop}{audio}\n\n"
            "Open the folder now?",
            name=result.path.name,
            duration=duration_text,
            size=size_text,
            fps=result.fps,
            bitrate=self._video_settings.bitrate_mbps,
            parent=result.path.parent,
            stop=stop_note,
            audio=audio_note,
        )
        LOGGER.info("录制完成：%s", result.path)
        open_now = messagebox.askyesno(
            tr("录制完成", "Recording complete"), message, parent=self.root
        )
        if open_now:
            try:
                open_folder(result.path.parent)
            except OSError as exc:
                messagebox.showerror(
                    tr("无法打开文件夹", "Cannot open folder"),
                    str(exc),
                    parent=self.root,
                )

    def _completion_audio_note(self, result: RecordingResult) -> str:
        sources = []
        if self._audio_selection.system_enabled:
            sources.append(tr("系统声音", "System audio"))
        if self._audio_selection.microphone_enabled:
            sources.append(tr("麦克风", "Microphone"))
        note = tr(
            "\n音频：{sources}",
            "\nAudio: {sources}",
            sources=" + ".join(sources) if sources else tr("无", "none"),
        )
        warning = format_audio_warning(result.audio_warnings)
        if warning:
            note += tr("\n注意：{warning}", "\nWarning: {warning}", warning=warning)
        return note

    def _abort_start(self, message: str) -> None:
        self._finish_recording_ui()
        self._restore_main()
        self._state = _IDLE
        self._set_status(message, _WARN)
        messagebox.showerror(
            tr("无法开始录制", "Cannot start recording"), message, parent=self.root
        )

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
            self._duration_seconds = self._duration_from_input()
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
                record_cursor=bool(self.record_cursor_var.get()),
                language=self._language,
            )
        )

    def _on_close(self) -> None:
        if self._state == _RECORDING:
            should_exit = messagebox.askyesno(
                tr("正在录制", "Recording in progress"),
                tr(
                    "当前正在录制，退出将取消本次录制。\n确定要退出吗？",
                    "Recording is in progress. Exiting will cancel it.\nExit anyway?",
                ),
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
                tr("程序错误", "Application error"),
                tr(
                    "界面发生未处理错误：\n{error}",
                    "An unhandled UI error occurred:\n{error}",
                    error=exception,
                ),
                parent=self.root,
            )
        except tk.TclError:
            pass


def device_error_text(error: str) -> str:
    if "soundcard" in error:
        return tr(
            "音频组件未安装：请执行 python -m pip install -e .，"
            "或使用项目 .venv\\Scripts\\python run.py 启动。",
            "Audio components are missing. Run python -m pip install -e ., "
            "or start with .venv\\Scripts\\python run.py.",
        )
    return tr("音频设备不可用：{error}", "Audio device unavailable: {error}", error=error)


def _configure_logging() -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        base = os.environ.get("LOCALAPPDATA")
        log_dir = (Path(base) if base else Path.home()) / "QuickRec"
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
