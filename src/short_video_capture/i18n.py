"""轻量中英文界面文案支持。

界面文案直接写成 ``tr("中文", "English")``，避免维护独立的翻译键目录。
切换语言时重建主界面即可；其余模块在真正显示文字时再调用 ``tr``。
"""

from __future__ import annotations

DEFAULT_LANGUAGE = "zh"
LANGUAGES = (("zh", "中文"), ("en", "English"))
_SUPPORTED = {code for code, _label in LANGUAGES}

_current = DEFAULT_LANGUAGE


def normalize_language(code: object) -> str:
    """返回受支持的语言代码，未知值回退到中文。"""
    if isinstance(code, str) and code.lower() in _SUPPORTED:
        return code.lower()
    return DEFAULT_LANGUAGE


def set_language(code: object) -> str:
    """设置当前界面语言，返回实际生效的代码。"""
    global _current
    _current = normalize_language(code)
    return _current


def get_language() -> str:
    return _current


def language_name(code: object) -> str:
    """返回语言的本地名称，用于语言菜单。"""
    normalized = normalize_language(code)
    for value, label in LANGUAGES:
        if value == normalized:
            return label
    return LANGUAGES[0][1]


def tr(zh: str, en: str, **kwargs: object) -> str:
    """按当前语言返回文案；``kwargs`` 可选择性地格式化占位符。"""
    text = en if _current == "en" else zh
    return text.format(**kwargs) if kwargs else text
