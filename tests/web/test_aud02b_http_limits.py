"""AUD-02b: bounded concurrent web requests and idle socket timeout."""
from http.server import BaseHTTPRequestHandler
from threading import Event, Thread
import socket

import pytest

from sonoscdplayer.web.http_server import (
    BoundedHTTPServer, MAX_CLIENTS, SOCKET_TIMEOUT_SECONDS,
)


class BlockingHandler(BaseHTTPRequestHandler):
    entered = Event()
    release = Event()

    def do_GET(self):
        self.entered.set()
        self.release.wait(3)
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'OK')

    def log_message(self, *_args):
        pass


def test_connection_timeout_and_client_limit(tmp_path):
    BlockingHandler.entered.clear()
    BlockingHandler.release.clear()
    server = BoundedHTTPServer(('127.0.0.1', 0), BlockingHandler, max_clients=1)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    first = second = None
    try:
        first = socket.create_connection(server.server_address, timeout=2)
        first.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        assert BlockingHandler.entered.wait(2)
        second = socket.create_connection(server.server_address, timeout=2)
        second.settimeout(2)
        second.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        assert second.recv(4096) == b''  # No second worker allowed.
        BlockingHandler.release.set()
        assert b'200' in first.recv(4096)
    finally:
        BlockingHandler.release.set()
        for conn in (first, second):
            if conn is not None:
                conn.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_default_limits_and_argument_validation():
    assert MAX_CLIENTS == 16
    assert SOCKET_TIMEOUT_SECONDS == 15
    with pytest.raises(ValueError):
        BoundedHTTPServer(('127.0.0.1', 0), BlockingHandler, max_clients=0)
