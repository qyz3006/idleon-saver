"""存档编辑器纯逻辑层（不依赖 kivy）。

负责加载 / 校验 / 编码 / 回写 wrapped JSON 存档，并检测游戏进程是否运行。
所有 LevelDB 与编解码交互都收敛在此模块，UI 层（gui/editor.py）只做编排。

复用约定：
- 读取：``get_db(ldb).get(key)`` → ``bytes.strip(b"\\x01")`` → ``StencylDecoder(text).result.wrapped``
- 回写：``StencylEncoder(data).result`` → ``db.put(key, b"\\x01" + stencyl.encode("ascii"))``
- 编辑器只编辑 wrapped JSON（含 start/contents/end），与 decode/encode 链路一致。

LevelDB 读取健壮性（应对游戏版本更新导致的格式不兼容）：
- 主路径读失败（plyvel 抛 CorruptionError）时，自动把存档目录复制到临时目录、
  调用 ``plyvel.repair_db`` 重建 MANIFEST 后再从副本读取——**绝不触碰原存档**。
- 无论修复成功与否，错误信息都包含 plyvel 的**原始**错误文本，便于定位是
  "block checksum mismatch"（真损坏）还是 "bad version edit"（格式不兼容）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
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

# CorruptionError 仅在目标环境（装了 plyvel）可用。这里做容错导入，
# 使无 plyvel 的测试环境也能导入本模块；运行期即真实的 plyvel.CorruptionError。
try:
    from plyvel import CorruptionError  # type: ignore
except ImportError:  # pragma: no cover - 仅无 plyvel 的测试环境
    CorruptionError = type("CorruptionError", (Exception,), {})  # type: ignore

from idleon_saver.stencyl.decoder import StencylDecoder
from idleon_saver.stencyl.encoder import StencylEncoder
# 纯 Python LevelDB 读取器：当 plyvel 因版本不兼容读不了 DB 时的首选回退。
# 直接解析 .ldb/.log 原始文件（含 Snappy 解压 + WAL 重组），不依赖 plyvel。
from idleon_saver.pure_ldb import read_value_by_key_suffix as _pure_read_value

logger = logging.getLogger(__name__)

# 存档 key 后缀：扫描方式定位存档时使用
MY_SAVE_SUFFIX = b"index.html:mySave"
# 游戏进程名（Windows 可执行名）
GAME_PROCESS = "LegendsOfIdleon.exe"


class SaveCorruptedError(Exception):
    """存档 LevelDB 读取失败（损坏块或格式不兼容）。

    错误信息始终包含 plyvel 的原始错误文本，便于区分真损坏与版本不兼容。
    """


# 复制 DB 副本时跳过 LOCK 文件：游戏运行中 LOCK 被占用无法复制，且 repair
# 会重建它，复制它毫无意义。
_REPAIR_COPY_IGNORE = shutil.ignore_patterns("LOCK")


def _find_save_key(db, idleon: Optional[Path]) -> Tuple[Optional[bytes], Optional[Exception]]:
    """定位存档 key。返回 (key, underlying_error)，不抛 SaveCorruptedError。

    Args:
        db: 已打开的 LevelDB 连接（支持 .get / .iterator）。
        idleon: 可选的游戏安装目录；为 None 时自动扫描。

    Returns:
        ``(key, None)`` 成功；``(None, exc)`` 读取失败（exc 为 plyvel 原始错误，
        通常是 CorruptionError）；``(None, KeyError(...))`` 扫描完未命中后缀。
    """
    if idleon is not None and db_key is not None:
        try:
            key = db_key(idleon)
        except Exception as exc:  # db_key 构造失败（路径异常等）
            logger.debug("db_key 构造失败，回退扫描：%s", exc)
            key = None
        if key is not None:
            try:
                raw = db.get(key)
            except CorruptionError as exc:
                return None, exc
            except Exception as exc:
                logger.debug("db.get(key) 非 Corruption 异常，回退扫描：%s", exc)
            else:
                if raw is not None:
                    return key, None
    # 扫描分支：遍历所有 key，命中 mySave 后缀即返回。
    try:
        for key, _ in db.iterator():
            if key.endswith(MY_SAVE_SUFFIX):
                return key, None
    except CorruptionError as exc:
        return None, exc
    return None, KeyError(f"未找到后缀为 {MY_SAVE_SUFFIX!r} 的存档 key")


def _decode_raw(raw: bytes) -> dict:
    """去 0x01 前缀 → UTF-8 → Stencyl 解码为 wrapped dict。"""
    text = str(raw.strip(b"\x01"), encoding="utf-8")
    return StencylDecoder(text).result.wrapped


def _try_repair_and_read(
    ldb: Path, idleon: Optional[Path]
) -> Tuple[Optional[dict], Optional[Exception]]:
    """非破坏性修复回退：复制 DB 到临时目录 → repair_db → 读取。

    原存档目录绝不被修改。repair_db 会重建 MANIFEST 并丢弃无法解析的块，
    对"MANIFEST 损坏 / 格式版本不兼容"类问题 often 能恢复出 mySave 数据。

    Returns:
        ``(wrapped_dict, None)`` 修复后读取成功；``(None, exc)`` 失败（exc 为
        原始错误，便于上层在最终报错里展示）。
    """
    try:
        import plyvel as _plyvel  # 局部导入：无 plyvel 的测试环境直接跳过
    except ImportError:
        return None, RuntimeError("plyvel 未安装，无法执行修复")

    tmp_root = Path(tempfile.mkdtemp(prefix="idleon_repair_"))
    try:
        copy_dir = tmp_root / "ldb"
        # 跳过 LOCK：游戏运行时它被占用，且 repair 会重建。
        shutil.copytree(ldb, copy_dir, ignore=_REPAIR_COPY_IGNORE)
        try:
            _plyvel.repair_db(str(copy_dir))
        except Exception as exc:
            logger.warning("repair_db 失败：%s", exc)
            return None, exc
        with get_db(copy_dir) as db:  # type: ignore[operator]
            key, kerr = _find_save_key(db, idleon)
            if key is None:
                return None, kerr or KeyError("修复后仍未找到存档 key")
            try:
                raw = db.get(key)
            except CorruptionError as exc:
                return None, exc
            if raw is None:
                return None, KeyError("修复后存档 key 无数据")
            return _decode_raw(raw), None
    except Exception as exc:
        return None, exc
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def load_wrapped_json(ldb: Path, idleon: Optional[Path] = None) -> dict:
    """读取 leveldb 存档 → 解码为 wrapped JSON（含 start/contents/end）。

    读取健壮性（三层回退，原存档绝不被修改）：
    1. plyvel 主路径：标准 LevelDB 读取（兼容老版本 DB）。
    2. 纯 Python 读取器：直接解析 .ldb/.log 原始文件（Snappy + WAL 重组），
       不依赖 plyvel，解决新版 Chromium/Electron 写出的 DB plyvel 读不了的问题。
    3. plyvel repair 副本：复制 DB → repair_db 重建 MANIFEST → 读副本。

    Args:
        ldb: 存档目录（leveldb）。
        idleon: 可选的游戏安装目录，用于构造 db key；为 None 时自动扫描。

    Returns:
        wrapped dict。

    Raises:
        SaveCorruptedError: 全部回退失败（含各层原始错误文本）。
        KeyError: 存档 key 不存在。
    """
    # --- 第 1 层：plyvel 主路径 ---
    primary_error: Optional[Exception] = None
    if get_db is None:
        # plyvel 未安装（测试环境 / 冻结 exe 缺 plyvel）→ 直接跳到纯 Python 读取器
        primary_error = ImportError("plyvel 未安装")
    else:
        try:
            with get_db(ldb) as db:  # type: ignore[operator]
                key, kerr = _find_save_key(db, idleon)
                if key is None:
                    primary_error = kerr or KeyError("未找到存档 key")
                else:
                    try:
                        raw = db.get(key)
                    except CorruptionError as exc:
                        primary_error = exc
                    else:
                        if raw is None:
                            primary_error = KeyError(f"存档 key 无数据：{key!s}")
                        else:
                            return _decode_raw(raw)
        except CorruptionError as exc:
            primary_error = exc

    # --- 第 2 层：纯 Python 读取器（不依赖 plyvel） ---
    # 首选回退：直接解析 .ldb/.log 原始文件。对新版 Chromium LevelDB 格式
    # （plyvel 因 MANIFEST/格式版本不兼容读不了的情况）往往一步到位。
    logger.info("plyvel 主路径失败 (%s)，尝试纯 Python 读取器", primary_error)
    try:
        raw = _pure_read_value(ldb, MY_SAVE_SUFFIX)
        if raw is not None:
            logger.info("纯 Python 读取器成功取到 mySave 值 (%d 字节)", len(raw))
            return _decode_raw(raw)
        logger.info("纯 Python 读取器未找到 mySave key")
    except Exception as exc:
        logger.warning("纯 Python 读取器失败：%s", exc)
        pure_error = exc
    else:
        pure_error = KeyError("纯 Python 读取器未找到 mySave key")

    # --- 第 3 层：plyvel repair 副本（仅 CorruptionError 时有意义） ---
    if isinstance(primary_error, CorruptionError):
        logger.warning(
            "LevelDB 主路径读取失败 (%s: %s)；尝试修复副本回退",
            type(primary_error).__name__,
            primary_error,
        )
        repaired, rerr = _try_repair_and_read(ldb, idleon)
        if repaired is not None:
            logger.warning("修复副本回退成功，已从副本读取存档（原存档未修改）")
            return repaired
    else:
        rerr = None

    # --- 全部失败：报错带上各层原始错误，便于定位 ---
    raise SaveCorruptedError(
        f"存档读取失败（三层回退均未成功）。\n"
        f"plyvel 主路径：{type(primary_error).__name__}: {primary_error}\n"
        f"纯 Python 读取器：{type(pure_error).__name__}: {pure_error}\n"
        f"repair 副本：{rerr}\n\n"
        f"可能原因：游戏版本更新导致 LevelDB 格式不兼容。"
        f"可尝试通过『备份管理』还原最近备份，或把此完整错误上报。"
    ) from primary_error

    # 非 Corruption 错误（KeyError / IOError 等）直接抛
    if primary_error is None:
        primary_error = KeyError("未知读取失败")
    raise primary_error


# --------------------------------------------------------------------------- #
# Unwrapped ↔ Wrapped 转换（编辑器用 unwrapped 显示，保存时叠回 wrapped 保类型）
# --------------------------------------------------------------------------- #
# 背景：wrapped JSON（含 start/contents/end 类型标签）体积是 unwrapped 的 ~5.6
# 倍（本存档 31.7MB vs 5.7MB）。直接把 31.7MB 塞进 Kivy TextInput 会卡死。
# 改为：编辑器显示 unwrapped（紧凑、人类可读），保存时把用户编辑叠回原 wrapped
# 结构（保留 start/end 类型标签），保证 StencylEncoder 能正确编码。


def wrapped_to_unwrapped(node) -> "Any":
    """从 wrapped 节点递归计算 unwrapped 值（人类可读的纯数据树）。

    叶子返回 ``contents`` 原值；dict 容器返回 ``{key: unwrapped_child}``；
    list 容器返回 ``[unwrapped_child, ...]``。
    """
    if not isinstance(node, dict) or "start" not in node:
        return node
    contents = node.get("contents")
    if isinstance(contents, dict):
        return {str(k): wrapped_to_unwrapped(v) for k, v in contents.items()}
    if isinstance(contents, list):
        return [wrapped_to_unwrapped(v) for v in contents]
    return contents


def overlay_unwrapped(wrapped_node, unwrapped_val):
    """把用户编辑的 unwrapped 值叠回 wrapped 结构，保留 start/end 类型标签。

    递归遍历 wrapped 树：对叶子节点用 unwrapped_val 替换 contents；对容器节点
    按键/索引递归。未在 unwrapped 中出现的键保留原值（用户未改动）。

    JSON 往返会把 int 键变成 str，因此按键的 ``str()`` 匹配。
    """
    if not isinstance(wrapped_node, dict) or "start" not in wrapped_node:
        return wrapped_node
    result = dict(wrapped_node)  # 浅拷贝，保留 start/end
    w_contents = wrapped_node.get("contents")
    if isinstance(w_contents, dict):
        new_contents = {}
        for k, child in w_contents.items():
            # JSON 往返后键为 str；用 str(k) 匹配
            match_key = str(k) if not isinstance(k, str) else k
            if isinstance(unwrapped_val, dict) and match_key in unwrapped_val:
                new_contents[k] = overlay_unwrapped(child, unwrapped_val[match_key])
            elif isinstance(unwrapped_val, dict) and k in unwrapped_val:
                new_contents[k] = overlay_unwrapped(child, unwrapped_val[k])
            else:
                new_contents[k] = child  # 保留原值
        result["contents"] = new_contents
    elif isinstance(w_contents, list):
        new_list = []
        for i, child in enumerate(w_contents):
            if isinstance(unwrapped_val, list) and i < len(unwrapped_val):
                new_list.append(overlay_unwrapped(child, unwrapped_val[i]))
            else:
                new_list.append(child)
        result["contents"] = new_list
    else:
        # 叶子：用用户编辑的值替换 contents（start/end 类型标签保留）
        result["contents"] = unwrapped_val
    return result


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
        key, kerr = _find_save_key(db, idleon)
        if key is None:
            # 写回时找不到 key 是致命的：不能凭空造一个 key 写入
            raise kerr or KeyError("未找到存档 key，无法写回")
        try:
            db.put(key, b"\x01" + encoded)
        except Exception as exc:
            raise IOError(f"写回数据库失败：{exc}") from exc
    logger.info(f"已写回存档 key={key!s} 到 {ldb}")


def is_game_running() -> bool:
    """检测 LegendsOfIdleon.exe 是否正在运行（零新依赖，使用标准库 subprocess）。

    通过 ``tasklist``（win32）/ ``pgrep``（posix）匹配进程名；
    命中即视为运行中。检测失败（命令缺失、冻结态句柄无效等）时保守返回 False，
    不向用户报警、也不写警告日志（冻结 exe 下 subprocess 触及无效句柄属正常噪声）。

    Returns:
        游戏进程正在运行返回 True，否则 False。
    """
    # CREATE_NO_WINDOW 在非 Windows 上用 getattr 取 0，保持兼容。
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["tasklist", "/fi", f"imagename eq {GAME_PROCESS}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                creationflags=flags,
            )
            return GAME_PROCESS.lower() in result.stdout.lower()
        # posix（linux / mac）
        result = subprocess.run(
            ["pgrep", "-f", "LegendsOfIdleon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            creationflags=flags,
        )
        return bool(result.stdout.strip())
    except (OSError, ValueError):
        # 冻结 exe 下 kivy 可能重定向了 std 句柄，subprocess 触到无效句柄
        #（如 [WinError 6] 句柄无效）属可预期噪声；静默视为未运行即可。
        return False
