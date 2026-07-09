"""Intermediate model, defensive converters, and pure parsers.

This package sits between the frozen decode core and the exporters. It holds:

* :mod:`idleon_saver.core.model` -- the source-agnostic ``Account``/``Character``
  dataclasses that exporters consume.
* :mod:`idleon_saver.core.converters` -- defensive helpers (``safe_get``,
  ``safe_json_parse``, ``safer_convert``, ``try_to_parse``).
* :mod:`idleon_saver.core.parsers` -- pure ``get_xxx`` parser functions.
"""
