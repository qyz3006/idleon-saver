"""存档编辑器纯逻辑层（不依赖 kivy）。

负责加载 / 校验 / 编码 / 回写 wrapped JSON 存档，并检测游戏进程是否运行。
所有 LevelDB 与编解码交互都收敛在此模块，UI 层（gui/editor.py）只做编排。

复用约定：
- 读取：``get_db(ldb).get(key)`` → ``bytes.strip(b"\\x01")`` → ``StencylDecoder(text).result.wrapped``
- 回写：``StencylEncoder(data).result`` → ``db.put(key, b"\\x01" + stencyl.encode("ascii"))``
- 编辑器只编辑 wrapped JSON（含 start/contents/end），与 decode/encode 链路一致。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

# plyvel 仅在目标环境（Python 3.9 + kivy + plyvel）存在。
# 为避免在无 plyvel 的本机测试环境中连模块都无法导入，这里做容错导入；
# 运行期在目标环境这些符号即为真实的 get_db / db_key。
try:
    from idleon_saver.ldb import db_key, get_db
except ImportError:  # pragma: no cover - 仅在无 plyvel 的测试环境触发
    db_key = None  # type: ignore[assignment]
    get_db = None  # type: ignore[assignment]

from idleon_saver.stencyl.decoder import StencylDecoder
from idleon_saver.stencyl.encoder import StencylEncoder

logger = logging.getLogger(__name__)

# 存档 key 后缀：扫描方式定位存档时使用
MY_SAVE_SUFFIX = b"index.html:mySave"
# 游戏进程名（Windows 可执行名）
GAME_PROCESS = "LegendsOfIdleon.exe"


def _resolve_key(db, idleon: Optional[Path]) -> bytes:
    """根据安装目录构造存档 key；未提供时扫描 mySave 后缀 key，保证往返一致。

    Args:
        db: 已打开的 LevelDB 连接（支持 .get / .iterator）。
        idleon: 可选的游戏安装目录；为 None 时自动扫描。

    Returns:
        存档 key（bytes）；若都找不到则抛 KeyError。
    """
    if idleon is not None:
        key = db_key(idleon)  # type: ignore[operator]
        if db.get(key) is None:
            raise KeyError(f"存档 key 不存在于数据库：{key!s}")
        return key
    for key, _ in db.iterator():
        if key.endswith(MY_SAVE_SUFFIX):
            return key
    raise KeyError(f"未找到后缀为 {MY_SAVE_SUFFIX!r} 的存档 key")


def load_wrapped_json(ldb: Path, idleon: Optional[Path] = None) -> dict:
    """读取 leveldb 存档 → 解码为 wrapped JSON（含 start/contents/end）。

    Args:
        ldb: 存档目录（leveldb）。
        idleon: 可选的游戏安装目录，用于构造 db key；为 None 时自动扫描。

    Returns:
        wrapped dict；若数据库无对应存档则抛 KeyError。
    """
    with get_db(ldb) as db:  # type: ignore[operator]
        key = _resolve_key(db, idleon)
        raw = db.get(key)
        if raw is None:
            raise KeyError(f"存档 key 无数据：{key!s}")
        # 去除 0x01 前缀后再送解码器
        text = str(raw.strip(b"\x01"), encoding="utf-8")
        return StencylDecoder(text).result.wrapped


def validate_wrapped_json(data) -> Tuple[bool, str]:
    """校验 wrapped JSON 是否可无损编码回 Stencyl。

    先做结构预检（必须是 dict，且含 start/contents），
    再以 ``StencylEncoder`` 试编码作为权威判据
    （容器节点缺 end、类型标签不匹配等都会抛异常 → 视为非法）。

    Returns:
        ``(ok, 错误描述)``；ok 为 True 时描述为 ""。
    """
    if not isinstance(data, dict):
        return False, "顶层必须是 JSON 对象（wrapped 结构）。"
    if "start" not in data:
        return False, "缺少必需字段：start。"
    if "contents" not in data:
        return False, "缺少必需字段：contents。"
    try:
        StencylEncoder(data).result
    except Exception as exc:  # 编解码对结构极严格，任何异常都视为非法
        return False, f"结构校验失败，无法编码为 Stencyl：{exc}"
    return True, ""


def encode_to_stencyl(data: dict) -> str:
    """把 wrapped JSON 编码为 Stencyl 字符串（不含 0x01 前缀）。"""
    return StencylEncoder(data).result


def write_leveldb(ldb: Path, stencyl: str, idleon: Optional[Path] = None) -> None:
    """把 Stencyl 字符串带 0x01 前缀写回 leveldb 存档。

    Args:
        ldb: 存档目录（leveldb）。
        stencyl: 由 ``encode_to_stencyl`` 得到的 Stencyl 字符串。
        idleon: 可选的安装目录，用于定位 key。
    """
    encoded = stencyl.encode("ascii")
    with get_db(ldb) as db:  # type: ignore[operator]
        key = _resolve_key(db, idleon)
        try:
            db.put(key, b"\x01" + encoded)
        except Exception as exc:
            raise IOError(f"写回数据库失败：{exc}") from exc
    logger.info(f"已写回存档 key={key!s} 到 {ldb}")


def is_game_running() -> bool:
    """检测 LegendsOfIdleon.exe 是否正在运行（零新依赖，使用标准库 subprocess）。

    通过 ``tasklist``（win32）/ ``pgrep``（posix）匹配进程名；
    命中即视为运行中。检测失败（命令缺失等）时保守返回 False。

    Returns:
        游戏进程正在运行返回 True，否则 False。
    """
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["tasklist", "/fi", f"imagename eq {GAME_PROCESS}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return GAME_PROCESS.lower() in result.stdout.lower()
        # posix（linux / mac）
        result = subprocess.run(
            ["pgrep", "-f", "LegendsOfIdleon"],
            capture_output=True,
            text=True,
            check=False,
        )
        return bool(result.stdout.strip())
    except (FileNotFoundError, OSError) as exc:
        logger.warning(f"进程检测失败（视为未运行）：{exc}")
        return False
