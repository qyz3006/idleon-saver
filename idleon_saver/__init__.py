"""idleon-saver: convert Legends of Idleon save data to and from JSON."""

try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("idleon_saver")
except Exception:  # pragma: no cover - importlib may be unavailable
    __version__ = "0.0.0"
