"""字体注册 / 回退逻辑的单元测试（tests/test_editor_font.py）。

验证 idleon_saver.gui.fonts 的两条核心分支：
- resource_find 能解析到字体 -> 注册 IdleonMono 并应用到控件；
- resource_find 全部返回 None（或 kivy 不可用）-> 不设置 font_name，保持默认。

本测试无需真实 kivy：通过 monkeypatch 替换模块内被容错导入的
``_resource_find`` / ``_KivyLabel`` / ``_KIVY_AVAILABLE`` 来模拟运行环境。
"""

from __future__ import annotations

from types import SimpleNamespace

import idleon_saver.gui.fonts as fonts


class _StubWidget:
    """模拟拥有 font_name 属性的 kivy 控件。"""

    font_name = "default"


def test_register_success_when_font_found(monkeypatch):
    """resource_find 返回路径时：应注册 IdleonMono 且 apply 设置 font_name。"""
    calls: list[tuple] = []

    def _fake_register(name, fn=None):
        calls.append((name, fn))

    monkeypatch.setattr(fonts, "_KIVY_AVAILABLE", True)
    monkeypatch.setattr(
        fonts, "_resource_find", lambda c: f"/kivy/data/fonts/{c}"
    )
    monkeypatch.setattr(fonts, "_KivyLabel", SimpleNamespace(register=_fake_register))

    # 应成功注册，且注册的是首个候选（RobotoMono-Regular.ttf）的解析路径
    assert fonts.register_idleon_mono() is True
    assert calls == [
        (fonts.MONO_FONT_NAME, "/kivy/data/fonts/RobotoMono-Regular.ttf")
    ]

    widget = _StubWidget()
    fonts.apply_mono_font_to(widget)
    assert widget.font_name == fonts.MONO_FONT_NAME


def test_apply_keeps_default_when_font_missing(monkeypatch):
    """resource_find 返回 None 时：不注册、不设置 font_name（保持默认）。"""
    calls: list[tuple] = []

    def _fake_register(name, fn=None):
        calls.append((name, fn))

    monkeypatch.setattr(fonts, "_KIVY_AVAILABLE", True)
    monkeypatch.setattr(fonts, "_resource_find", lambda c: None)
    monkeypatch.setattr(fonts, "_KivyLabel", SimpleNamespace(register=_fake_register))

    assert fonts.register_idleon_mono() is False
    assert calls == []  # 不应调用注册

    widget = _StubWidget()
    fonts.apply_mono_font_to(widget)
    assert widget.font_name == "default"  # 保持默认字体


def test_no_kivy_falls_back_to_default(monkeypatch):
    """kivy 不可用时：注册返回 False，apply 不改动 font_name。"""
    monkeypatch.setattr(fonts, "_KIVY_AVAILABLE", False)
    monkeypatch.setattr(fonts, "_resource_find", lambda c: "/x")  # 即便有路径也不应使用
    monkeypatch.setattr(fonts, "_KivyLabel", None)

    assert fonts.register_idleon_mono() is False

    widget = _StubWidget()
    fonts.apply_mono_font_to(widget)
    assert widget.font_name == "default"


def test_apply_with_none_widget_is_noop(monkeypatch):
    """widget 为 None 时：直接返回，不报错也不改动任何状态。"""
    monkeypatch.setattr(fonts, "_KIVY_AVAILABLE", True)
    monkeypatch.setattr(
        fonts, "_resource_find", lambda c: f"/kivy/data/fonts/{c}"
    )
    # 即便能注册，None widget 也不应导致异常
    fonts.apply_mono_font_to(None)
