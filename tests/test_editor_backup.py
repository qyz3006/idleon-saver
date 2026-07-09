"""备份工具单测（tests/test_editor_backup.py）。

覆盖：backup_leveldb 生成带时间戳备份、list_backups 倒序、restore_backup
还原前再备份、open_in_explorer 对缺失路径报错。纯标准库，本机可直接运行。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from idleon_saver import backup


def _make_fake_ldb(root: Path) -> Path:
    """在 root 下造一个假的 leveldb 目录（含若干文件）。"""
    ldb = root / "leveldb"
    ldb.mkdir()
    (ldb / "000003.log").write_text("fake")
    (ldb / "CURRENT").write_text("fake")
    return ldb


def test_backup_leveldb(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    dest = backup.backup_leveldb(ldb, root)
    assert dest.exists() and dest.is_dir()
    assert dest.name.startswith("leveldb_")
    # 目录整体复制
    assert (dest / "000003.log").exists()
    assert (dest / "CURRENT").exists()


def test_list_backups(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    b1 = backup.backup_leveldb(ldb, root)
    # 改动当前存档后再备份；sleep 确保两次备份时间戳不在同一秒，
    # 避免 mtime 并列导致 list_backups 的稳定排序结果不确定。
    (ldb / "000003.log").write_text("changed")
    time.sleep(1.1)
    b2 = backup.backup_leveldb(ldb, root)
    backups = backup.list_backups(root)
    assert len(backups) == 2
    # 最新创建的排在最前
    assert backups[0] == b2
    assert backups[1] == b1


def test_restore_backup(tmp_path):
    ldb = _make_fake_ldb(tmp_path)
    root = tmp_path / "backups"
    # 初始备份
    orig = backup.backup_leveldb(ldb, root)
    # 改动当前存档
    (ldb / "000003.log").write_text("dirty")
    # 还原到 orig
    backup.restore_backup(orig, ldb, root)
    assert (ldb / "000003.log").read_text() == "fake"
    # 还原前应先备份当前态（dirty 那一版）
    backups = backup.list_backups(root)
    assert any(b != orig for b in backups)


def test_open_in_explorer_missing(tmp_path):
    with pytest.raises(IOError):
        backup.open_in_explorer(tmp_path / "nope")
