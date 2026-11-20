import asyncio
import contextlib
import socket

import httpx2
import pytest

from tests.conftest import NetworkAccessError

URL = "https://www.smard.de/app/chart_data/410/DE/index_hour.json"


def test_dns_lookup_is_blocked(network_attempts: list[str]) -> None:
    with pytest.raises(NetworkAccessError):
        socket.getaddrinfo("www.smard.de", 443)
    assert network_attempts[0].startswith("getaddrinfo")


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_tcp_connect_is_blocked(method: str, network_attempts: list[str]) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkAccessError),
    ):
        getattr(sock, method)(("192.0.2.1", 443))
    assert network_attempts == [f"socket.{method}(('192.0.2.1', 443),)"]


def test_udp_send_is_blocked(network_attempts: list[str]) -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(NetworkAccessError),
    ):
        sock.sendto(b"x", ("192.0.2.1", 53))
    assert len(network_attempts) == 1


def test_unix_sockets_stay_usable() -> None:
    left, right = socket.socketpair()
    with left, right:
        left.sendall(b"ping")
        assert right.recv(4) == b"ping"


def test_sync_http_client_is_blocked(network_attempts: list[str]) -> None:
    with httpx2.Client() as client, pytest.raises(NetworkAccessError):
        client.get(URL)
    assert network_attempts


def test_async_http_client_is_blocked(network_attempts: list[str]) -> None:
    async def fetch() -> None:
        async with httpx2.AsyncClient() as client:
            await client.get(URL)

    with pytest.raises(Exception):  # noqa: B017, PT011  # the transport wraps the guard error
        asyncio.run(fetch())
    assert network_attempts


def test_swallowed_attempt_is_still_recorded(network_attempts: list[str]) -> None:
    with contextlib.suppress(Exception):
        socket.create_connection(("www.smard.de", 443))
    assert network_attempts == ["create_connection(('www.smard.de', 443),)"]
