"""QA 独立验收验证（严过关）。

不依赖 plyvel / kivy：通过 monkeypatch 隔离 LevelDB，仅用 Stencyl 编解码
纯逻辑层 + 标准库做往返、0x01 前缀、validate_wrapped_json、backup 行为验证。
本文件为 QA 独立验证用，不修改工程师源码。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from idleon_saver import backup, editor
from idleon_saver.stencyl.decoder import StencylDecoder
from idleon_saver.utility import ROOT_DIR, chunk


def _stencyl_text() -> str:
    return (ROOT_DIR / "tests" / "data" / "stencylsave.txt").read_text()


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
    """假 LevelDB：key 以 mySave 后缀，值带 0x01 前缀。"""
    stencyl = _stencyl_text()
    store = {b"_file://x/index.html:mySave": b"\x01" + stencyl.encode("ascii")}

    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        yield _FakeDB(store)

    monkeypatch.setattr(editor, "get_db", _fake_get_db)
    return store, stencyl


# --------------------------------------------------------------------------- #
# 1) 解码 -> 编码 -> 再解码，两次 wrapped 相等（无损）
# --------------------------------------------------------------------------- #
def test_roundtrip_wrapped_equal(fake_leveldb, tmp_path):
    _store, stencyl = fake_leveldb
    wrapped1 = editor.load_wrapped_json(tmp_path)  # idleon=None → 扫描后缀
    assert isinstance(wrapped1, dict)
    encoded = editor.encode_to_stencyl(wrapped1)
    wrapped2 = StencylDecoder(encoded).result.wrapped
    # 再次解码的 wrapped 必须与原 wrapped 完全一致（无损往返）
    assert wrapped1 == wrapped2


def test_roundtrip_byte_equal(fake_leveldb, tmp_path):
    """工程师断言：重新编码的字节串应与原 stencyl 完全一致。"""
    _store, stencyl = fake_leveldb
    wrapped = editor.load_wrapped_json(tmp_path)
    encoded = editor.encode_to_stencyl(wrapped)
    assert chunk(stencyl, 50) == chunk(encoded, 50)


# --------------------------------------------------------------------------- #
# 2) write_leveldb 写入字节确以 b"\x01" 开头（mock get_db / db_key 注入）
# --------------------------------------------------------------------------- #
def test_write_leveldb_prefix_injection(tmp_path, monkeypatch):
    stencyl = _stencyl_text()
    key = b"fake_save_key"
    store = {key: b"\x01" + b"old"}  # 预置 key 使 db.get 非空
    captured: dict = {}

    @contextmanager
    def _fake_get_db(path, create_if_missing=False):
        db = _FakeDB(store)
        orig_put = db.put

        def _put(k, v):
            captured[k] = v
            orig_put(k, v)

        db.put = _put
        yield db

    monkeypatch.setattr(editor, "get_db", _fake_get_db)
    monkeypatch.setattr(editor, "db_key", lambda idleon: key)

    editor.write_leveldb(tmp_path, stencyl, idleon=Path("/games/idleon"))

    assert key in captured, "write_leveldb 未对该 key 执行 put"
    assert captured[key].startswith(b"\x01"), "写回字节缺失 0x01 前缀！"
    assert captured[key] == b"\x01" + stencyl.encode("ascii")


# --------------------------------------------------------------------------- #
# 3) validate_wrapped_json：缺 start / 缺 contents / 结构非法
# --------------------------------------------------------------------------- #
def test_validate_non_dict():
    ok, msg = editor.validate_wrapped_json("not a dict")
    assert ok is False and "JSON 对象" in msg


def test_validate_missing_start():
    ok, msg = editor.validate_wrapped_json({"contents": {}})
    assert ok is False and "start" in msg


def test_validate_missing_contents():
    ok, msg = editor.validate_wrapped_json({"start": "o"})
    assert ok is False and "contents" in msg


def test_validate_container_missing_end():
    # 容器标记 o 但缺 end → 编码时访问 x["end"] 抛 KeyError → 判非法
    ok, msg = editor.validate_wrapped_json({"start": "o", "contents": {}})
    assert ok is False and msg


def test_validate_literal_bad_contents():
    # 字面量 y 但 contents 不是字符串 → 编码失败
    ok, msg = editor.validate_wrapped_json({"start": "y", "contents": 5})
    assert ok is False and msg


def test_validate_container_ok():
    # 结构完整（含 end）的容器应通过
    ok, msg = editor.validate_wrapped_json(
        {"start": "o", "contents": {"a": {"start": "i", "contents": 1}}, "end": "g"}
    )
    assert ok is True and msg == ""


# --------------------------------------------------------------------------- #
# 4) backup_leveldb 生成目录 / list_backups 倒序 / restore_backup 还原前再备份
# --------------------------------------------------------------------------- #
def _make_fake_ldb(root: Path) -> Path:
    ldb = root / "leveldb"
    ldb.mkdir()
    (ldb / "CURRENT").write_text("v1")
    (ldb / "000003.log").write_text("log1")
    return ldb


def test_backup_creates_timestamped_dir(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    dest = backup.backup_leveldb(ldb, root)
    assert dest.exists() and dest.is_dir()
    assert dest.name.startswith("leveldb_")
    assert (dest / "CURRENT").read_text() == "v1"
    assert (dest / "000003.log").read_text() == "log1"


def test_list_backups_reverse(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    b1 = backup.backup_leveldb(ldb, root)
    (ldb / "CURRENT").write_text("v2")
    time.sleep(1.1)
    b2 = backup.backup_leveldb(ldb, root)
    backups = backup.list_backups(root)
    assert len(backups) == 2
    assert backups[0] == b2  # 最新在前
    assert backups[-1] == b1  # 最旧在后


def test_restore_re_backups_current(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    orig = backup.backup_leveldb(ldb, root)
    # 改动当前存档
    (ldb / "CURRENT").write_text("dirty")
    backup.restore_backup(orig, ldb, root)
    # 已还原回初始内容
    assert (ldb / "CURRENT").read_text() == "v1"
    # 还原前应先备份了 dirty 当前态（存在一份不同于 orig 的备份）
    names = [p.name for p in backup.list_backups(root)]
    assert any(n != orig.name for n in names)


def test_restore_missing_src_raises(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    with pytest.raises(IOError):
        backup.restore_backup(tmp_path / "no_such_backup", ldb, root)


def test_open_in_explorer_missing_raises(tmp_path):
    with pytest.raises(IOError):
        backup.open_in_explorer(tmp_path / "nope")
