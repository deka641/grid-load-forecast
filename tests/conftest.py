"""Test-wide network guard.

Tests must never reach the real data source. The guard is installed once at
configuration time, so it also covers import-time code and fixtures of any scope.
Every blocked attempt is recorded, and a test fails at teardown if it made one,
even when the code under test swallowed the exception.
"""

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest


class NetworkAccessError(RuntimeError):
    """Raised when code under test tries to use the network."""


_attempts: list[str] = []


def _blocker(name: str) -> Callable[..., Any]:
    def guard(*args: Any, **_kwargs: Any) -> Any:
        _attempts.append(f"{name}{args[1:] if name.startswith('socket.') else args}")
        raise NetworkAccessError(f"network access is disabled in tests ({name}); use fixtures")

    return guard


def _inet_only(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    blocked = _blocker(name)

    def guard(self: socket.socket, *args: Any, **kwargs: Any) -> Any:
        if self.family == socket.AF_UNIX:
            return original(self, *args, **kwargs)
        return blocked(self, *args, **kwargs)

    return guard


def install_network_guard() -> None:
    """Replace every outbound entry point of the socket module with a blocker."""
    for name in (
        "getaddrinfo",
        "getnameinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "create_connection",
    ):
        setattr(socket, name, _blocker(name))
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        original = getattr(socket.socket, name)
        setattr(socket.socket, name, _inet_only(f"socket.{name}", original))


def pytest_configure(config: pytest.Config) -> None:
    install_network_guard()


def _take_attempts() -> str:
    found = ", ".join(_attempts)
    _attempts.clear()
    return found


@pytest.fixture(autouse=True)
def _fail_on_network_attempt() -> Iterator[None]:
    if _attempts:
        pytest.fail(f"network access before this test (import or fixture): {_take_attempts()}")
    yield
    if _attempts:
        pytest.fail(f"test attempted network access: {_take_attempts()}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _attempts:
        print(f"\nnetwork access outside of tests: {_take_attempts()}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def network_attempts() -> Iterator[list[str]]:
    """Expose recorded attempts and clear them, for tests of the guard itself."""
    yield _attempts
    _attempts.clear()
