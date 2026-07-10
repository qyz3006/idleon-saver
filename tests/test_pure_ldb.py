"""Tests for the pure-Python LevelDB reader (idleon_saver/pure_ldb.py).

Constructs synthetic SSTable/WAL files in temp dirs and verifies the reader
extracts values correctly — no plyvel or real LevelDB required.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from idleon_saver import pure_ldb


# --------------------------------------------------------------------------- #
# helpers to build synthetic leveldb files
# --------------------------------------------------------------------------- #
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _build_block(entries: list[tuple[bytes, bytes]]) -> bytes:
    """Build an uncompressed leveldb data/index block from (key, value) pairs."""
    body = bytearray()
    prev = b""
    restarts = bytearray()
    for i, (key, value) in enumerate(entries):
        if i == 0 or i % 16 == 0:  # restart point
            restarts += struct.pack("<I", len(body))
        # prefix-compress
        shared = 0
        while (shared < len(prev) and shared < len(key)
               and prev[shared] == key[shared]):
            shared += 1
        unshared = len(key) - shared
        body += _varint(shared) + _varint(unshared) + _varint(len(value))
        body += key[shared:] + value
        prev = key
    restarts += struct.pack("<I", len(restarts) // 4 + 0)  # num_restarts... fix below
    # actually num_restarts is a separate trailing uint32
    block = bytes(body) + bytes(restarts) + struct.pack("<I", len(restarts) // 4)
    return block


def _block_with_trailer(block: bytes, compress: int = 0) -> tuple[bytes, bytes]:
    """Return (block_bytes_without_trailer, full_block_with_trailer).
    Also returns the raw block for handle size."""
    trailer = bytes([compress]) + b"\x00\x00\x00\x00"  # type + crc (crc unused by reader)
    return block, block + trailer


def _build_sstable(entries: list[tuple[bytes, bytes]]) -> bytes:
    """Build a complete .ldb SSTable file from (internal_key, value) entries."""
    data_block = _build_block(entries)
    data_off = 0
    # index block: one entry per data block; key = last key, value = handle
    handle = _varint(data_off) + _varint(len(data_block))
    last_key = entries[-1][0] if entries else b"\xff"
    index_block = _build_block([(last_key + b"\xff", handle)])
    # assemble: data_block+trailer + index_block+trailer + footer
    data_full = data_block + bytes([0]) + b"\x00\x00\x00\x00"
    index_off = len(data_full)
    index_full = index_block + bytes([0]) + b"\x00\x00\x00\x00"
    # footer: metaindex_handle + index_handle + padding + magic (8 bytes)
    meta_handle = _varint(0) + _varint(0)  # dummy metaindex
    idx_handle = _varint(index_off) + _varint(len(index_block))
    footer_body = meta_handle + idx_handle
    footer_body += b"\x00" * (40 - len(footer_body))  # pad to 40 bytes
    footer = footer_body + struct.pack("<Q", pure_ldb._LEVELDB_MAGIC)
    return data_full + index_full + footer


def _internal_key(user_key: bytes, seq: int = 1, ktype: int = 1) -> bytes:
    """Build a leveldb internal key: user_key + 8-byte (seq<<8|type) LE."""
    return user_key + struct.pack("<Q", (seq << 8) | ktype)


# --------------------------------------------------------------------------- #
# Snappy decompressor
# --------------------------------------------------------------------------- #
class TestSnappy:
    def test_uncompressed_literal(self):
        # snappy blob: uncompressed_len=5 (varint 0x05), then literal tag for 5 bytes
        # tag 0b00010100 = 0x14 → literal, len=(0x14>>2)+1 = 5+1 = 6... no
        # literal tag: len = (tag>>2)+1. For len=5: tag>>2 = 4, tag = 0x10
        blob = bytes([0x05, 0x10]) + b"hello"
        assert pure_ldb._snappy_decompress(blob) == b"hello"

    def test_empty(self):
        # uncompressed_len=0, no elements
        assert pure_ldb._snappy_decompress(bytes([0x00])) == b""

    def test_copy_2byte_offset(self):
        # "abc" then copy back 3 bytes (offset=3, len=3) → "abcabc"
        # uncompressed_len=6 (0x06)
        # literal "abc": tag 0x0c (len=(0x0c>>2)+1=3+1=4... no, 0x0c>>2=3, +1=4. wrong)
        # for len=3: tag>>2=2, tag=0x08
        # copy2: tag 0b00000010 | (len-1<<2). len=3: (3-1)<<2=8, tag=0x0a. offset=3: 03 00
        blob = bytes([0x06, 0x08]) + b"abc" + bytes([0x0a, 0x03, 0x00])
        assert pure_ldb._snappy_decompress(blob) == b"abcabc"

    def test_long_text_roundtrip(self):
        """Compress a long repetitive text with python-snappy (if available) and
        verify our decompressor matches."""
        snappy = pytest.importorskip("snappy")
        text = (b"oy14:dummyMonsterIDi23y5:NodeX" * 1000)
        compressed = snappy.compress(text)
        assert pure_ldb._snappy_decompress(compressed) == text


# --------------------------------------------------------------------------- #
# SSTable reader
# --------------------------------------------------------------------------- #
class TestSSTableReader:
    def test_read_single_entry(self, tmp_path):
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        value = b"\x01oy14:dummyMonsterIDi23g"
        ik = _internal_key(user_key)
        sstable = _build_sstable([(ik, value)])
        (tmp_path / "000001.ldb").write_bytes(sstable)
        result = pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave")
        assert result == value

    def test_skips_delete_tombstone(self, tmp_path):
        """A deletion tombstone (type=0) for mySave must be skipped."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        tombstone = _internal_key(user_key, ktype=0)  # type=0 = delete
        sstable = _build_sstable([(tombstone, b"")])
        (tmp_path / "000001.ldb").write_bytes(sstable)
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") is None

    def test_newest_ldb_wins(self, tmp_path):
        """When multiple .ldb files have the key, the highest file number wins."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        (tmp_path / "000001.ldb").write_bytes(
            _build_sstable([(_internal_key(user_key), b"\x01OLD")])
        )
        (tmp_path / "000005.ldb").write_bytes(
            _build_sstable([(_internal_key(user_key), b"\x01NEW")])
        )
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") == b"\x01NEW"

    def test_wal_takes_precedence_over_ldb(self, tmp_path):
        """A .log WAL entry overrides .ldb (WAL holds newer writes)."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        (tmp_path / "000001.ldb").write_bytes(
            _build_sstable([(_internal_key(user_key), b"\x01FROM_LDB")])
        )
        # build a WAL record: WriteBatch with 1 put
        batch = struct.pack("<Q", 1) + struct.pack("<I", 1)  # seq=1, count=1
        batch += bytes([1])  # op=put
        batch += _varint(len(user_key)) + user_key
        batch += _varint(len(b"\x01FROM_WAL")) + b"\x01FROM_WAL"
        record = struct.pack("<I", 0) + struct.pack("<H", len(batch)) + bytes([1]) + batch
        (tmp_path / "000002.log").write_bytes(record)
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") == b"\x01FROM_WAL"

    def test_wal_last_write_wins(self, tmp_path):
        """When a WAL has multiple writes to the same key, the LAST one wins
        (LevelDB WAL semantics: later writes override earlier ones)."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        # WriteBatch with 2 puts to the same key: OLD then NEW
        batch = struct.pack("<Q", 1) + struct.pack("<I", 2)  # seq=1, count=2
        # put 1: OLD value
        batch += bytes([1])
        batch += _varint(len(user_key)) + user_key
        batch += _varint(len(b"\x01OLD")) + b"\x01OLD"
        # put 2: NEW value
        batch += bytes([1])
        batch += _varint(len(user_key)) + user_key
        batch += _varint(len(b"\x01NEW")) + b"\x01NEW"
        record = struct.pack("<I", 0) + struct.pack("<H", len(batch)) + bytes([1]) + batch
        (tmp_path / "000001.log").write_bytes(record)
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") == b"\x01NEW"

    def test_no_match_returns_none(self, tmp_path):
        user_key = b"_file://\x00\x01/E:/game/index.html:otherKey"
        (tmp_path / "000001.ldb").write_bytes(
            _build_sstable([(_internal_key(user_key), b"\x01data")])
        )
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") is None

    def test_missing_dir_returns_none(self, tmp_path):
        assert pure_ldb.read_value_by_key_suffix(tmp_path / "nope", b"mySave") is None


# --------------------------------------------------------------------------- #
# WAL write (pure Python)
# --------------------------------------------------------------------------- #
class TestWALWrite:
    def test_write_then_read(self, tmp_path):
        """Write a value via pure-Python WAL, read it back — should match."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        value = b"\x01oy14:dummyMonsterIDi42g"
        log_path = pure_ldb.write_value_wal(tmp_path, user_key, value)
        assert log_path.exists()
        result = pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave")
        assert result == value

    def test_write_overrides_ldb(self, tmp_path):
        """WAL write overrides existing .ldb value (last-write-wins)."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        (tmp_path / "000001.ldb").write_bytes(
            _build_sstable([(_internal_key(user_key), b"\x01OLD_FROM_LDB")])
        )
        pure_ldb.write_value_wal(tmp_path, user_key, b"\x01NEW_FROM_WAL")
        assert pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave") == b"\x01NEW_FROM_WAL"

    def test_write_large_value_fragments(self, tmp_path):
        """Large values (>32KB) are fragmented across multiple WAL records."""
        user_key = b"_file://\x00\x01/E:/game/index.html:mySave"
        # 100KB value — must span multiple 32KB blocks
        value = b"\x01" + b"x" * 100000
        pure_ldb.write_value_wal(tmp_path, user_key, value)
        result = pure_ldb.read_value_by_key_suffix(tmp_path, b"index.html:mySave")
        assert result == value
        assert len(result) == 100001

    def test_crc32c_correctness(self):
        """CRC32C must match LevelDB's expected values."""
        # Known CRC32C test vector: CRC32C of "123456789" = 0xE3069283
        assert pure_ldb._crc32c(b"123456789") == 0xE3069283
