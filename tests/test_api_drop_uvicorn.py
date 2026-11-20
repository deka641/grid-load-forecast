"""A dropped slow reader, served by a real uvicorn over a Unix socket.

The ASGI tests check the messages the application sends; this test checks what
the client sees when the server acts on them: the status line, fewer body bytes
than Content-Length promised, then the closed connection.
"""

import logging
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from gridload.api.limits import ChunkedResponse, Limits

BODY = b"x" * (16 * 2**20)


def _app(done: threading.Event) -> FastAPI:
    app = FastAPI()

    @app.get("/big")
    async def big() -> ChunkedResponse:
        return ChunkedResponse(BODY, {}, done.set, Limits(send_timeout=0.3, min_throughput=1e12))

    return app


def _read_all(sock: socket.socket) -> bytes:
    data = b""
    while chunk := sock.recv(2**16):
        data += chunk
    return data


def test_a_dropped_reader_sees_a_truncated_body_and_a_closed_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "api.sock"
    done = threading.Event()
    config = uvicorn.Config(_app(done), uds=str(path), log_config=None, lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    with caplog.at_level(logging.WARNING):
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert time.monotonic() < deadline, "uvicorn did not start"
                time.sleep(0.01)
            with socket.socket(socket.AF_UNIX) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
                sock.connect(str(path))
                sock.sendall(b"GET /big HTTP/1.1\r\nHost: test\r\n\r\n")
                # Read nothing until the server has given up on this client.
                assert done.wait(10)
                sock.settimeout(10)
                head, _, body = _read_all(sock).partition(b"\r\n\r\n")
        finally:
            server.should_exit = True
            thread.join(10)
    lines = head.split(b"\r\n")
    assert lines[0] == b"HTTP/1.1 200 OK"
    assert f"content-length: {len(BODY)}".encode() in (line.lower() for line in lines)
    # Incomplete: a client that checks Content-Length knows the body is truncated.
    assert 0 < len(body) < len(BODY)
    messages = [r.getMessage() for r in caplog.records]
    assert any("dropped a client that was too slow to read" in m for m in messages)
    # uvicorn reports every response the application leaves incomplete at ERROR level.
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == ["ASGI callable returned without completing response."]
