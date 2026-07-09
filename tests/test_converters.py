"""Tests for the defensive conversion helpers in idleon_saver.core.converters."""

import logging

import pytest

from idleon_saver.core.converters import (
    safe_get,
    safe_json_parse,
    safer_convert,
    try_to_parse,
)


def test_safe_get_present():
    assert safe_get({"a": 1}, "a") == 1


def test_safe_get_missing_default():
    assert safe_get({"a": 1}, "b", "default") == "default"


def test_safe_get_non_mapping_default():
    # A list is not a Mapping; safe_get must not raise (no __getitem__ KeyError).
    assert safe_get([1, 2, 3], 0, "default") == "default"
    assert safe_get(None, "x", "default") == "default"


def test_safe_get_swallows_exceptions():
    class Boom:
        def get(self, key, default=None):
            raise RuntimeError("boom")

    assert safe_get(Boom(), "x", "default") == "default"


def test_safe_json_parse_valid():
    assert safe_get  # keep linters happy about imports
    assert safe_json_parse('{"a": 1}') == {"a": 1}
    assert safe_json_parse("[1, 2, 3]") == [1, 2, 3]
    assert safe_json_parse(b'{"b": 2}') == {"b": 2}


def test_safe_json_parse_invalid_default():
    assert safe_json_parse("not json", "default") == "default"
    assert safe_json_parse(None, "default") == "default"


def test_safer_convert_success():
    assert safer_convert("5", int) == 5
    assert safer_convert("3.5", float) == 3.5


def test_safer_convert_failure_default():
    assert safer_convert("x", int, 0) == 0
    assert safer_convert("x", int, 0, warn=False) == 0


def test_safer_convert_warns(caplog):
    with caplog.at_level(logging.WARNING):
        assert safer_convert("x", int, -1) == -1
    assert any("Conversion failed" in r.message for r in caplog.records)


def test_try_to_parse_success():
    assert try_to_parse(int, "7") == 7


def test_try_to_parse_failure_default():
    assert try_to_parse(int, "bad", 0) == 0
    assert try_to_parse(lambda x: x / 0, 1, "fallback") == "fallback"
