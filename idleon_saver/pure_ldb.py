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
