"""The package version comes from the installed metadata, set in pyproject.toml."""

import tomllib
from pathlib import Path

import gridload

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_the_one_in_pyproject() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == gridload.DISTRIBUTION
    assert gridload.__version__ == project["version"]


def test_missing_metadata_gives_the_unknown_version() -> None:
    assert gridload.installed_version("grid-load-forecast-not-installed") == "0+unknown"
    assert gridload.UNKNOWN_VERSION == "0+unknown"
