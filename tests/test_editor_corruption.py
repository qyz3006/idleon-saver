"""存档损坏（LevelDB CorruptionError）的优雅处理测试（tests/test_editor_corruption.py）。

覆盖：
- load_wrapped_json 在 LevelDB 损坏块（迭代 / 读取抛 CorruptionError）时，
  应抛 SaveCorruptedError 且不是裸 KeyError（修复3）。
- 编解码无损往返回归：最小合法 wrapped JSON 经 decode → encode → 再 decode 一致。

plyvel 导入用容错：idleon_saver.editor 已对 CorruptionError 做 fallback，
无 plyvel 的测试环境也能导入并构造同源的 CorruptionError 用于 mock。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from idleon_saver import editor
from idleon_saver.editor import CorruptionError, SaveCorruptedError
from idleon_saver.stencyl.decoder import StencylDecoder
from idleon_saver.stencyl.encoder import StencylEncoder


# --------------------------------------------------------------------------- #
# 假 LevelDB：任何读取 / 迭代都抛 CorruptionError（模拟损坏块）
# --------------------------------------------------------------------------- #
class _CorruptIterator:
    def __iter__(self):
        raise CorruptionError(b"Corruption: corrupted compressed block contents")

    def __next__(self):  # pragma: no cover - __iter__ 已抛
        raise CorruptionError(b"Corruption: corrupted compressed block contents")


class _CorruptDB:
    """模拟一个已损坏的 LevelDB：get / put / iterator 全抛 CorruptionError。"""

    def get(self, key):
        raise CorruptionError(b"Corruption: corrupted compressed block contents")

    def put(self, key, value):
        raise CorruptionError(b"Corruption: corrupted compressed block contents")

    def iterator(self):
        return _CorruptIterator()

    def close(self):
        pass


@pytest.fixture
def corrupt_leveldb(monkeypatch):
    """把 idleon_saver.editor.get_db 替换为一个损坏 db 的上下文管理器。"""
    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        yield _CorruptDB()
    monkeypatch.setattr(editor, "get_db", _fake_get_db)


# --------------------------------------------------------------------------- #
# 修复3：损坏应抛 SaveCorruptedError（而非裸 KeyError）
# --------------------------------------------------------------------------- #
def test_scan_corruption_raises_save_corrupted(corrupt_leveldb, tmp_path):
    """扫描分支撞到损坏块：应抛 SaveCorruptedError，绝不冒泡成 KeyError。"""
    with pytest.raises(SaveCorruptedError):
        editor.load_wrapped_json(tmp_path)  # idleon=None → 走扫描分支


def test_get_corruption_raises_save_corrupted(corrupt_leveldb, tmp_path, monkeypatch):
    """idleon 给定分支在读取值时损坏：同样抛 SaveCorruptedError。"""
    monkeypatch.setattr(editor, "db_key", lambda idleon: b"fixed_key")
    with pytest.raises(SaveCorruptedError):
        editor.load_wrapped_json(tmp_path, idleon=Path("/games/idleon"))


def test_corruption_is_not_keyerror(corrupt_leveldb, tmp_path):
    """SaveCorruptedError 必须与 KeyError 区分开，避免被通用分支误吞。"""
    with pytest.raises(SaveCorruptedError):
        try:
            editor.load_wrapped_json(tmp_path)
        except KeyError as exc:  # pragma: no cover
            pytest.fail(f"损坏应抛 SaveCorruptedError，却抛了 KeyError：{exc}")


# --------------------------------------------------------------------------- #
# 修复4：错误信息必须包含 plyvel 原始错误文本（便于区分真损坏 vs 格式不兼容）
# --------------------------------------------------------------------------- #
def test_corruption_error_includes_raw_plyvel_message(corrupt_leveldb, tmp_path):
    """SaveCorruptedError 的 message 必须包含 plyvel 原始错误文本。"""
    with pytest.raises(SaveCorruptedError) as exc_info:
        editor.load_wrapped_json(tmp_path)
    msg = str(exc_info.value)
    # 原始 CorruptionError 的消息文本必须出现在最终报错里
    assert "Corruption: corrupted compressed block contents" in msg, (
        f"报错未包含 plyvel 原始错误文本：{msg}"
    )
    # 还应提示格式不兼容可能性与备份还原建议
    assert "格式" in msg or "备份" in msg


def test_repair_fallback_attempted_on_corruption(corrupt_leveldb, tmp_path, monkeypatch):
    """主路径 CorruptionError 时应触发修复副本回退（_try_repair_and_read）。"""
    called = {"count": 0}

    def _fake_repair(ldb, idleon):
        called["count"] += 1
        return None, RuntimeError("repair simulated fail")

    monkeypatch.setattr(editor, "_try_repair_and_read", _fake_repair)
    with pytest.raises(SaveCorruptedError):
        editor.load_wrapped_json(tmp_path)
    assert called["count"] == 1, "主路径 CorruptionError 时必须调用修复回退"


def test_repair_fallback_success_returns_data(tmp_path, monkeypatch):
    """修复回退成功时直接返回其结果，不抛 SaveCorruptedError。"""
    fake_wrapped = {"start": "o", "contents": {"x": {"start": "i", "contents": 1}}, "end": "g"}

    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        yield _CorruptDB()

    monkeypatch.setattr(editor, "get_db", _fake_get_db)
    monkeypatch.setattr(
        editor,
        "_try_repair_and_read",
        lambda ldb, idleon: (fake_wrapped, None),
    )
    result = editor.load_wrapped_json(tmp_path)
    assert result is fake_wrapped


# --------------------------------------------------------------------------- #
# 回归：最小合法 wrapped JSON 的无损往返（decode → encode → 再 decode）
# --------------------------------------------------------------------------- #
def test_minimal_wrapped_roundtrip():
    # 由一段极简 stencyl 字符串解码得到的最小合法 wrapped JSON。
    wrapped = StencylDecoder("oy1:ai1g").result.wrapped
    stencyl = StencylEncoder(wrapped).result
    assert StencylDecoder(stencyl).result.wrapped == wrapped
