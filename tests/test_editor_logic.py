"""编辑器纯逻辑单测（tests/test_editor_logic.py）。

覆盖：定位函数、进程检测（mock subprocess）、加载-编码往返（mock plyvel）、
validate wrapped、write_leveldb 的 0x01 前缀。

说明：本机无 plyvel / kivy，因此 editor.py 对 ldb 的 plyvel 依赖做了容错导入，
测试通过 monkeypatch ``idleon_saver.editor.get_db`` / ``db_key`` 隔离 LevelDB。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from idleon_saver import editor
from idleon_saver.utility import ROOT_DIR, chunk


# --------------------------------------------------------------------------- #
# 辅助：读取仓库内真实 Stencyl 存档样本（tests/data/stencylsave.txt）
# --------------------------------------------------------------------------- #
def _stencyl_text() -> str:
    return (ROOT_DIR / "tests" / "data" / "stencylsave.txt").read_text()


# --------------------------------------------------------------------------- #
# 定位函数（tests/test_editor_logic.py: locate）
# --------------------------------------------------------------------------- #
def test_locate_leveldb_found(monkeypatch, tmp_path):
    from idleon_saver.utility import locate_leveldb

    ldb = tmp_path / "legends-of-idleon" / "Local Storage" / "leveldb"
    ldb.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert locate_leveldb() == ldb


def test_locate_leveldb_missing(monkeypatch, tmp_path):
    from idleon_saver.utility import locate_leveldb

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert locate_leveldb() is None


def test_locate_idleon_install_missing():
    from idleon_saver.utility import locate_idleon_install

    # 沙箱里默认 Steam 安装目录不存在
    assert locate_idleon_install() is None


# --------------------------------------------------------------------------- #
# 进程检测（mock subprocess）
# --------------------------------------------------------------------------- #
def test_is_game_running_true(monkeypatch):
    class _Result:
        stdout = "INFO\tLegendsOfIdleon.exe"

    monkeypatch.setattr(
        "idleon_saver.editor.subprocess.run", lambda *a, **k: _Result()
    )
    assert editor.is_game_running() is True


def test_is_game_running_false(monkeypatch):
    class _Result:
        stdout = ""

    monkeypatch.setattr(
        "idleon_saver.editor.subprocess.run", lambda *a, **k: _Result()
    )
    assert editor.is_game_running() is False


# --------------------------------------------------------------------------- #
# 假 LevelDB（隔离 plyvel）
# --------------------------------------------------------------------------- #
class _FakeDB:
    def __init__(self, store: dict):
        self._store = store

    def get(self, key):
        return self._store.get(key)

    def put(self, key, value):
        self._store[key] = value

    def iterator(self):
        return iter(self._store.items())

    def close(self):
        pass


@pytest.fixture
def fake_leveldb(monkeypatch):
    """构造一个假的 leveldb：key 以 mySave 后缀，值带 0x01 前缀。"""
    stencyl = _stencyl_text()
    store = {b"_file://x/index.html:mySave": b"\x01" + stencyl.encode("ascii")}

    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        yield _FakeDB(store)

    monkeypatch.setattr(editor, "get_db", _fake_get_db)
    return store, stencyl


# --------------------------------------------------------------------------- #
# 加载 - 编码 往返
# --------------------------------------------------------------------------- #
def test_load_wrapped_json_scan(fake_leveldb, tmp_path):
    _store, stencyl = fake_leveldb
    wrapped = editor.load_wrapped_json(tmp_path)  # idleon=None → 扫描后缀
    assert isinstance(wrapped, dict)
    assert "start" in wrapped and "contents" in wrapped
    # 往返：重新编码应等于原始 stencyl
    encoded = editor.encode_to_stencyl(wrapped)
    assert chunk(stencyl, 50) == chunk(encoded, 50)


def test_load_with_idleon(monkeypatch, tmp_path):
    stencyl = _stencyl_text()
    key = b"fixed_key"
    store = {key: b"\x01" + stencyl.encode("ascii")}

    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        yield _FakeDB(store)

    monkeypatch.setattr(editor, "get_db", _fake_get_db)
    monkeypatch.setattr(editor, "db_key", lambda idleon: key)

    wrapped = editor.load_wrapped_json(tmp_path, idleon=Path("/games/idleon"))
    assert isinstance(wrapped, dict)
    assert "contents" in wrapped


def test_write_leveldb_prefix(fake_leveldb, tmp_path):
    store, _stencyl = fake_leveldb
    wrapped = editor.load_wrapped_json(tmp_path)
    encoded = editor.encode_to_stencyl(wrapped)
    editor.write_leveldb(tmp_path, encoded)
    key = b"_file://x/index.html:mySave"
    # 写回应带 0x01 前缀
    assert store[key] == b"\x01" + encoded.encode("ascii")


# --------------------------------------------------------------------------- #
# 校验 wrapped JSON
# --------------------------------------------------------------------------- #
def test_validate_ok(fake_leveldb, tmp_path):
    wrapped = editor.load_wrapped_json(tmp_path)
    ok, msg = editor.validate_wrapped_json(wrapped)
    assert ok is True and msg == ""


def test_validate_missing_start(fake_leveldb, tmp_path):
    ok, msg = editor.validate_wrapped_json({"contents": {}})
    assert ok is False
    assert "start" in msg


def test_validate_missing_contents(fake_leveldb, tmp_path):
    ok, msg = editor.validate_wrapped_json({"start": "o"})
    assert ok is False
    assert "contents" in msg


def test_validate_bad_structure(fake_leveldb, tmp_path):
    # 容器标记 a 但 contents 不是 list/dict → 编码失败
    ok, msg = editor.validate_wrapped_json({"start": "a", "contents": 5})
    assert ok is False


def test_overlay_coerces_types_for_encode():
    """回归：overlay 必须按目标叶子类型标签转换用户值，否则 StencylEncoder
    会对 int/float/bool 调 quote() 抛 'quote_from_bytes() expected bytes'，
    或对 float 塞进 i 叶子生成 i3.14 这类解码器无法解析的坏结构。

    这正是“导入后保存失败 / 结构校验失败，无法编码为 Stencyl”的根因：
    游戏的存档大量字段以字符串/整数形式存储（y3:467 / i135），但 unwrapped
    JSON 导出丢掉了类型信息——字符串字段变 JSON 数字、空槽变 "Null" 字符串。
    导入保存时若不转换，编码即崩。
    """
    from idleon_saver.stencyl.decoder import StencylDecoder

    template = {
        "start": "o",
        "end": "g",
        "contents": {
            "name": {"start": "y", "contents": "Alice"},   # 字符串叶子
            "atk": {"start": "i", "contents": 10},         # 整数叶子
            "hp": {"start": "d", "contents": "3.5"},       # 浮点叶子
            "slot": {"start": "i", "contents": 135},       # 整数叶子（真实值）
        },
    }
    # 用户编辑的 unwrapped：类型与模板不一致（正是真实导出 JSON 的形态）
    unwrapped = {
        "name": 467,       # int → 应转字符串 "467"（y 叶子）
        "atk": 20,         # int 一致
        "hp": 2,           # int → 应转 float 2.0（d 叶子）
        "slot": "Null",    # "Null" → 应转 int 0（i 叶子，空槽）
    }
    result = editor.overlay_unwrapped(template, unwrapped)
    # 不应抛错
    enc = editor.encode_to_stencyl(result)
    # 关键值检查
    assert result["contents"]["name"]["contents"] == "467"
    assert result["contents"]["hp"]["contents"] == 2.0
    assert result["contents"]["slot"]["contents"] == 0
    # 编码结果能回解码（证明结构合法）
    StencylDecoder(enc).result
