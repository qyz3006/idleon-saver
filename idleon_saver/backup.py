"""存档备份工具（无状态函数）。

负责把 leveldb 存档目录整体复制为带时间戳的备份、列出历史备份、
还原备份，以及在文件管理器中定位目录。与编解码核心完全解耦。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 备份目录命名前缀与时间戳格式（设计 §7 约定）
BACKUP_PREFIX = "leveldb_"
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"


def _timestamp() -> str:
    """生成当前时间戳字符串，格式 leveldb_ 命名使用。"""
    return datetime.now().strftime(TIMESTAMP_FMT)


def backup_leveldb(ldb: Path, root: Path) -> Path:
    """把 leveldb 存档目录整体复制为带时间戳的备份。

    Args:
        ldb: 当前存档目录（leveldb）。
        root: 备份根目录（不存在则创建）。

    Returns:
        新备份目录的 Path。

    Raises:
        IOError: 复制失败（如源目录不存在）。
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{BACKUP_PREFIX}{_timestamp()}"
        # 避免同一秒内重复导致命名冲突：追加 pid 区分
        while dest.exists():
            dest = root / f"{BACKUP_PREFIX}{_timestamp()}_{os.getpid()}"
        shutil.copytree(ldb, dest)
        logger.info(f"已创建备份：{dest}")
        return dest
    except OSError as exc:
        raise IOError(f"备份失败：{exc}") from exc


def list_backups(root: Path) -> list[Path]:
    """按名称（即时间戳）倒序列出 root 下的历史备份目录。

    备份目录命名为 ``leveldb_{YYYYMMDD_HHMMSS}``（同一秒冲突时追加
    ``_{pid}``），零填充时间戳字典序即时间序，因此按名称倒序等价于
    按创建时间倒序，且不依赖文件系统 mtime（copytree 默认会保留源 mtime）。

    Args:
        root: 备份根目录。

    Returns:
        备份目录 Path 列表（最新在前）；root 不存在时返回空列表。
    """
    if not root.exists():
        return []
    backups = [
        p
        for p in root.iterdir()
        if p.is_dir() and p.name.startswith(BACKUP_PREFIX)
    ]
    backups.sort(key=lambda p: p.name, reverse=True)
    return backups


def restore_backup(src: Path, ldb: Path, root: Path) -> None:
    """把一份备份还原回当前 leveldb 目录。

    还原前会先对当前态再做一次备份，确保可回退（设计 §7 约定）。

    Args:
        src: 待还原的备份目录。
        ldb: 当前存档目录（leveldb），将被覆盖。
        root: 备份根目录（用于还原前再备份）。

    Raises:
        IOError: 备份不存在或还原失败。
    """
    if not (src.exists() and src.is_dir()):
        raise IOError(f"备份不存在或不是目录：{src}")
    # 还原前先备份当前态，避免改坏后无法挽回
    backup_leveldb(ldb, root)
    # 清空并整体覆盖回 ldb
    if ldb.exists():
        shutil.rmtree(ldb)
    shutil.copytree(src, ldb)
    logger.info(f"已从备份还原：{src} -> {ldb}")


def open_in_explorer(p: Path) -> None:
    """在文件管理器中定位目录。

    Windows 使用 ``os.startfile(p, "explore")``；
    macOS 使用 ``open``；其它使用 ``xdg-open``。

    Raises:
        IOError: 目标路径不存在。
    """
    target = Path(p)
    if not target.exists():
        raise IOError(f"路径不存在：{target}")
    if os.name == "nt":
        # skipcq: BAN-B606
        os.startfile(str(target), "explore")
    elif sys.platform == "darwin":
        # skipcq: BAN-B606
        os.system(f'open "{target}"')
    else:
        # skipcq: BAN-B606
        os.system(f'xdg-open "{target}"')
