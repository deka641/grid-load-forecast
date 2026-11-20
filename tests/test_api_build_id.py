"""Where the build id in the ETags comes from."""

from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from gridload.api import service
from gridload.api.app import create_app
from gridload.config import Settings
from gridload.pipeline import snapshot
from gridload.pipeline.db import migrate


@pytest.fixture
def build_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "BUILD_ID"
    monkeypatch.setattr(service, "BUILD_ID_FILE", path)
    monkeypatch.delenv("GRIDLOAD_BUILD_ID", raising=False)
    return path


def test_the_environment_wins_over_the_image_build_id(
    build_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build_file.write_text("image\n")
    monkeypatch.setenv("GRIDLOAD_BUILD_ID", "env")
    assert service.default_build_id() == "env"
    monkeypatch.setenv("GRIDLOAD_BUILD_ID", "")
    assert service.default_build_id() == "image"


def test_the_image_build_id_is_read_from_its_file(build_file: Path) -> None:
    build_file.write_text("  0123abcd\n")
    assert service.default_build_id() == "0123abcd"


@pytest.mark.parametrize("content", [None, "", " \n", b"\xff\xfe"])
def test_without_a_usable_file_the_process_start_stands_in(
    build_file: Path, content: str | bytes | None
) -> None:
    if isinstance(content, bytes):
        build_file.write_bytes(content)
    elif content is not None:
        build_file.write_text(content)
    assert service.default_build_id() == service.PROCESS_START


def test_a_restart_with_the_same_image_keeps_the_etag(tmp_path: Path, build_file: Path) -> None:
    """Two processes of one image derive the same tag; a new image derives a new one."""
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    migrate(con)
    settings = Settings(data_dir=tmp_path)
    snapshot.publish(con, settings.serving_path)

    def tag() -> str:
        return str(TestClient(create_app(settings)).get("/api/v1/forecasts/days").headers["etag"])

    build_file.write_text("first")
    first = tag()
    assert tag() == first
    build_file.write_text("second")
    assert tag() != first
