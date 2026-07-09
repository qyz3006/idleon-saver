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
        to_stdout: When ``True`` log to ``sys.__stdout__``; otherwise
            ``sys.__stderr__``. The "dunder" streams are used deliberately:
            kivy replaces ``sys.stdout``/``sys.stderr`` with its own
            :class:`~kivy.logger.LogFile` wrapper whose ``write`` calls
            ``Logger.warning``. Routing our handler to that redirected stream
            would re-enter kivy's logger and recurse infinitely at startup.
            When no real console stream exists (e.g. a PyInstaller
            ``--windowed`` build) the dunder stream is ``None``; we then skip
            the console handler entirely since kivy still writes to its log file.
    """
    global _CONFIGURED
    root = logging.getLogger()
    root.setLevel(level)

    if _CONFIGURED:
        return

    # Use the original (pre-kivy-redirection) streams so output is not fed
    # back into kivy's redirected sys.stderr -> Logger.warning recursion.
    stream = sys.__stdout__ if to_stdout else sys.__stderr__
    if stream is None:
        # No real console in this environment (e.g. frozen --windowed exe).
        # kivy's file handler still captures the logs for the bug-report zip.
        _CONFIGURED = True
        return

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    formatter = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    handler.setFormatter(formatter)
    root.addHandler(handler)
    _CONFIGURED = True
