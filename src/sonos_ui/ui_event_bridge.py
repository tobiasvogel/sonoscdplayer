"""Optional local, best-effort GPIO event stream; never issues player commands.

The existing button process alone reads GPIO. A separate UI process receives
bounded semantic events on a private Unix datagram socket. Events are dropped
if the UI process is absent or busy; no backlog, replay or retries.
"""
import json
import os
from pathlib import Path
import socket
import stat
import queue
import threading

ALLOWED = frozenset({
    ('encoder', 'left'), ('encoder', 'right'),
    ('encoder_sw', 'pressed'), ('power', 'short'),
    ('speaker_select', 'pressed'),
    ('connect', 'status'), ('connect', 'settings'), ('connect', 'settings_hint'),
    ('connect', 'setup_requested'),
    *(('connect', f'warning_{seconds}') for seconds in range(1, 6)),
})
MAX_EVENT_BYTES = 128


def encode_event(name, event):
    if (name, event) not in ALLOWED:
        return None
    return json.dumps({'name': name, 'event': event}, separators=(',', ':')).encode('ascii')


def decode_event(data):
    if not isinstance(data, bytes) or len(data) > MAX_EVENT_BYTES:
        return None
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(payload, dict) or set(payload) != {'name', 'event'}:
        return None
    pair = (payload['name'], payload['event'])
    return pair if pair in ALLOWED else None


def publish_event(path, name, event):
    """Single bounded nonblocking send; safe to call from GPIO sampling loop."""
    data = encode_event(name, event)
    if data is None:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.setblocking(False)
            client.sendto(data, str(path))
        return True
    except (OSError, BlockingIOError):
        return False


class EventReceiver:
    """A single UI owner binds a private datagram socket; no stale path unlink."""
    def __init__(self, path):
        self.path = Path(path)
        self.sock = None
        self.inode = None

    def __enter__(self):
        if not self.path.parent.is_dir():
            raise FileNotFoundError('event socket directory missing')
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError('refusing existing event socket path')
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        old = os.umask(0o177)
        try:
            sock.bind(str(self.path))
        except BaseException:
            sock.close()
            raise
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        self.inode = os.lstat(self.path).st_ino
        sock.setblocking(False)
        self.sock = sock
        return self

    def receive(self):
        try:
            raw = self.sock.recv(MAX_EVENT_BYTES + 1)
        except BlockingIOError:
            return None
        return decode_event(raw)

    def __exit__(self, *_):
        self.sock.close()
        try:
            info = os.lstat(self.path)
            if stat.S_ISSOCK(info.st_mode) and info.st_ino == self.inode:
                self.path.unlink()
        except FileNotFoundError:
            pass


class AsyncEventPublisher:
    """Bounded fire-and-forget transport. GPIO thread only calls enqueue().

    Queue capacity is fixed; no retries, no pending events across restarts.
    Sender runs on a daemon thread and is the only code touching the socket.
    """
    def __init__(self, path, *, max_pending=16, sender=publish_event):
        if not 1 <= max_pending <= 128:
            raise ValueError('invalid queue capacity')
        self.path = str(path)
        self._queue = queue.Queue(maxsize=max_pending)
        self._sender = sender
        self._closed = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError('publisher already started')
        self._thread = threading.Thread(target=self._run, name='ui-event-publisher', daemon=True)
        self._thread.start()
        return self

    def enqueue(self, name, event):
        """Never waits and never touches a socket. Returns False when dropped."""
        if self._closed.is_set() or encode_event(name, event) is None:
            return False
        try:
            self._queue.put_nowait((name, event))
            return True
        except queue.Full:
            return False

    def _run(self):
        while not self._closed.is_set():
            try:
                name, event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._closed.is_set():
                break
            try:
                self._sender(self.path, name, event)
            except Exception:
                pass

    def close(self):
        """Discard pending UI events. No drain or retry on shutdown."""
        self._closed.set()
