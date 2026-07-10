"""Pure-Python LevelDB reader for Chromium localStorage (no plyvel dependency).

Why this module exists
----------------------
``plyvel`` 1.4.0 (bundled in the frozen exe) ships an older LevelDB C++ library
that cannot open LevelDB databases written by newer Chromium / Electron builds
— it raises ``CorruptionError`` during DB open or iteration. Since Legends of
Idleon stores its save in Chromium's localStorage LevelDB, this breaks save
reading whenever the game's bundled Electron updates its LevelDB format.

This module reads the save **directly from the raw ``.ldb`` / ``.log`` files**
using pure Python, bypassing plyvel entirely. It implements just enough of the
LevelDB on-disk format to extract a single key's value:

- SSTable (``.ldb``) parsing: footer → index block → data blocks, with
  Snappy block decompression (self-contained, no external dep).
- WAL (``.log``) parsing with multi-record reassembly (large writes span
  32 KB records: first + middle* + last).
- Internal key format: ``user_key + 8-byte (seq<<8 | type)`` footer.

It is **read-only** for SSTables but also supports **WAL append** for writing
(``write_value_wal``), so the editor can save even when plyvel can't open
the database.

Scope / limitations
-------------------
This is a targeted reader for ``legends-of-idleon``'s localStorage DB, not a
full LevelDB implementation. It does not parse the MANIFEST (which determines
the "live" file set); instead it scans ``.log`` files first (newest writes) then
``.ldb`` files newest-first by file number, returning the first match. This is
correct for a quiescent DB (game closed, no active compaction) — the common
case for a save editor.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

_LEVELDB_MAGIC = 0xDB4775248B80FB57
_BLOCK_SIZE = 32768  # leveldb log block size


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a base-128 varint. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        b = data[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _snappy_decompress(src: bytes) -> bytes:
    """Self-contained Snappy block-format decompression (no external deps).

    Implements the Google Snappy block format: a varint uncompressed-length
    prefix followed by a sequence of literal/copy elements.
    """
    out = bytearray()
    i = 0
    n = len(src)
    # Skip the uncompressed-length varint header.
    while i < n and (src[i] & 0x80):
        i += 1
    i += 1
    while i < n:
        tag = src[i]
        i += 1
        typ = tag & 0x03
        if typ == 0:  # literal
            ln = (tag >> 2) + 1
            if ln > 60:  # extended length: 1-4 extra bytes
                extra = ln - 60
                ln = 0
                for j in range(extra):
                    ln |= src[i + j] << (8 * j)
                i += extra
                ln += 1
            out += src[i:i + ln]
            i += ln
        elif typ == 1:  # copy, 1-byte offset
            ln = 4 + ((tag >> 2) & 0x07)
            off = ((tag >> 5) & 0x07) * 256 + src[i]
            i += 1
            s = len(out) - off
            for _ in range(ln):
                out.append(out[s])
                s += 1
        elif typ == 2:  # copy, 2-byte offset
            ln = 1 + (tag >> 2)
            off = src[i] + src[i + 1] * 256
            i += 2
            s = len(out) - off
            for _ in range(ln):
                out.append(out[s])
                s += 1
        else:  # typ == 3, copy, 4-byte offset
            ln = 1 + (tag >> 2)
            off = (src[i] + src[i + 1] * 256 + src[i + 2] * 65536
                   + src[i + 3] * 16777216)
            i += 4
            s = len(out) - off
            for _ in range(ln):
                out.append(out[s])
                s += 1
    return bytes(out)


def _parse_block(data: bytes, off: int, size: int) -> List[Tuple[bytes, bytes]]:
    """Parse a leveldb block → list of (internal_key, value).

    Handles Snappy compression (type 1) and no compression (type 0).
    """
    compress_type = data[off + size]
    raw = data[off:off + size]
    if compress_type == 1:
        raw = _snappy_decompress(raw)
    elif compress_type != 0:
        raise ValueError(f"unsupported block compression type: {compress_type}")
    num_restarts = struct.unpack("<I", raw[-4:])[0]
    restarts_start = len(raw) - 4 - num_restarts * 4
    entries: List[Tuple[bytes, bytes]] = []
    p = 0
    prev_key = b""
    while p < restarts_start:
        shared, p = _read_varint(raw, p)
        unshared, p = _read_varint(raw, p)
        vlen, p = _read_varint(raw, p)
        key_delta = raw[p:p + unshared]
        p += unshared
        value = raw[p:p + vlen]
        p += vlen
        key = prev_key[:shared] + key_delta
        entries.append((key, value))
        prev_key = key
    return entries


def _iter_sstable(data: bytes) -> Iterator[Tuple[bytes, bytes]]:
    """Yield (internal_key, value) for every entry in a .ldb SSTable.

    Returns nothing if the file is too small or the footer magic doesn't match.
    """
    if len(data) < 48:
        return
    if struct.unpack("<Q", data[-8:])[0] != _LEVELDB_MAGIC:
        return
    pos = len(data) - 48
    # metaindex handle (unused), then index handle
    _, pos = _read_varint(data, pos)
    _, pos = _read_varint(data, pos)
    idx_off, pos = _read_varint(data, pos)
    idx_size, pos = _read_varint(data, pos)
    for _sep_key, block_handle in _parse_block(data, idx_off, idx_size):
        o, p = _read_varint(block_handle, 0)
        s, p = _read_varint(block_handle, p)
        for internal_key, value in _parse_block(data, o, s):
            yield internal_key, value


def _iter_wal(data: bytes) -> Iterator[Tuple[bytes, bytes]]:
    """Yield (key, value) for every PUT in a .log WAL file.

    Reassembles records that span multiple 32 KB blocks (first/middle/last).
    """
    pos = 0
    buf = bytearray()
    n = len(data)
    while pos < n:
        block_end = pos + _BLOCK_SIZE - (pos % _BLOCK_SIZE)
        if block_end > n:
            block_end = n
        while pos + 7 <= block_end:
            # record header: crc(4) + length(2) + type(1)
            length = struct.unpack("<H", data[pos + 4:pos + 6])[0]
            rtype = data[pos + 6]
            payload = data[pos + 7:pos + 7 + length]
            pos += 7 + length
            if rtype == 1:  # full record
                yield from _parse_write_batch(payload)
            elif rtype == 2:  # first fragment
                buf = bytearray(payload)
            elif rtype == 3:  # middle fragment
                buf += payload
            elif rtype == 4:  # last fragment
                buf += payload
                yield from _parse_write_batch(bytes(buf))
                buf = bytearray()
            # rtype == 0: zero/padding — skip
        pos = block_end  # advance to next block boundary


def _parse_write_batch(payload: bytes) -> Iterator[Tuple[bytes, bytes]]:
    """Parse a WriteBatch payload → yield (key, value) for each PUT."""
    if len(payload) < 12:
        return
    count = struct.unpack("<I", payload[8:12])[0]
    p = 12
    for _ in range(count):
        if p >= len(payload):
            return
        op = payload[p]
        p += 1
        if op == 1:  # put
            klen, p = _read_varint(payload, p)
            key = payload[p:p + klen]
            p += klen
            vlen, p = _read_varint(payload, p)
            value = payload[p:p + vlen]
            p += vlen
            yield key, value
        elif op == 0:  # delete
            klen, p = _read_varint(payload, p)
            p += klen
        # other op codes: ignore


def _internal_key_user_key(internal_key: bytes) -> bytes:
    """Strip the 8-byte (seq<<8|type) footer → user key."""
    return internal_key[:-8]


def _internal_key_is_put(internal_key: bytes) -> bool:
    """Type is the low byte of the 8-byte footer (type=1 → PUT)."""
    return len(internal_key) >= 8 and (internal_key[-8] & 0xFF) == 1


def _file_number(name: str) -> int:
    """Extract the numeric prefix of a leveldb file name (e.g. '000073.ldb' → 73)."""
    try:
        return int(name.split(".")[0])
    except ValueError:
        return -1


def read_value_by_key_suffix(ldb_dir: Path, key_suffix: bytes) -> Optional[bytes]:
    """Read the raw value for the newest key ending with ``key_suffix``.

    Scans WAL (``.log``) files first — they hold the most recent unflushed
    writes — then ``.ldb`` SSTable files newest-first by file number.

    WAL 语义：一个 .log 文件内可能有多条对同一 key 的写入（游戏多次存档），
    **最后一条**才是最新值。因此遍历整个 WAL 取最后一条匹配，而非第一条。
    多个 .log 文件时按文件号降序（最新优先），首个含匹配的 .log 的最后一条
    即为当前值。.ldb 文件按文件号降序，首个匹配即为最新已刷盘值。

    Args:
        ldb_dir: Path to the leveldb directory.
        key_suffix: Byte suffix to match (e.g. ``b"index.html:mySave"``).

    Returns:
        The raw value bytes, or ``None`` if no matching key was found.
    """
    ldb_dir = Path(ldb_dir)
    if not ldb_dir.is_dir():
        return None

    # 1) WAL files — newest (highest file number) first.
    #    Within each .log, iterate ALL records and keep the LAST match
    #    (later writes override earlier ones in LevelDB WAL semantics).
    log_files = sorted(
        (f for f in ldb_dir.iterdir() if f.suffix == ".log"),
        key=lambda f: _file_number(f.name),
        reverse=True,
    )
    for lf in log_files:
        try:
            data = lf.read_bytes()
        except OSError:
            continue
        last_value = None
        for key, value in _iter_wal(data):
            if key.endswith(key_suffix):
                last_value = value  # keep the LAST match (most recent write)
        if last_value is not None:
            return last_value

    # 2) SSTable files, newest (highest file number) first.
    ldb_files = sorted(
        (f for f in ldb_dir.iterdir() if f.suffix == ".ldb"),
        key=lambda f: _file_number(f.name),
        reverse=True,
    )
    for lf in ldb_files:
        try:
            data = lf.read_bytes()
        except OSError:
            continue
        try:
            for internal_key, value in _iter_sstable(data):
                if not _internal_key_is_put(internal_key):
                    continue
                user_key = _internal_key_user_key(internal_key)
                if user_key.endswith(key_suffix):
                    return value
        except (IndexError, ValueError):
            # corrupt/unparseable SSTable — skip to the next file
            continue
    return None


# --------------------------------------------------------------------------- #
# WAL write (pure Python, no plyvel)
# --------------------------------------------------------------------------- #
_CRC32C_POLY = 0x82F63B78
_crc32c_table: Optional[list] = None


def _make_crc32c_table() -> list:
    """Build the CRC32C (Castagnoli) lookup table."""
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ _CRC32C_POLY if (crc & 1) else crc >> 1
        table.append(crc)
    return table


def _crc32c(data: bytes) -> int:
    """Compute CRC32C (Castagnoli) checksum — what LevelDB uses for WAL records."""
    global _crc32c_table
    if _crc32c_table is None:
        _crc32c_table = _make_crc32c_table()
    crc = 0xFFFFFFFF
    for b in data:
        crc = (crc >> 8) ^ _crc32c_table[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFFFFFF


def _varint_encode(n: int) -> bytes:
    """Encode an integer as a base-128 varint (for WriteBatch key/value lengths)."""
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _next_file_number(ldb_dir: Path) -> int:
    """Find the next available file number for a new leveldb file."""
    max_num = 0
    for f in ldb_dir.iterdir():
        num = _file_number(f.name)
        if num > max_num:
            max_num = num
    return max_num + 1


# LevelDB 的 WAL 重放按"序列号最大者获胜"决定同一 key 的最终值，
# 而不是文件顺序或写入先后。已提交到 SSTable 的旧存档序列号通常很大
# （真实游戏存档累计写入远小于 1e9）。若这里用 0，新建记录的序列号会
# 小于已提交数据，导致这笔写入在游戏（真正的 LevelDB）读档时被旧值覆盖、
# 静默丢失——表现就是"转 JSON 改完回编 ldb，进游戏发现改动没生效"。
# 因此用 1<<40 作为基准序列号：远超任何真实存档序列号，保证我们的写入
# 一定获胜；同时远小于 56 位序列号上限 (2^56)，不会溢出 internal key。
WAL_BATCH_SEQUENCE = 1 << 40


def write_value_wal(ldb_dir: Path, key: bytes, value: bytes) -> Path:
    """Append a PUT record for ``key``/``value`` to the leveldb WAL.

    This is the pure-Python write path — no plyvel required. It appends a
    WriteBatch record (containing a single PUT) to the newest ``.log`` file,
    handling 32 KB block fragmentation (first/middle/last records) and
    CRC32C checksums.

    On next DB open, LevelDB replays the WAL and applies the PUT. Since WAL
    replay is last-write-wins, the appended value overrides any previous value
    for the same key.

    Args:
        ldb_dir: Path to the leveldb directory.
        key: The raw user key bytes (WAL stores user keys, not internal keys).
        value: The raw value bytes (e.g. ``b"\\x01" + stencyl.encode("ascii")``).

    Returns:
        The path of the .log file that was written to.
    """
    ldb_dir = Path(ldb_dir)
    # 始终新建一个更高编号、从 0 字节（即 32KB 块边界）起始的 .log 文件，
    # 而不是向已有 .log 尾部追加。
    #
    # 原因：LevelDB 的 WAL 记录按 32KB 块分片（first/middle/last）。若向一个
    # 末尾未对齐到块边界的旧 .log 追加，新记录的分片帧会接在旧记录残留的
    # 半块之后，纯 Python 读取器在跨块重组时可能错位，导致读回的值损坏。
    # 新建文件保证每条 WAL 记录都从块边界开始，分片重组无歧义。
    #
    # LevelDB 按文件号顺序重放 WAL，编号最大的 .log 最后应用，即"最后写入
    # 获胜"，因此新建更高编号的文件即可覆盖同一 key 的旧值，语义与追加一致。
    log_path = ldb_dir / f"{_next_file_number(ldb_dir):06d}.log"

    # Build WriteBatch: [seq(8)][count(4)][op=1][klen varint][key][vlen varint][value]
    # seq 用 WAL_BATCH_SEQUENCE（而非 0）：见该常量说明，避免被已提交的高序列
    # 旧值覆盖，确保回编后的改动在游戏里真正生效。
    batch = struct.pack("<Q", WAL_BATCH_SEQUENCE) + struct.pack("<I", 1)  # seq, count=1
    batch += bytes([1])  # op = PUT
    batch += _varint_encode(len(key)) + key
    batch += _varint_encode(len(value)) + value

    BLOCK = 32768
    HEADER = 7  # crc(4) + length(2) + type(1)

    current_size = log_path.stat().st_size if log_path.exists() else 0
    pos = current_size
    remaining = batch
    first = True

    with open(log_path, "ab") as f:
        while remaining:
            block_remaining = BLOCK - (pos % BLOCK)
            if block_remaining < HEADER:
                # Pad to next block boundary with zero bytes
                f.write(b"\x00" * block_remaining)
                pos += block_remaining
                block_remaining = BLOCK

            chunk_size = min(len(remaining), block_remaining - HEADER)
            chunk = remaining[:chunk_size]
            remaining = remaining[chunk_size:]

            if first and not remaining:
                rtype = 1  # full
            elif first:
                rtype = 2  # first
            elif remaining:
                rtype = 3  # middle
            else:
                rtype = 4  # last

            # CRC covers type(1) + payload(chunk)
            crc = _crc32c(bytes([rtype]) + chunk)
            record = (
                struct.pack("<I", crc)
                + struct.pack("<H", len(chunk))
                + bytes([rtype])
                + chunk
            )
            f.write(record)
            pos += HEADER + len(chunk)
            first = False

    return log_path
