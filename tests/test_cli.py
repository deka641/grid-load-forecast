import socket

import pytest

from gridload import __version__
from gridload.cli import main
from tests.conftest import NetworkAccessError


def test_main_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: gridload" in capsys.readouterr().out


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_network_guard_blocks_connections() -> None:
    with pytest.raises(NetworkAccessError):
        socket.create_connection(("example.org", 443))
