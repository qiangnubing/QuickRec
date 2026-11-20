"""基于纯 Tkinter + NumPy 绘制的圆角与抗锯齿现代控件。

不依赖 Pillow 或第三方主题库，确保与 PyInstaller 打包配置完全兼容。
"""

from __future__ import annotations

import functools
import struct
import tkinter as tk
import zlib
from collections.abc import Callable, Sequence
from pathlib import Path
from tkinter import font as tkfont

import numpy as np

from .win32 import monitor_work_area_for_point, tk_geometry

# ------------------------------------------------------------------
# 纯 NumPy 抗锯齿 PNG 渲染工具
# ------------------------------------------------------------------


def _parse_hex(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _encode_png_rgba(rgba: np.ndarray) -> bytes:
    """将 (H, W, 4) uint8 RGBA 数组编码为标准 PNG 字节流。"""
    height, width, _ = rgba.shape
    raw = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), rgba.reshape(height, width * 4)],
        axis=1,
    ).tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, level=1))
        + chunk(b"IEND", b"")
    )


def _encode_png_rgb(rgb: np.ndarray) -> bytes:
    """将 (H, W, 3) uint8 RGB 数组编码为不透明 PNG 字节流。"""
    height, width, _ = rgb.shape
    raw = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), rgb.reshape(height, width * 3)],
        axis=1,
    ).tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, level=1))
        + chunk(b"IEND", b"")
    )


def _sd_rounded_box(
    dx: np.ndarray,
    dy: np.ndarray,
    half_w: float,
    half_h: float,
    radius: float,
) -> np.ndarray:
    """圆角矩形有向距离场（内部为负、外部为正）。"""
    qx = np.abs(dx) - (half_w - radius)
    qy = np.abs(dy) - (half_h - radius)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return outside + inside - radius


def _blend(canvas: np.ndarray, color: tuple[int, int, int], alpha: np.ndarray) -> None:
    """将单色按 alpha 遮罩混合到 float32 RGB 画布。"""
    a = alpha[:, :, None]
    col = np.array(color, dtype=np.float32).reshape(1, 1, 3)
    canvas *= 1.0 - a
    canvas += col * a


@functools.lru_cache(maxsize=512)
def render_rounded_rect_png(
    width: int,
    height: int,
    radius: float,
    fill: str,
    parent_bg: str,
    border: str | None = None,
    border_width: float = 1.0,
    shadow_color: str | None = None,
    shadow_alpha: float = 0.0,
    shadow_blur: float = 0.0,
    shadow_offset_y: float = 0.0,
    pad_x: int = 0,
    pad_top: int = 0,
    pad_bottom: int = 0,
    transparent_key: bool = False,
) -> bytes:
    """渲染抗锯齿圆角矩形（可选描边与柔和阴影），直接合成在父背景色上。

    当 ``transparent_key=True`` 时，外轮廓外部保持严格等于 ``parent_bg``（用于
    Windows ``-transparentcolor`` 透明抠图），内侧描边与填充之间仍保留抗锯齿。
    """
    w = max(2, int(width))
    h = max(2, int(height))

    canvas = np.empty((h, w, 3), dtype=np.float32)
    canvas[:, :] = _parse_hex(parent_bg)

    x0 = float(pad_x)
    x1 = float(w - pad_x)
    y0 = float(pad_top)
    y1 = float(h - pad_bottom)
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    hx = max(1.0, (x1 - x0) * 0.5)
    hy = max(1.0, (y1 - y0) * 0.5)
    r = min(float(radius), hx, hy)

    px = (np.arange(w, dtype=np.float32) + 0.5)[None, :]
    py = (np.arange(h, dtype=np.float32) + 0.5)[:, None]

    if not transparent_key and shadow_color and shadow_alpha > 0.0 and shadow_blur > 0.0:
        s_dist = _sd_rounded_box(
            px - cx,
            py - (cy + shadow_offset_y),
            max(1.0, hx - 0.6),
            max(1.0, hy - 0.6),
            r,
        )
        sigma = max(0.8, shadow_blur * 0.55)
        s_mask = shadow_alpha * np.exp(
            -(np.maximum(s_dist, 0.0) ** 2) / (2.0 * sigma * sigma)
        )
        _blend(canvas, _parse_hex(shadow_color), s_mask.astype(np.float32))

    dist = _sd_rounded_box(px - cx, py - cy, hx, hy, r)
    if transparent_key:
        outer_a = (dist <= 0.35).astype(np.float32)
    else:
        outer_a = np.clip(0.5 - dist, 0.0, 1.0)

    if border and border_width > 0.0:
        _blend(canvas, _parse_hex(border), outer_a)
        inner_a = np.clip(0.5 - (dist + border_width), 0.0, 1.0) * outer_a
        _blend(canvas, _parse_hex(fill), inner_a)
    else:
        _blend(canvas, _parse_hex(fill), outer_a)

    return _encode_png_rgb(np.clip(np.rint(canvas), 0, 255).astype(np.uint8))


@functools.lru_cache(maxsize=64)
def render_dot_png(
    size: int,
    fill: str,
    parent_bg: str,
    *,
    dot_radius: float | None = None,
    glow_alpha: float = 0.0,
    glow_blur: float = 0.0,
) -> bytes:
    """渲染抗锯齿圆点指示灯（可选柔和发光晕），用于录制状态提示。"""
    s = max(8, int(size))
    canvas = np.empty((s, s, 3), dtype=np.float32)
    canvas[:, :] = _parse_hex(parent_bg)

    px = (np.arange(s, dtype=np.float32) + 0.5)[None, :]
    py = (np.arange(s, dtype=np.float32) + 0.5)[:, None]
    c = s * 0.5
    r = float(dot_radius) if dot_radius is not None else s * 0.32
    dist = np.hypot(px - c, py - c) - r

    color = _parse_hex(fill)
    if glow_alpha > 0.0 and glow_blur > 0.0:
        sigma = max(0.8, glow_blur * 0.55)
        g_mask = glow_alpha * np.exp(
            -(np.maximum(dist, 0.0) ** 2) / (2.0 * sigma * sigma)
        )
        _blend(canvas, color, g_mask.astype(np.float32))

    dot_a = np.clip(0.5 - dist, 0.0, 1.0)
    _blend(canvas, color, dot_a)
    return _encode_png_rgb(np.clip(np.rint(canvas), 0, 255).astype(np.uint8))


def _sd_segment(
    px: np.ndarray,
    py: np.ndarray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> np.ndarray:
    """点到线段 (a -> b) 的距离场。"""
    pax = px - ax
    pay = py - ay
    bax = bx - ax
    bay = by - ay
    denom = bax * bax + bay * bay
    t = np.clip((pax * bax + pay * bay) / max(denom, 1e-6), 0.0, 1.0)
    return np.hypot(pax - bax * t, pay - bay * t)


@functools.lru_cache(maxsize=64)
def render_checkbox_png(
    size: int,
    checked: bool,
    disabled: bool,
    hovered: bool,
    radius: float,
    parent_bg: str,
    checked_bg: str = "#0F172A",
    checked_hover_bg: str = "#1E293B",
    disabled_checked_bg: str = "#94A3B8",
    unchecked_bg: str = "#FFFFFF",
    unchecked_border: str = "#CBD5E1",
    unchecked_hover_border: str = "#94A3B8",
    disabled_unchecked_bg: str = "#F1F5F9",
    disabled_unchecked_border: str = "#E2E8F0",
) -> bytes:
    """渲染现代圆角复选框图标（抗锯齿圆角 + 勾选符号）。"""
    s = max(12, int(size))
    canvas = np.empty((s, s, 3), dtype=np.float32)
    canvas[:, :] = _parse_hex(parent_bg)

    px = (np.arange(s, dtype=np.float32) + 0.5)[None, :]
    py = (np.arange(s, dtype=np.float32) + 0.5)[:, None]
    half = (s - 1.0) * 0.5
    c = s * 0.5
    r = min(float(radius), half)
    dist = _sd_rounded_box(px - c, py - c, half, half, r)
    outer_a = np.clip(0.5 - dist, 0.0, 1.0)

    if checked:
        fill = (
            disabled_checked_bg
            if disabled
            else (checked_hover_bg if hovered else checked_bg)
        )
        _blend(canvas, _parse_hex(fill), outer_a)

        # 绘制清晰抗锯齿对勾（相对比例坐标，适配任意 DPI 尺寸）
        d1 = _sd_segment(px, py, s * 0.27, s * 0.52, s * 0.43, s * 0.68)
        d2 = _sd_segment(px, py, s * 0.43, s * 0.68, s * 0.74, s * 0.34)
        stroke_half = max(0.85, s * 0.062)
        check_a = np.clip(0.5 - (np.minimum(d1, d2) - stroke_half), 0.0, 1.0)
        _blend(canvas, (255, 255, 255), check_a * outer_a)
    else:
        if disabled:
            fill = disabled_unchecked_bg
            border = disabled_unchecked_border
        else:
            fill = unchecked_bg
            border = unchecked_hover_border if hovered else unchecked_border
        _blend(canvas, _parse_hex(border), outer_a)
        inner_a = np.clip(0.5 - (dist + 1.25), 0.0, 1.0)
        _blend(canvas, _parse_hex(fill), inner_a)

    return _encode_png_rgb(np.clip(np.rint(canvas), 0, 255).astype(np.uint8))


def load_scaled_icon_png(path: Path, target_size: int) -> bytes | None:
    """将标准 8 位非交错 RGBA PNG 双线性平滑缩放为指定像素尺寸。"""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None

    pos = 8
    width = height = 0
    idat_parts: list[bytes] = []
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos : pos + 8])
        chunk_data = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, comp, flt, interlace = struct.unpack(
                ">IIBBBBB", chunk_data
            )
            if (bit_depth, color_type, comp, flt, interlace) != (8, 6, 0, 0, 0):
                return None
        elif tag == b"IDAT":
            idat_parts.append(chunk_data)
        elif tag == b"IEND":
            break

    if not width or not height or not idat_parts:
        return None
    if width == target_size and height == target_size:
        return data

    try:
        raw = zlib.decompress(b"".join(idat_parts))
    except zlib.error:
        return None

    stride = width * 4
    if len(raw) != height * (stride + 1):
        return None

    # 解码 PNG 行滤波（支持 None/Sub/Up/Average/Paeth）
    src = np.zeros((height, stride), dtype=np.uint8)
    for y in range(height):
        offset = y * (stride + 1)
        flt_type = raw[offset]
        row = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=offset + 1).copy()
        prev = src[y - 1] if y > 0 else np.zeros(stride, dtype=np.uint8)
        if flt_type == 0:
            src[y] = row
        elif flt_type == 1:
            out = row.astype(np.int32)
            for i in range(4, stride):
                out[i] = (out[i] + out[i - 4]) & 0xFF
            src[y] = out.astype(np.uint8)
        elif flt_type == 2:
            src[y] = ((row.astype(np.int32) + prev.astype(np.int32)) & 0xFF).astype(np.uint8)
        elif flt_type == 3:
            out = row.astype(np.int32)
            p = prev.astype(np.int32)
            for i in range(stride):
                left = out[i - 4] if i >= 4 else 0
                out[i] = (out[i] + ((left + p[i]) >> 1)) & 0xFF
            src[y] = out.astype(np.uint8)
        elif flt_type == 4:
            out = row.astype(np.int32)
            p = prev.astype(np.int32)
            for i in range(stride):
                a = out[i - 4] if i >= 4 else 0
                b = p[i]
                c = p[i - 4] if i >= 4 else 0
                pa = abs(b - c)
                pb = abs(a - c)
                pc = abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                out[i] = (out[i] + pr) & 0xFF
            src[y] = out.astype(np.uint8)
        else:
            return None

    img = src.reshape(height, width, 4).astype(np.float32)
    # 预乘 Alpha 后做双线性重采样，避免边缘黑晕
    alpha = img[:, :, 3:4] / 255.0
    premul = np.concatenate([img[:, :, :3] * alpha, img[:, :, 3:4]], axis=2)

    out_size = max(16, int(target_size))
    xs = (np.arange(out_size, dtype=np.float32) + 0.5) * (width / out_size) - 0.5
    ys = (np.arange(out_size, dtype=np.float32) + 0.5) * (height / out_size) - 0.5
    xs = np.clip(xs, 0.0, width - 1.0)
    ys = np.clip(ys, 0.0, height - 1.0)

    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, width - 1)
    y1 = np.minimum(y0 + 1, height - 1)
    wx = (xs - x0)[None, :, None]
    wy = (ys - y0)[:, None, None]

    top = premul[y0[:, None], x0[None, :]] * (1.0 - wx) + premul[y0[:, None], x1[None, :]] * wx
    bot = premul[y1[:, None], x0[None, :]] * (1.0 - wx) + premul[y1[:, None], x1[None, :]] * wx
    resized = top * (1.0 - wy) + bot * wy

    out_a = resized[:, :, 3:4]
    safe_a = np.where(out_a > 1e-3, out_a / 255.0, 1.0)
    rgb = np.where(out_a > 1e-3, resized[:, :, :3] / safe_a, 0.0)
    rgba = np.concatenate([rgb, out_a], axis=2)
    return _encode_png_rgba(np.clip(np.rint(rgba), 0, 255).astype(np.uint8))


# ------------------------------------------------------------------
# 自定义圆角控件
# ------------------------------------------------------------------


class RoundedCard(tk.Frame):
    """圆角卡片容器，带细边框和可选柔和投影或透明圆角外轮廓。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        parent_bg: str,
        card_bg: str = "#FFFFFF",
        border: str = "#E2E8F0",
        border_width: float = 1.0,
        radius: float = 10.0,
        pad_x: int = 16,
        pad_y: int | tuple[int, int] = (11, 13),
        shadow: bool = True,
        transparent_key: bool = False,
    ) -> None:
        super().__init__(parent, bg=parent_bg, bd=0, highlightthickness=0)
        self._parent_bg = parent_bg
        self._card_bg = card_bg
        self._border = border
        self._border_width = border_width
        self._radius = radius
        self._shadow = shadow and not transparent_key
        self._transparent_key = transparent_key
        self._bg_photo: tk.PhotoImage | None = None
        self._last_size = (0, 0)

        self._bg_canvas = tk.Canvas(
            self, bg=parent_bg, bd=0, highlightthickness=0
        )
        self._bg_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        self.inner = tk.Frame(self, bg=card_bg, bd=0, highlightthickness=0)
        self.inner.pack(fill="both", expand=True, padx=pad_x, pady=pad_y)
        self.inner.lift()
        self.bind("<Configure>", self._on_configure)

    def _on_configure(self, event: tk.Event) -> None:
        size = (event.width, event.height)
        if size == self._last_size or size[0] < 4 or size[1] < 4:
            return
        self._last_size = size
        self._redraw(size[0], size[1])

    def _redraw(self, width: int, height: int) -> None:
        png = render_rounded_rect_png(
            width,
            height,
            self._radius,
            self._card_bg,
            self._parent_bg,
            border=self._border,
            border_width=self._border_width,
            shadow_color="#0F172A" if self._shadow else None,
            shadow_alpha=0.045 if self._shadow else 0.0,
            shadow_blur=4.0 if self._shadow else 0.0,
            shadow_offset_y=1.5 if self._shadow else 0.0,
            pad_x=0 if self._transparent_key else 1,
            pad_top=0 if self._transparent_key else 1,
            pad_bottom=0 if self._transparent_key else (3 if self._shadow else 1),
            transparent_key=self._transparent_key,
        )
        self._bg_photo = tk.PhotoImage(master=self, data=png)
        self._bg_canvas.delete("all")
        self._bg_canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)


class RoundedButton(tk.Canvas):
    """抗锯齿圆角按钮，支持悬停、按下状态、自适应文本宽度和可选柔和阴影。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        command: Callable[[], None],
        parent_bg: str,
        fill: str,
        hover_fill: str,
        active_fill: str | None = None,
        fg: str = "#FFFFFF",
        disabled_fill: str = "#E2E8F0",
        disabled_fg: str = "#94A3B8",
        font: tuple[object, ...] = ("Microsoft YaHei UI", 10, "bold"),
        width: int | None = None,
        min_width: int = 0,
        text_pad_x: int = 16,
        height: int = 36,
        radius: float = 8.0,
        border: str | None = None,
        shadow_color: str | None = None,
        shadow_alpha: float = 0.0,
        shadow_blur: float = 5.0,
        shadow_offset_y: float = 2.0,
        pad_x: int = 0,
        pad_top: int = 0,
        pad_bottom: int = 0,
    ) -> None:
        self._tk_font = tkfont.Font(root=parent, font=font)
        self._auto_width = width is None
        self._min_width = min_width
        self._text_pad_x = text_pad_x
        self._pad_x = pad_x
        self._pad_top = pad_top
        self._pad_bottom = pad_bottom
        canvas_w = (
            width
            if width is not None
            else self._measure_auto_width(text)
        )
        super().__init__(
            parent,
            width=canvas_w,
            height=height,
            bg=parent_bg,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._parent_bg = parent_bg
        self._fill = fill
        self._hover_fill = hover_fill
        self._active_fill = active_fill or hover_fill
        self._fg = fg
        self._disabled_fill = disabled_fill
        self._disabled_fg = disabled_fg
        self._font = font
        self._radius = radius
        self._border = border
        self._shadow_color = shadow_color
        self._shadow_alpha = shadow_alpha
        self._shadow_blur = shadow_blur
        self._shadow_offset_y = shadow_offset_y
        self._state = "normal"
        self._hovered = False
        self._pressed = False
        self._photo: tk.PhotoImage | None = None
        self._size = (canvas_w, height)

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def _measure_auto_width(self, text: str) -> int:
        measured = self._tk_font.measure(text) + self._text_pad_x * 2 + self._pad_x * 2
        return max(self._min_width, measured)

    def set_style(
        self,
        *,
        fill: str,
        hover_fill: str,
        fg: str,
        active_fill: str | None = None,
        border: str | None = None,
        shadow_color: str | None = None,
        shadow_alpha: float = 0.0,
    ) -> None:
        self._fill = fill
        self._hover_fill = hover_fill
        self._active_fill = active_fill or hover_fill
        self._fg = fg
        self._border = border
        self._shadow_color = shadow_color
        self._shadow_alpha = shadow_alpha
        self._redraw()

    def set_text(self, text: str) -> None:
        self._text = text
        if self._auto_width:
            target_w = self._measure_auto_width(text)
            if target_w != self._size[0]:
                self.configure(width=target_w)
                self._size = (target_w, self._size[1])
        self._redraw()

    def set_state(self, state: str) -> None:
        self._state = state
        self.configure(cursor="hand2" if state == "normal" else "arrow")
        if state != "normal":
            self._hovered = False
            self._pressed = False
        self._redraw()

    def _on_configure(self, event: tk.Event) -> None:
        if event.width > 1 and event.height > 1:
            self._size = (event.width, event.height)
            self._redraw()

    def _on_enter(self, _event: tk.Event) -> None:
        if self._state != "normal":
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hovered or self._pressed:
            self._hovered = False
            self._pressed = False
            self._redraw()

    def _on_press(self, _event: tk.Event) -> None:
        if self._state != "normal":
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, event: tk.Event) -> None:
        if self._state != "normal":
            return
        was_pressed = self._pressed
        self._pressed = False
        inside = 0 <= event.x <= self._size[0] and 0 <= event.y <= self._size[1]
        self._hovered = inside
        self._redraw()
        if was_pressed and inside:
            self._command()

    def _redraw(self) -> None:
        w, h = self._size
        if w < 4 or h < 4:
            return
        if self._state != "normal":
            fill = self._disabled_fill
            fg = self._disabled_fg
            shadow_color = None
            shadow_alpha = 0.0
        elif self._pressed:
            fill = self._active_fill
            fg = self._fg
            shadow_color = self._shadow_color
            shadow_alpha = self._shadow_alpha * 0.7
        elif self._hovered:
            fill = self._hover_fill
            fg = self._fg
            shadow_color = self._shadow_color
            shadow_alpha = self._shadow_alpha
        else:
            fill = self._fill
            fg = self._fg
            shadow_color = self._shadow_color
            shadow_alpha = self._shadow_alpha

        png = render_rounded_rect_png(
            w,
            h,
            self._radius,
            fill,
            self._parent_bg,
            border=self._border,
            border_width=1.0 if self._border else 0.0,
            shadow_color=shadow_color,
            shadow_alpha=shadow_alpha,
            shadow_blur=self._shadow_blur,
            shadow_offset_y=self._shadow_offset_y,
            pad_x=self._pad_x,
            pad_top=self._pad_top,
            pad_bottom=self._pad_bottom,
        )
        self._photo = tk.PhotoImage(master=self, data=png)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)
        text_y = self._pad_top + (h - self._pad_top - self._pad_bottom) * 0.5
        self.create_text(
            w * 0.5,
            text_y,
            text=self._text,
            fill=fg,
            font=self._font,
            anchor="center",
        )


class RoundedEntry(tk.Frame):
    """圆角输入框，带聚焦高亮边框，支持可选的上下键微调回调。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        textvariable: tk.StringVar,
        parent_bg: str,
        field_bg: str = "#F8FAFC",
        border: str = "#D8E0EA",
        focus_border: str = "#2563EB",
        fg: str = "#0F172A",
        font: tuple[object, ...] = ("Microsoft YaHei UI", 9),
        width: int | None = None,
        height: int = 32,
        radius: float = 6.0,
        justify: str = "left",
        pad_x: int = 8,
        step_callback: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(
            parent,
            bg=parent_bg,
            width=width if width is not None else 120,
            height=height,
            bd=0,
            highlightthickness=0,
        )
        self.pack_propagate(False)
        self._parent_bg = parent_bg
        self._field_bg = field_bg
        self._border = border
        self._focus_border = focus_border
        self._radius = radius
        self._pad_x = pad_x
        self._focused = False
        self._photo: tk.PhotoImage | None = None
        self._size = (width if width is not None else 120, height)

        self._bg_canvas = tk.Canvas(
            self, bg=parent_bg, bd=0, highlightthickness=0, cursor="xterm"
        )
        self._bg_canvas.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            bg=field_bg,
            fg=fg,
            relief="flat",
            bd=0,
            highlightthickness=0,
            insertbackground=fg,
            selectbackground="#DBEAFE",
            selectforeground=fg,
            font=font,
            justify=justify,
        )
        self.entry.place(
            x=pad_x,
            rely=0.5,
            anchor="w",
            width=max(10, self._size[0] - 2 * pad_x),
        )

        self._bg_canvas.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", self._on_focus_in, add="+")
        self.entry.bind("<FocusOut>", self._on_focus_out, add="+")
        if step_callback is not None:
            self.entry.bind("<Up>", lambda _e: (step_callback(1), "break")[1])
            self.entry.bind("<Down>", lambda _e: (step_callback(-1), "break")[1])
        self.bind("<Configure>", self._on_configure)
        self._redraw()

    def _on_configure(self, event: tk.Event) -> None:
        if event.width < 4 or event.height < 4:
            return
        self._size = (event.width, event.height)
        self.entry.place_configure(
            x=self._pad_x,
            rely=0.5,
            anchor="w",
            width=max(10, event.width - 2 * self._pad_x),
        )
        self._redraw()

    def _on_focus_in(self, _event: tk.Event) -> None:
        self._focused = True
        self._redraw()

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._focused = False
        self._redraw()

    def _redraw(self) -> None:
        w, h = self._size
        if w < 4 or h < 4:
            return
        png = render_rounded_rect_png(
            w,
            h,
            self._radius,
            self._field_bg,
            self._parent_bg,
            border=self._focus_border if self._focused else self._border,
            border_width=1.2 if self._focused else 1.0,
        )
        self._photo = tk.PhotoImage(master=self, data=png)
        self._bg_canvas.delete("all")
        self._bg_canvas.create_image(0, 0, anchor="nw", image=self._photo)


class RoundedCheckbox(tk.Frame):
    """现代圆角复选框（选中时为深蓝底白色对勾）。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        text: str,
        variable: tk.BooleanVar,
        command: Callable[[], None] | None = None,
        bg: str = "#FFFFFF",
        fg: str = "#0F172A",
        disabled_fg: str = "#94A3B8",
        font: tuple[object, ...] = ("Microsoft YaHei UI", 9),
        box_size: int = 16,
        radius: float = 4.0,
        width: int | None = None,
    ) -> None:
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0, cursor="hand2")
        self._variable = variable
        self._command = command
        self._bg = bg
        self._fg = fg
        self._disabled_fg = disabled_fg
        self._box_size = box_size
        self._radius = radius
        self._state = "normal"
        self._hovered = False
        self._icon_photo: tk.PhotoImage | None = None

        if width is not None:
            self.configure(width=width, height=max(box_size + 10, 26))
            self.pack_propagate(False)

        self._icon_label = tk.Label(self, bg=bg, bd=0, highlightthickness=0)
        self._icon_label.pack(side="left")

        self._text_label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=font,
            bd=0,
            highlightthickness=0,
            anchor="w",
        )
        self._text_label.pack(side="left", padx=(7, 0))

        for widget in (self, self._icon_label, self._text_label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        self._trace_id: str | None = self._variable.trace_add(
            "write", lambda *_args: self._redraw()
        )
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._redraw()

    def configure(self, cnf: dict[str, object] | None = None, **kwargs: object) -> object:
        if "state" in kwargs:
            state = str(kwargs.pop("state"))
            self.set_state(state)
        if cnf is not None or kwargs:
            return super().configure(cnf, **kwargs)
        return None

    config = configure

    def set_state(self, state: str) -> None:
        self._state = state
        cursor = "hand2" if state == "normal" else ""
        super().configure(cursor=cursor)
        self._icon_label.configure(cursor=cursor)
        self._text_label.configure(
            cursor=cursor,
            fg=self._fg if state == "normal" else self._disabled_fg,
        )
        if state != "normal":
            self._hovered = False
        self._redraw()

    def _toggle(self, _event: tk.Event) -> None:
        if self._state != "normal":
            return
        self._variable.set(not bool(self._variable.get()))
        if self._command is not None:
            self._command()

    def _on_enter(self, _event: tk.Event) -> None:
        if self._state != "normal":
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hovered:
            self._hovered = False
            self._redraw()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self and self._trace_id is not None:
            try:
                self._variable.trace_remove("write", self._trace_id)
            except tk.TclError:
                pass
            self._trace_id = None

    def _redraw(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        png = render_checkbox_png(
            self._box_size,
            bool(self._variable.get()),
            self._state != "normal",
            self._hovered,
            self._radius,
            self._bg,
        )
        self._icon_photo = tk.PhotoImage(master=self, data=png)
        self._icon_label.configure(image=self._icon_photo)


class RoundedDropdown(tk.Canvas):
    """圆角下拉选择框，替代原生带方角灰色箭头的 ttk.Combobox。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        textvariable: tk.StringVar,
        values: Sequence[str] = (),
        on_select: Callable[[], None] | None = None,
        parent_bg: str,
        field_bg: str = "#FFFFFF",
        disabled_bg: str = "#F1F5F9",
        border: str | None = "#DCE3EE",
        hover_border: str | None = "#CBD5E1",
        active_border: str | None = "#2563EB",
        fg: str = "#0F172A",
        disabled_fg: str = "#94A3B8",
        arrow_color: str = "#64748B",
        font: tuple[object, ...] = ("Microsoft YaHei UI", 9),
        width: int | None = None,
        height: int = 32,
        radius: float = 6.0,
        pad_x: int = 10,
    ) -> None:
        canvas_w = width if width is not None else 180
        super().__init__(
            parent,
            width=canvas_w,
            height=height,
            bg=parent_bg,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self._variable = textvariable
        self._values: list[str] = list(values)
        self._on_select = on_select
        self._parent_bg = parent_bg
        self._field_bg = field_bg
        self._disabled_bg = disabled_bg
        self._border = border
        self._hover_border = hover_border
        self._active_border = active_border
        self._fg = fg
        self._disabled_fg = disabled_fg
        self._arrow_color = arrow_color
        self._font = font
        self._tk_font = tkfont.Font(root=self, font=font)
        self._radius = radius
        self._pad_x = pad_x
        self._state = "readonly"
        self._hovered = False
        self._popup: tk.Toplevel | None = None
        self._prev_grab: tk.Misc | None = None
        self._photo: tk.PhotoImage | None = None
        self._size = (canvas_w, height)

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

        self._trace_id: str | None = self._variable.trace_add(
            "write", lambda *_args: self._redraw()
        )
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._redraw()

    def configure(self, cnf: dict[str, object] | None = None, **kwargs: object) -> object:
        need_redraw = False
        if "values" in kwargs:
            raw_values = kwargs.pop("values")
            self._values = list(raw_values) if isinstance(raw_values, Sequence) else []
            need_redraw = True
        if "state" in kwargs:
            self._state = str(kwargs.pop("state"))
            super().configure(cursor="hand2" if self._is_enabled() else "")
            if not self._is_enabled():
                self._hovered = False
                self._close_popup()
            need_redraw = True
        result = super().configure(cnf, **kwargs) if (cnf is not None or kwargs) else None
        if need_redraw:
            self._redraw()
        return result

    config = configure

    def _is_enabled(self) -> bool:
        return self._state != "disabled"

    def _on_configure(self, event: tk.Event) -> None:
        if event.width > 1 and event.height > 1:
            self._size = (event.width, event.height)
            self._redraw()

    def _on_enter(self, _event: tk.Event) -> None:
        if not self._is_enabled():
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hovered:
            self._hovered = False
            self._redraw()

    def _on_click(self, _event: tk.Event) -> None:
        if not self._is_enabled() or not self._values:
            return
        if self._popup is not None:
            self._close_popup()
            return
        self._open_popup()

    def _open_popup(self) -> None:
        self._close_popup()
        popup = tk.Toplevel(self)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(bg="#CBD5E1")
        try:
            popup.attributes("-topmost", True)
        except tk.TclError:
            pass

        inner = tk.Frame(popup, bg="#FFFFFF", bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        current = self._variable.get()
        for item in self._values:
            is_selected = item == current
            bg_normal = "#EFF6FF" if is_selected else "#FFFFFF"
            fg_color = "#1D4ED8" if is_selected else self._fg
            label = tk.Label(
                inner,
                text=item,
                bg=bg_normal,
                fg=fg_color,
                font=self._font,
                anchor="w",
                padx=self._pad_x,
                pady=6,
                cursor="hand2",
            )
            label.pack(fill="x")
            label.bind(
                "<Enter>",
                lambda _e, lbl=label: lbl.configure(bg="#EFF6FF"),
            )
            label.bind(
                "<Leave>",
                lambda _e, lbl=label, bg=bg_normal: lbl.configure(bg=bg),
            )
            label.bind(
                "<Button-1>",
                lambda _e, val=item: self._choose(val),
            )

        popup.update_idletasks()
        req_w = max(self.winfo_width(), popup.winfo_reqwidth() + 8)
        req_h = popup.winfo_reqheight()
        root_x = self.winfo_rootx()
        root_y = self.winfo_rooty()
        area = monitor_work_area_for_point(root_x, root_y)

        y = root_y + self.winfo_height() + 3
        if y + req_h > area.bottom - 12:
            y = max(area.top + 8, root_y - req_h - 3)
        max_x = max(area.left + 8, area.right - req_w - 8)
        x = max(area.left + 8, min(root_x, max_x))

        popup.geometry(tk_geometry(req_w, req_h, x, y))
        popup.deiconify()
        popup.lift()
        popup.focus_set()
        try:
            current_grab = self.grab_current()
            self._prev_grab = current_grab if current_grab is not popup else None
        except tk.TclError:
            self._prev_grab = None
        try:
            popup.grab_set()
        except tk.TclError:
            pass

        popup.bind("<Escape>", lambda _e: self._close_popup())
        popup.bind("<ButtonPress-1>", self._on_popup_click)
        popup.bind("<FocusOut>", self._on_popup_focus_out)

        self._popup = popup
        self._redraw()

    def _on_popup_click(self, event: tk.Event) -> None:
        if self._popup is None:
            return
        px = self._popup.winfo_rootx()
        py = self._popup.winfo_rooty()
        pw = self._popup.winfo_width()
        ph = self._popup.winfo_height()
        if not (px <= event.x_root <= px + pw and py <= event.y_root <= py + ph):
            self._close_popup()

    def _on_popup_focus_out(self, _event: tk.Event) -> None:
        if self._popup is None:
            return
        try:
            focused = self._popup.focus_get()
        except tk.TclError:
            focused = None
        if focused is None or focused.winfo_toplevel() is not self._popup:
            self._close_popup()

    def _choose(self, value: str) -> None:
        self._close_popup()
        self._variable.set(value)
        if self._on_select is not None:
            self._on_select()

    def _close_popup(self) -> None:
        popup = self._popup
        self._popup = None
        prev_grab = self._prev_grab
        self._prev_grab = None
        if popup is not None:
            try:
                popup.grab_release()
            except tk.TclError:
                pass
            try:
                popup.destroy()
            except tk.TclError:
                pass
        if prev_grab is not None:
            try:
                if prev_grab.winfo_exists():
                    prev_grab.grab_set()
            except tk.TclError:
                pass
        self._redraw()

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self:
            self._close_popup()
            if self._trace_id is not None:
                try:
                    self._variable.trace_remove("write", self._trace_id)
                except tk.TclError:
                    pass
                self._trace_id = None

    def _elide_text(self, text: str, max_width: int) -> str:
        if not text or max_width <= 8:
            return ""
        if self._tk_font.measure(text) <= max_width:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._tk_font.measure(text[:mid] + ellipsis) <= max_width:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + ellipsis if lo > 0 else ellipsis

    def _redraw(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        w, h = self._size
        if w < 8 or h < 8:
            return

        enabled = self._is_enabled()
        fill = self._field_bg if enabled else self._disabled_bg
        fg = self._fg if enabled else self._disabled_fg
        if not enabled:
            border = self._border
        elif self._popup is not None and self._active_border:
            border = self._active_border
        elif self._hovered and self._hover_border:
            border = self._hover_border
        else:
            border = self._border

        png = render_rounded_rect_png(
            w,
            h,
            self._radius,
            fill,
            self._parent_bg,
            border=border,
            border_width=1.2 if (self._popup is not None and border) else (1.0 if border else 0.0),
        )
        self._photo = tk.PhotoImage(master=self, data=png)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)

        arrow_space = 24
        available = w - self._pad_x - arrow_space
        display_text = self._elide_text(self._variable.get(), available)
        self.create_text(
            self._pad_x,
            h * 0.5,
            text=display_text,
            fill=fg,
            font=self._font,
            anchor="w",
        )

        # 绘制右侧简洁小倒三角 ▾
        ax = w - 13
        ay = h * 0.5
        arrow_fill = self._arrow_color if enabled else self._disabled_fg
        self.create_polygon(
            ax - 3.5,
            ay - 2.0,
            ax + 3.5,
            ay - 2.0,
            ax,
            ay + 2.8,
            fill=arrow_fill,
            outline="",
        )


class RoundedScrollbar(tk.Canvas):
    """抗锯齿圆角垂直滚动条，替代原生方角 tk.Scrollbar。"""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        command: Callable[..., object] | None = None,
        bg: str = "#FFFFFF",
        thumb_color: str = "#CBD5E1",
        thumb_hover_color: str = "#94A3B8",
        thumb_active_color: str = "#64748B",
        width: int = 10,
        thumb_pad_x: int = 2,
        thumb_pad_y: int = 2,
        min_thumb_height: int = 24,
    ) -> None:
        super().__init__(
            parent,
            width=width,
            bg=bg,
            bd=0,
            highlightthickness=0,
        )
        self._command = command
        self._bg = bg
        self._thumb_color = thumb_color
        self._thumb_hover_color = thumb_hover_color
        self._thumb_active_color = thumb_active_color
        self._thumb_pad_x = thumb_pad_x
        self._thumb_pad_y = thumb_pad_y
        self._min_thumb_height = min_thumb_height
        self._first = 0.0
        self._last = 1.0
        self._size = (width, 1)
        self._thumb_bounds: tuple[int, int] | None = None
        self._hovered = False
        self._dragging = False
        self._drag_offset = 0.0
        self._photo: tk.PhotoImage | None = None

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def configure(self, cnf: dict[str, object] | None = None, **kwargs: object) -> object:
        if "command" in kwargs:
            cmd = kwargs.pop("command")
            self._command = cmd if callable(cmd) else None
        if cnf is not None or kwargs:
            return super().configure(cnf, **kwargs)
        return None

    config = configure

    def set(self, first: str | float, last: str | float) -> None:
        lo = max(0.0, min(1.0, float(first)))
        hi = max(lo, min(1.0, float(last)))
        if abs(lo - self._first) < 1e-6 and abs(hi - self._last) < 1e-6:
            return
        self._first = lo
        self._last = hi
        self._redraw()

    def _is_scrollable(self) -> bool:
        return not (self._first <= 1e-6 and self._last >= 1.0 - 1e-6)

    def _track_metrics(self) -> tuple[int, int, int, int, float]:
        _w, h = self._size
        track_top = self._thumb_pad_y
        track_h = max(1, h - 2 * self._thumb_pad_y)
        visible = max(0.0, min(1.0, self._last - self._first))
        thumb_h = max(min(self._min_thumb_height, track_h), round(track_h * visible))
        travel = max(0, track_h - thumb_h)
        scrollable = max(0.0, 1.0 - visible)
        return track_top, track_h, thumb_h, travel, scrollable

    def _on_configure(self, event: tk.Event) -> None:
        if event.width > 1 and event.height > 1:
            self._size = (event.width, event.height)
            self._redraw()

    def _on_enter(self, _event: tk.Event) -> None:
        if self._is_scrollable():
            self._hovered = True
            self._redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hovered and not self._dragging:
            self._hovered = False
            self._redraw()

    def _on_press(self, event: tk.Event) -> None:
        if not self._is_scrollable() or self._command is None:
            return
        bounds = self._thumb_bounds
        if bounds is not None and bounds[0] <= event.y <= bounds[1]:
            self._dragging = True
            self._drag_offset = event.y - bounds[0]
            self._redraw()
            return
        track_top, _track_h, thumb_h, travel, scrollable = self._track_metrics()
        self._dragging = True
        self._drag_offset = thumb_h * 0.5
        self._move_thumb_to(event.y - self._drag_offset, track_top, travel, scrollable)
        self._redraw()

    def _on_drag(self, event: tk.Event) -> None:
        if not self._dragging or self._command is None:
            return
        track_top, _track_h, _thumb_h, travel, scrollable = self._track_metrics()
        self._move_thumb_to(event.y - self._drag_offset, track_top, travel, scrollable)

    def _on_release(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        w, h = self._size
        self._hovered = (
            self._is_scrollable() and 0 <= event.x <= w and 0 <= event.y <= h
        )
        self._redraw()

    def _move_thumb_to(
        self,
        thumb_top: float,
        track_top: int,
        travel: int,
        scrollable: float,
    ) -> None:
        if self._command is None or travel <= 0 or scrollable <= 1e-6:
            return
        clamped = max(0.0, min(float(travel), thumb_top - track_top))
        fraction = (clamped / travel) * scrollable
        self._command("moveto", f"{fraction:.6f}")

    def _redraw(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        w, h = self._size
        self.delete("all")
        if w < 4 or h < 6 or not self._is_scrollable():
            self._thumb_bounds = None
            super().configure(cursor="")
            return

        track_top, _track_h, thumb_h, travel, scrollable = self._track_metrics()
        ratio = (self._first / scrollable) if scrollable > 1e-6 else 0.0
        thumb_y = track_top + round(travel * max(0.0, min(1.0, ratio)))
        thumb_w = max(4, w - 2 * self._thumb_pad_x)
        self._thumb_bounds = (thumb_y, thumb_y + thumb_h)
        super().configure(cursor="hand2")

        if self._dragging:
            fill = self._thumb_active_color
        elif self._hovered:
            fill = self._thumb_hover_color
        else:
            fill = self._thumb_color

        png = render_rounded_rect_png(
            thumb_w,
            thumb_h,
            thumb_w * 0.5,
            fill,
            self._bg,
        )
        self._photo = tk.PhotoImage(master=self, data=png)
        self.create_image(self._thumb_pad_x, thumb_y, anchor="nw", image=self._photo)
