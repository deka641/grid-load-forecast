"""Day-ahead forecast of German grid load from public SMARD data."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION = "grid-load-forecast"
# Reported when the code runs without installed package metadata, for example from
# a source tree on PYTHONPATH. The version itself is set only in pyproject.toml.
UNKNOWN_VERSION = "0+unknown"


def installed_version(distribution: str = DISTRIBUTION) -> str:
    """Version of the installed ``distribution``, or UNKNOWN_VERSION without metadata."""
    try:
        return version(distribution)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = installed_version()
