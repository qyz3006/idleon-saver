"""Tests for wrapped↔unwrapped conversion (editor.py).

Verifies that unwrapped display + overlay-back-on-save is lossless: the
re-wrapped structure encodes to the same Stencyl string as the original.
This is what lets the editor show compact unwrapped JSON (5.7MB) instead of
bloated wrapped JSON (31.7MB) without losing type info.
"""

from __future__ import annotations

import json

import pytest

from idleon_saver import editor
from idleon_saver.stencyl.decoder import StencylDecoder
from idleon_saver.stencyl.encoder import StencylEncoder


def _wrap(stencyl: str) -> dict:
    return StencylDecoder(stencyl).result.wrapped


# Minimal valid stencyl: object with int, string, array, nested dict
# o=object(g) y14:=str len14 i15=int y5:=str len5 a=array(h) ... h g
SAMPLE = "oy14:dummyMonsterIDi15y5:NodeXai13i929i140hg"


class TestWrappedUnwrapped:
    def test_unwrap_leaf(self):
        w = {"start": "i", "contents": 42}
        assert editor.wrapped_to_unwrapped(w) == 42

    def test_unwrap_string(self):
        w = {"start": "y", "contents": "hello"}
        assert editor.wrapped_to_unwrapped(w) == "hello"

    def test_unwrap_dict(self):
        w = _wrap(SAMPLE)
        u = editor.wrapped_to_unwrapped(w)
        assert isinstance(u, dict)
        assert u["dummyMonsterID"] == 15
        assert u["NodeX"] == [13, 929, 140]

    def test_roundtrip_lossless(self):
        """unwrap → overlay back → encode == original stencyl."""
        w = _wrap(SAMPLE)
        u = editor.wrapped_to_unwrapped(w)
        w2 = editor.overlay_unwrapped(w, u)
        assert StencylEncoder(w2).result == SAMPLE

    def test_edit_propagates(self):
        """Editing a value in unwrapped must propagate to the re-encoded stencyl."""
        w = _wrap(SAMPLE)
        u = editor.wrapped_to_unwrapped(w)
        u["dummyMonsterID"] = 99  # edit
        w2 = editor.overlay_unwrapped(w, u)
        encoded = StencylEncoder(w2).result
        # The encoded stencyl should now contain i99 instead of i15
        assert "i99" in encoded
        assert "i15" not in encoded

    def test_edit_nested_array(self):
        w = _wrap(SAMPLE)
        u = editor.wrapped_to_unwrapped(w)
        u["NodeX"][0] = 777
        w2 = editor.overlay_unwrapped(w, u)
        encoded = StencylEncoder(w2).result
        assert "i777" in encoded

    def test_int_keys_match_after_json_roundtrip(self):
        """JSON converts int dict keys to str; overlay must still match them.

        Note: the StencylEncoder has a pre-existing limitation encoding int
        dict keys (it calls quote() on them). Real Legends of Idleon saves
        don't use IntMaps with int keys (verified via byte-identical roundtrip
        on the actual save), so this tests the overlay matching only, not
        the full encode.
        """
        # Build a wrapped IntMap-like structure manually (avoiding encoder).
        w = {"start": "q", "contents": {
            1: {"start": "i", "contents": 2},
            3: {"start": "i", "contents": 4},
        }, "end": "h"}
        u = editor.wrapped_to_unwrapped(w)
        # JSON round-trip stringifies keys: {"1": 2, "3": 4}
        u_json = json.loads(json.dumps(u))
        assert u_json == {"1": 2, "3": 4}
        # Overlay must match stringified keys back to original int keys
        w2 = editor.overlay_unwrapped(w, u_json)
        # Original int keys preserved, values overlaid
        assert w2["contents"][1]["contents"] == 2
        assert w2["contents"][3]["contents"] == 4
        # Edit propagates through stringified key
        u_json["1"] = 99
        w3 = editor.overlay_unwrapped(w, u_json)
        assert w3["contents"][1]["contents"] == 99

    def test_missing_key_preserved(self):
        """Keys absent from unwrapped (user deleted) keep original value."""
        w = _wrap(SAMPLE)
        u = editor.wrapped_to_unwrapped(w)
        del u["dummyMonsterID"]
        w2 = editor.overlay_unwrapped(w, u)
        # dummyMonsterID should still be 15 in the re-encoded stencyl
        encoded = StencylEncoder(w2).result
        assert "i15" in encoded
