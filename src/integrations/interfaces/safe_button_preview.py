"""Opt-in, read-only GPIO preview of the M0.18 selected-target policy.

Only status/get-target RPCs are sent. Never sends transport commands, never
retries, never exposes device UIDs, and never blocks the GPIO sampling loop.
"""
import json
import socket
import threading

try:
    from .safe_button_policy import plan_playpause
except ImportError:
    from safe_button_policy import plan_playpause


def read_snapshot(socket_path, command, *, timeout=1.0):
    """Read exactly one response to one approved read-only command."""
    if command not in ('status', 'get-target'):
        raise ValueError('preview reader only permits status/get-target')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(socket_path))
        client.sendall((json.dumps({'command': command}) + '\n').encode('utf-8'))
        data = bytearray()
        while b'\n' not in data and len(data) <= 16384:
            block = client.recv(4096)
            if not block:
                break
            data.extend(block)
    if len(data) > 16384 or b'\n' not in data:
        raise RuntimeError('invalid preview response')
    reply = json.loads(data.split(b'\n', 1)[0])
    if not isinstance(reply, dict):
        raise RuntimeError('invalid preview payload')
    return reply


class SelectedTargetPreview:
    """One in-flight inspection; noisy/rapid presses are dropped, not queued."""

    def __init__(self, socket_path, *, reader=read_snapshot, report=print):
        self.socket_path = socket_path
        self.reader = reader
        self.report = report
        self._lock = threading.Lock()
        self._busy = False

    def submit(self):
        with self._lock:
            if self._busy:
                return False
            self._busy = True

        def perform():
            try:
                status = self.reader(self.socket_path, 'status')
                selection = self.reader(self.socket_path, 'get-target')
                command = plan_playpause(status, selection)
                self.report('selected-target preview: ' +
                            (command if command else 'blocked'))
            except Exception:
                self.report('selected-target preview: unavailable')
            finally:
                with self._lock:
                    self._busy = False

        try:
            threading.Thread(target=perform, name='selected-target-preview',
                             daemon=True).start()
        except Exception:
            with self._lock:
                self._busy = False
            raise
        return True
