"""Logging helpers for idleon-saver.

Modules should obtain a logger via :func:`get_logger` and never configure
handlers themselves at import time. :func:`configure_logging` is called once
from the CLI entry point (:mod:`idleon_saver.cli`) and from the GUI so that
user-facing output is routed to the console.
"""

import logging
import sys
from typing import Optional

_CONFIGURED = False


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger for *name*.

    Args:
        name: Usually ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance (no handlers attached here).
    """
    return logging.getLogger(name)


def configure_logging(level: int = logging.INFO, to_stdout: bool = False) -> None:
    """Configure the root logger with a single stream handler.

    Idempotent: repeated calls will not add duplicate handlers.

    Args:
        level: Minimum log level to emit.
        to_stdout: When ``True`` log to ``sys.stdout``; otherwise ``sys.stderr``.
    """
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)

    if _CONFIGURED:
        return

    stream = sys.stdout if to_stdout else sys.stderr
    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)
    _CONFIGURED = True
