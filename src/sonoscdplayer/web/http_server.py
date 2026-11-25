"""Bound concurrent LAN HTTP clients and idle socket time on the web service.

No authentication is implied: this only limits resource exhaustion from stalled
connections, not the rate at which a client can make completed requests.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import threading


MAX_CLIENTS = 16
SOCKET_TIMEOUT_SECONDS = 15


class BoundedHTTPServer(ThreadingHTTPServer):
    """Reject excess connections before spawning a worker thread."""

    daemon_threads = True
    request_queue_size = 16

    def __init__(self, server_address, RequestHandlerClass, *, max_clients=MAX_CLIENTS):
        if type(max_clients) is not int or max_clients < 1:
            raise ValueError('invalid HTTP client limit')
        self._slots = threading.BoundedSemaphore(max_clients)
        super().__init__(server_address, RequestHandlerClass)

    def get_request(self):
        conn, address = super().get_request()
        conn.settimeout(SOCKET_TIMEOUT_SECONDS)
        return conn, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
