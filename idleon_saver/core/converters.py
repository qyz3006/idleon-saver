"""Defensive conversion helpers (ARB / TB / IE patterns).

These never raise. They are the building blocks the data loader and parsers
use to tolerate malformed or missing input.
"""

import json
import logging
from collections.abc import Mapping
from typing import Any, Callable, Optional, TypeVar

from idleon_saver.log import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def safe_get(obj: Any, key: Any, default: Any = None) -> Any:
    """Return ``obj[key]`` when *obj* is a mapping with *key*; else *default*.

    Never raises ``KeyError``/``TypeError`` (e.g. when *obj* is a list).

    Args:
        obj: Possibly a mapping.
        key: Key to look up.
        default: Value returned when *obj* is not a mapping or lacks *key*.

    Returns:
        The looked-up value or *default*.
    """
    try:
        if isinstance(obj, Mapping):
            return obj.get(key, default)
    except Exception:  # pragma: no cover - defensive
        pass
    return default


def safe_json_parse(raw: Any, default: Any = None) -> Any:
    """Parse *raw* as JSON, returning *default* on failure.

    Args:
        raw: A string/bytes JSON document, or ``None``.
        default: Value returned when parsing fails or *raw* is ``None``.

    Returns:
        The parsed object, or *default*.
    """
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def safer_convert(
    value: Any, typ: Callable[[Any], T], default: Any = None, *, warn: bool = True
) -> Any:
    """Convert *value* with *typ*, returning *default* on failure.

    Args:
        value: The value to convert.
        typ: A callable performing the conversion (e.g. ``int``).
        default: Value returned when conversion raises.
        warn: When ``True``, log a warning on failure.

    Returns:
        ``typ(value)`` or *default*.
    """
    try:
        return typ(value)
    except Exception as exc:  # noqa: BLE001 - intentionally swallow all
        if warn:
            logger.warning(
                "Conversion failed for value %r with %s: %s",
                value,
                getattr(typ, "__name__", repr(typ)),
                exc,
            )
        return default


def try_to_parse(parser: Callable[[Any], T], value: Any, default: Any = None) -> Any:
    """Run ``parser(value)``, swallowing exceptions and returning *default*.

    This is the IdleOnAutoReviewBot / TB ``tryToParse`` pattern.

    Args:
        parser: A callable that may raise.
        value: Argument passed to *parser*.
        default: Value returned when *parser* raises.

    Returns:
        ``parser(value)`` or *default*.
    """
    try:
        return parser(value)
    except Exception:  # noqa: BLE001 - intentionally swallow all
        return default
