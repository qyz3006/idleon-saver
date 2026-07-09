"""字体工具：为存档编辑器提供「冻结态一定可解析」的等宽字体。

背景与根因
----------
冻结 exe 启动即崩溃，traceback 关键行::

    File "kivy\\core\\text\\__init__.py", line 359, in resolve_font_name
    OSError: Label: File 'Courier New.ttf' not found

根因是 ``main.kv`` 里 ``<EditorScreen>`` 的 JSON 编辑框写了
``font_name: "Courier New"``。kivy 的 ``resolve_font_name`` 不会去搜索系统
字体目录，冻结包里没有 ``Courier New.ttf``，于是 OSError；并在异常处理的
``_split_smart`` 中又抛出二级 ``KeyError: ''``，导致 App 启动即崩溃。

本模块的方案
------------
- 已把 kv 中的系统字体字面量移除（改用 kivy 默认字体即可正常解析）。
- 在 Python 侧把 kivy 自带的等宽字体注册为名字 ``IdleonMono``，再赋给编辑框，
  以保留等宽编辑体验。kivy 2.1.0 自带 ``RobotoMono-Regular.ttf``（位于其包的
  ``data/fonts/``，会随 PyInstaller 打进 ``_MEIPASS``；``main.main()`` 中已用
  ``resource_add_path(sys._MEIPASS)`` 注册该路径，故 ``resource_find`` 可解析）。
- 健壮性（核心：冻结 exe 启动绝不因字体崩溃）：
  * 候选字体列表探测多个文件名（兼容不同 kivy 版本），优先等宽；
  * 若 ``resource_find`` 找不到任何候选（极少见），则**不设置** ``font_name``，
    退回 kivy 默认字体（默认字体一定可解析），绝不让 ``font_name`` 指向不存在文件；
  * 整个过程用 ``try/except`` 包裹，任何异常一律回退默认字体，不向外抛出。

导入容错
--------
本模块对 kivy 的导入做了容错，便于在无 kivy 的环境（如 CI / 本仓库的沙箱测试）
中被导入并被单元测试 mock；在真实运行环境（含冻结 exe）中这些导入正常成功。
"""

from __future__ import annotations

# 候选等宽/兜底字体文件名，按优先级排列。
# - kivy 2.1.0 自带 RobotoMono-Regular.ttf（等宽），本仓库锁定 kivy==2.1.0；
# - 更早版本（1.x）自带 DejaVuSansMono.ttf（等宽）；
# - DejaVuSans.ttf 随 kivy 2.1 打包（非等宽兜底，但一定可解析）。
# 逐个探测，先找到哪个就用哪个，兼容不同 kivy 版本。
_MONO_FONT_CANDIDATES: tuple[str, ...] = (
    "RobotoMono-Regular.ttf",
    "DejaVuSansMono.ttf",
    "DejaVuSans.ttf",
)

# 注册到 kivy 的字体家族名，供 Python 侧赋给 widget.font_name 使用。
MONO_FONT_NAME = "IdleonMono"

# 容错导入：无 kivy 环境（沙箱 / CI）下置为 None，使本模块可被导入、可被测试。
try:
    from kivy.core.text import Label as _KivyLabel
    from kivy.resources import resource_find as _resource_find
    _KIVY_AVAILABLE = True
except Exception:  # noqa: BLE001 - 覆盖导入期任何异常，保持模块可导入
    _KivyLabel = None  # type: ignore[assignment]
    _resource_find = None  # type: ignore[assignment]
    _KIVY_AVAILABLE = False


def _resolve_mono_font_path() -> "str | None":
    """探测候选等宽字体，返回首个能被 ``resource_find`` 解析到的绝对路径。

    Returns:
        str: 字体文件绝对路径；若全部不可解析则返回 ``None``。
    """
    if not _KIVY_AVAILABLE or _resource_find is None:
        return None
    for candidate in _MONO_FONT_CANDIDATES:
        try:
            found = _resource_find(candidate)
        except Exception:  # noqa: BLE001 - 单个候选失败不应阻断其他候选
            found = None
        if found:
            return found
    return None


def register_idleon_mono() -> bool:
    """把首个可解析的等宽字体注册为 ``IdleonMono``。

    幂等：重复调用安全（会刷新同一名字的注册项）。注册失败（无 kivy 或找不到
    字体文件）时返回 ``False``，调用方应退回默认字体。

    Returns:
        bool: 注册成功返回 ``True``，否则 ``False``。
    """
    if not _KIVY_AVAILABLE or _KivyLabel is None:
        return False
    path = _resolve_mono_font_path()
    if not path:
        return False
    try:
        _KivyLabel.register(MONO_FONT_NAME, fn=path)
        return True
    except Exception:  # noqa: BLE001 - 任何异常都回退默认字体
        return False


def apply_mono_font_to(widget) -> None:
    """把等宽字体应用到给定控件（如 EditorScreen 的 json_input）。

    安全策略（核心：冻结 exe 启动绝不因字体崩溃）：
    1. 先尝试注册并应用打包的等宽字体 ``IdleonMono``；
    2. 任意一步失败，**绝不**设置 ``font_name``，保留 kivy 默认字体（一定可解析）。

    整个过程用 ``try/except`` 包裹，异常一律回退默认字体，不向外抛出。

    Args:
        widget: 任意拥有 ``font_name`` 属性的 kivy 控件；为 ``None`` 时直接返回。
    """
    if widget is None:
        return
    try:
        if register_idleon_mono():
            widget.font_name = MONO_FONT_NAME
        # 否则不设置 font_name，保持默认字体（一定可解析）
    except Exception:  # noqa: BLE001 - 绝不因字体问题中断 UI 启动
        # 回退：保持默认字体即可，无需处理
        pass
