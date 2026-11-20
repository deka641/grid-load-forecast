"""Shared fixtures. Blocks all outbound network access during tests."""

import socket
from typing import Any

import pytest


class NetworkAccessError(RuntimeError):
    """Raised when a test tries to open a network connection."""


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def guard(*_args: Any, **_kwargs: Any) -> None:
        raise NetworkAccessError("network access is disabled in tests; use fixtures")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket, "create_connection", guard)
