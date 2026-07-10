"""Tests for CJK font registration (gui/fonts.py).

These tests do NOT require Kivy to be installed: they exercise the pure-Python
font-detection logic and inject a fake Config object to verify the
``default_font`` wiring without importing kivy.config.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

from idleon_saver.gui.fonts import find_cjk_font, setup_cjk_font


class TestFindCjkFont:
    def test_returns_existing_path_or_none(self):
        """find_cjk_font must return a path that exists, or None."""
        path = find_cjk_font()
        if path is None:
            # Non-Windows CI without CJK fonts: just assert contract holds.
            assert path is None
        else:
            assert os.path.isfile(path), f"Reported font does not exist: {path}"
            assert os.path.isabs(path), f"Font path is not absolute: {path}"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows font list")
    def test_windows_finds_at_least_one_cjk_font(self):
        """On Windows a CJK font should always be available."""
        path = find_cjk_font()
        assert path is not None, (
            "No CJK font found on Windows — Chinese will render as tofu. "
            "Expected one of msyh.ttc / simhei.ttf / simsun.ttc in C:/Windows/Fonts."
        )

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows font list")
    def test_prefers_microsoft_yahei(self):
        """YaHei (msyh.ttc) is the preferred modern CJK font on Windows."""
        path = find_cjk_font()
        # If YaHei exists it should win; otherwise fall back is acceptable.
        yahei = "C:/Windows/Fonts/msyh.ttc"
        if os.path.isfile(yahei):
            assert path == yahei, f"Expected YaHei first, got {path}"


class _FakeConfig:
    """Minimal stand-in for kivy.config.Config to avoid importing Kivy."""

    def __init__(self):
        self.set = MagicMock()

    def get(self, section, option):
        return self.set.call_args  # not used by these tests


class TestSetupCjkFont:
    def test_registers_font_with_injected_config(self):
        """setup_cjk_font must call config.set('kivy','default_font', [label, path])."""
        fake = _FakeConfig()
        result = setup_cjk_font(config=fake)

        font_path = find_cjk_font()
        if font_path is None:
            # No CJK font on this machine: should not call set, return None.
            assert result is None
            fake.set.assert_not_called()
            return

        assert result == font_path
        fake.set.assert_called_once_with(
            "kivy", "default_font", ["CJK", font_path]
        )

    def test_default_config_arg_imports_kivy_lazily(self):
        """Without kivy installed, default-arg path returns the path without crashing."""
        font_path = find_cjk_font()
        if font_path is None:
            pytest.skip("No CJK font available on this host")

        # Simulate "kivy not installed" by patching the lazy import to raise.
        import builtins

        real_import = builtins.__import__

        def _block_kivy(name, *args, **kwargs):
            if name == "kivy.config":
                raise ImportError("simulated: kivy not installed")
            return real_import(name, *args, **kwargs)

        builtins.__import__ = _block_kivy
        try:
            result = setup_cjk_font()  # config=None -> tries lazy import
        finally:
            builtins.__import__ = real_import

        # Should return the discovered path but NOT have registered it (no kivy).
        assert result == font_path

    def test_no_crash_when_no_font_found(self, monkeypatch):
        """If find_cjk_font returns None, setup must not crash and must not call set."""
        monkeypatch.setattr(
            "idleon_saver.gui.fonts.find_cjk_font", lambda: None
        )
        fake = _FakeConfig()
        result = setup_cjk_font(config=fake)
        assert result is None
        fake.set.assert_not_called()

    def test_registered_path_actually_exists(self):
        """The font path handed to Config must be a real file (frozen-exe safety)."""
        fake = _FakeConfig()
        result = setup_cjk_font(config=fake)
        if result is None:
            pytest.skip("No CJK font available")
        assert os.path.isfile(result), (
            f"Registered font path is not an existing file: {result}. "
            "Frozen builds rely on resolve_font_name treating an existing "
            "absolute path as a direct font source."
        )
