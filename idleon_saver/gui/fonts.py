"""Register a CJK-capable font as Kivy's default so Chinese text renders.

Why this module exists
----------------------
Kivy ships with ``Roboto`` as its default font. Roboto is a Latin-only
typeface with **zero** CJK (Chinese / Japanese / Korean) glyphs, so every
Chinese character in the GUI renders as a tofu box (``□□□``) unless we
explicitly override ``kivy.default_font`` with a CJK-capable font file.

Previous attempts in this codebase removed per-widget ``font_name`` settings
and relied on "the user's machine default font" — but that does not work:
Kivy never falls back to system fonts for missing glyphs, it always uses the
configured ``default_font`` (Roboto by default).

How we fix it
-------------
:func:`setup_cjk_font` locates a CJK font file on the host (Windows ships
several) and registers it via ``Config.set("kivy", "default_font", ...)``
**before** any Kivy widget is imported. Every widget (``Label``, ``Button``,
``TextInput``, ...) then uses that font by default — no per-widget
``font_name`` needed.

Why absolute paths (and not font names)
---------------------------------------
The earlier crashes in frozen (PyInstaller) builds came from passing a font
*name* like ``"Courier New"`` to a widget's ``font_name``. In a frozen build
Kivy's ``resolve_font_name`` does not search the Windows fonts directory, so
the name is not found → ``OSError`` → ``KeyError`` during error handling →
startup crash.

We sidestep this entirely by passing an **absolute path that exists** to
``default_font``. ``resolve_font_name`` checks ``os.path.exists(fontname)``
first; an existing absolute path is used directly and the broken
``resource_find`` fallback is never reached. This works identically in dev
and frozen state.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# CJK font search list, highest priority first.
# Order rationale:
#   - msyh.ttc  : Microsoft YaHei — modern, clean, default Win UI font (Vista+).
#                 .ttc is fine: the sdl2 text provider (confirmed in logs) loads
#                 face index 0 (YaHei Regular) without issue.
#   - simhei.ttf: SimHei — classic, pure .ttf, present since Windows 95. The
#                 safest single-file fallback if .ttc handling ever regresses.
#   - simsun.ttc: SimSun — classic serif, very widely installed.
#   - Deng.ttf  : DengXian — Win 10 default for some Office docs.
#   - msyhbd.ttc: YaHei Bold — only if regular is somehow missing.
_WIN_CJK_FONT_CANDIDATES: Tuple[str, ...] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simkai.ttf",
)


def _candidate_fonts() -> List[str]:
    """Build the ordered list of CJK font candidates for the current OS.

    On non-Windows hosts we still try a couple of common Linux/macOS CJK font
    locations so the helper degrades gracefully rather than silently no-op'ing.
    """
    if sys.platform == "win32":
        return list(_WIN_CJK_FONT_CANDIDATES)
    # Non-Windows best-effort (this app is Windows-targeted, but be tidy).
    return [
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # WenQuanYi (Linux)
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Noto (Linux)
        "/System/Library/Fonts/PingFang.ttc",  # macOS
        "/System/Library/Fonts/STHeiti Light.ttc",  # macOS fallback
    ]


def find_cjk_font() -> Optional[str]:
    """Return the absolute path of the first available CJK font, or ``None``.

    The search is ordered by preference (see :data:`_WIN_CJK_FONT_CANDIDATES`).
    """
    for path in _candidate_fonts():
        if path and os.path.isfile(path):
            return path
    return None


def setup_cjk_font(config=None) -> Optional[str]:
    """Register a CJK font as Kivy's ``default_font``.

    Must be called **after** ``from kivy.config import Config`` and **before**
    any ``kivy.uix`` / ``kivy.app`` import, because ``LabelBase`` caches the
    default font file at import time.

    Args:
        config: Optional Kivy ``Config`` object. When ``None`` (the normal
            case) the function imports Kivy's ``Config`` lazily so this module
            stays import-safe on machines without Kivy (e.g. CI running the
            non-GUI test suite). Passing ``config`` explicitly lets tests
            inject a fake without importing Kivy.

    Returns:
        The absolute path of the font that was registered, or ``None`` if no
        CJK font was found (in which case Kivy keeps its bundled Roboto and
        Chinese will render as tofu — we log a warning but never crash).
    """
    font_path = find_cjk_font()
    if font_path is None:
        logger.warning(
            "No CJK font found on this system; Chinese text will render as "
            "tofu boxes. Searched: %s",
            ", ".join(_candidate_fonts()),
        )
        return None

    if config is None:
        try:
            from kivy.config import Config as config  # noqa: F811
        except ImportError:
            logger.warning(
                "Kivy is not installed; cannot register CJK font %s", font_path
            )
            return font_path

    # default_font is a 2-element list: [label, font_file_path].
    # LabelBase reads index [1] as the actual font file. The label at [0] is
    # arbitrary and only used for debugging / resource lookup.
    config.set("kivy", "default_font", ["CJK", font_path])
    logger.info("Registered CJK default font: %s", font_path)
    return font_path
