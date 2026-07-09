"""Regression test for the frozen-exe logging RecursionError.

Background
----------
``idleon_saver/gui/main.py`` does ``logging.Logger.manager.root = Logger`` so
that idleon-saver's own loggers route through kivy's ``Logger`` and land in
kivy's log file (used for the bug-report ``logs.zip``).

kivy 2.1 redirects ``sys.stderr`` to a ``LogFile`` wrapper whose ``write``
method calls ``Logger.warning`` (see ``kivy/logger.py``:
``sys.stderr = LogFile('stderr', Logger.warning)``). kivy's own
``ConsoleHandler`` writes to that redirected ``sys.stderr`` and only avoids
infinite recursion because it special-cases messages prefixed with
``stderr:`` (writing them to the *real* ``previous_stderr`` and returning
``False``).

The old ``configure_logging()`` attached a ``StreamHandler(sys.stderr)`` to the
(now kivy) root logger. That handler's output does **not** carry the
``stderr:`` prefix, so every emit re-entered ``Logger.warning`` -> the handler
again -> infinite ``RecursionError`` at startup.

The fix (``idleon_saver/log.py``) routes the handler to ``sys.__stderr__``
(the real, pre-redirection stream) instead, and skips the handler entirely when
that stream is ``None`` (e.g. a PyInstaller ``--windowed`` build).

This test mocks the kivy redirection with no GUI/display required.

Run directly:
    python tests/test_logging_no_recursion.py
Or via pytest:
    pytest tests/test_logging_no_recursion.py
"""

import logging
import sys
from pathlib import Path
from unittest import mock

# Make the repo root importable so we can exercise the *real* configure_logging.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from idleon_saver.log import configure_logging  # noqa: E402


class HijackedStderr:
    """Minimal stand-in for kivy's ``LogFile`` redirection of sys.stderr.

    Every write funnels back into ``logger.warning``, mirroring kivy's
    ``sys.stderr = LogFile('stderr', Logger.warning)``.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._buffer = ""

    def write(self, msg: str) -> None:
        self._buffer += msg
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            if line:
                self._logger.warning("stderr: " + line)

    def flush(self) -> None:
        if self._buffer:
            self._logger.warning("stderr: " + self._buffer)
            self._buffer = ""


def test_old_setup_recurses() -> str:
    """OLD behaviour: handler on the redirected sys.stderr -> RecursionError.

    Returns ``"OLD_RECURSES"`` when the old logic recurses as expected, or
    ``"OLD_OK"`` if it somehow did not (which would mean the premise is wrong).
    """
    kivy_logger = logging.getLogger("kivy_like_old")
    kivy_logger.setLevel(logging.INFO)
    try:
        with mock.patch.object(sys, "stderr", HijackedStderr(kivy_logger)):
            # OLD: configure_logging attached a StreamHandler(sys.stderr). In the
            # real app kivy has *already* replaced sys.stderr by the time
            # configure_logging runs, so the handler captures the hijacked
            # (redirected) stream -- this is what triggers the recursion.
            kivy_logger.addHandler(logging.StreamHandler(sys.stderr))
            try:
                kivy_logger.info("hello")
            except RecursionError:
                return "OLD_RECURSES"
        return "OLD_OK"
    finally:
        logging.shutdown()
        for h in list(kivy_logger.handlers):
            kivy_logger.removeHandler(h)


def test_new_setup_no_recursion() -> str:
    """NEW behaviour: handler on the *real* (pre-redirection) stream -> no
    recursion.

    ``sys.__stderr__`` can be ``None`` in headless/frozen builds, so here we
    use a genuine non-redirected stream as its stand-in and confirm that writing
    to it never re-enters ``Logger.warning``. Returns ``"NEW_OK"`` when no
    ``RecursionError`` is raised.
    """
    import io

    kivy_logger = logging.getLogger("kivy_like_new")
    kivy_logger.setLevel(logging.INFO)
    real_stream = io.StringIO()  # stand-in for the un-redirected sys.__stderr__
    try:
        with mock.patch.object(sys, "stderr", HijackedStderr(kivy_logger)):
            # NEW: the fixed configure_logging routes the handler to the *real*
            # (pre-redirection) stream, so writing to it never re-enters
            # Logger.warning. Here we hand that real stream straight to the
            # handler (equivalent to StreamHandler(sys.__stderr__)).
            kivy_logger.addHandler(logging.StreamHandler(real_stream))
            kivy_logger.info("hello")  # must not raise RecursionError
        return "NEW_OK"
    finally:
        logging.shutdown()
        for h in list(kivy_logger.handlers):
            kivy_logger.removeHandler(h)


def test_configure_logging_real_code_no_recursion() -> None:
    """Directly exercise the fixed ``configure_logging()`` under a kivy-like
    redirection of ``sys.stderr``.

    Asserts:
      * no RecursionError
      * the message still reaches the *real* stream (sys.__stderr__)
      * idleon-saver logs still route through the (kivy) root logger
    """
    import io

    saved_root = logging.Logger.manager.root
    saved_dunder = sys.__stderr__
    real_capture = io.StringIO()
    sys.__stderr__ = real_capture  # real stream the fixed code must use

    kivy_logger = logging.getLogger("kivy_like_cfg")
    kivy_logger.setLevel(logging.INFO)
    logging.Logger.manager.root = kivy_logger  # main.py:40

    import idleon_saver.log as log_mod

    try:
        with mock.patch.object(sys, "stderr", HijackedStderr(kivy_logger)):
            log_mod._CONFIGURED = False
            configure_logging(level=logging.INFO)  # fixed version
            # Trigger the exact call from main(): Logger.info(...)
            logging.getLogger().info("Idleon Saver: version unknown")
        out = real_capture.getvalue()
        assert "Idleon Saver: version unknown" in out, f"msg not on real stderr: {out!r}"
    finally:
        sys.__stderr__ = saved_dunder
        logging.Logger.manager.root = saved_root
        logging.shutdown()
        for h in list(kivy_logger.handlers):
            kivy_logger.removeHandler(h)
        log_mod._CONFIGURED = False


def test_configure_logging_windowed_no_recursion() -> None:
    """When the real stream is ``None`` (PyInstaller --windowed), the handler
    is skipped and no RecursionError occurs -- kivy's file handler still logs.
    """
    saved_root = logging.Logger.manager.root
    saved_dunder = sys.__stderr__
    sys.__stderr__ = None  # frozen --windowed has no console stderr

    kivy_logger = logging.getLogger("kivy_like_win")
    kivy_logger.setLevel(logging.INFO)
    logging.Logger.manager.root = kivy_logger  # main.py:40

    import idleon_saver.log as log_mod

    try:
        with mock.patch.object(sys, "stderr", HijackedStderr(kivy_logger)):
            log_mod._CONFIGURED = False
            configure_logging(level=logging.INFO)  # must not raise
            # Even with the skipped handler, logging must not recurse.
            logging.getLogger().info("Idleon Saver: version unknown")
        # Handler must have been skipped (no StreamHandler added to root).
        assert not any(
            isinstance(h, logging.StreamHandler) for h in kivy_logger.handlers
        ), "unexpected StreamHandler attached in windowed mode"
    finally:
        sys.__stderr__ = saved_dunder
        logging.Logger.manager.root = saved_root
        logging.shutdown()
        for h in list(kivy_logger.handlers):
            kivy_logger.removeHandler(h)
        log_mod._CONFIGURED = False


if __name__ == "__main__":
    print(test_old_setup_recurses())
    print(test_new_setup_no_recursion())
    # Run the direct-code tests as plain assertions.
    test_configure_logging_real_code_no_recursion()
    test_configure_logging_windowed_no_recursion()
    print("ALL_DIRECT_TESTS_PASSED")
    print("DONE")
