"""Exception hierarchy for idleon-saver.

All custom errors derive from :class:`IdleonSaverError` so callers can catch
every idleon-saver specific failure with a single ``except`` clause.
"""


class IdleonSaverError(Exception):
    """Base class for all idleon-saver errors."""


class DecodeError(IdleonSaverError):
    """Raised when decoding the Stencyl blob or reading the LevelDB fails."""


class EncodeError(IdleonSaverError):
    """Raised when re-encoding data back into Stencyl format fails."""


class DataLoadError(IdleonSaverError):
    """Raised when vendored static game-data is unrecoverable.

    Note: a *missing* vendored file is NOT a :class:`DataLoadError` -- it is
    tolerated (empty default + warning). This is reserved for truly broken
    data that cannot be worked around.
    """


class ExportError(IdleonSaverError):
    """Raised when an exporter fails to produce its output."""


class SourceError(IdleonSaverError):
    """Raised when the save source is unknown or unsupported."""


class ChromeControllerError(IdleonSaverError):
    """Raised when driving the live game via ChromeController fails."""
