"""M0.12c-r2: explicit safe mapping from button press to local player protocol.

No GPIO imports; dry-run never opens a socket. The player service remains
sole authority for Sonos ownership/topology/stream checks.
"""
from dataclasses import dataclass
import socket
import json
import re

ACTIONS = {'playpause': 'playpause', 'stop': 'stop', 'skip': 'next', 'previous': 'previous'}
UID_PATTERN = re.compile(r'^[A-Za-z0-9_:-]{1,128}$')


@dataclass(frozen=True)
class Command:
    name: str
    uid: str | None = None

    def payload(self):
        result = {'command': self.name}
        if self.uid is not None:
            result['uid'] = self.uid
        return result


def map_event(name, event, uid=None):
    """Only pressed events for four keys dispatch. No force; PLAYPAUSE is a service-owned toggle."""
    if event != 'pressed' or name not in ACTIONS:
        return None
    command = ACTIONS[name]
    if command == 'playpause':
        if not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid):
            raise ValueError('PLAYPAUSE requires an explicitly configured valid --uid')
        return Command(command, uid)
    return Command(command)


def send_command(path, command, *, timeout=120):
    """Exactly one request; never retry on uncertain/timeout outcome."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        client.connect(str(path))
        client.settimeout(timeout)
        client.sendall((json.dumps(command.payload()) + '\n').encode('utf8'))
        data = bytearray()
        while b'\n' not in data and len(data) < 8192:
            chunk = client.recv(2048)
            if not chunk:
                break
            data.extend(chunk)
        if b'\n' not in data:
            raise RuntimeError('incomplete service response; outcome UNKNOWN')
        result = json.loads(data.split(b'\n', 1)[0])
        if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
            raise ValueError('invalid service response')
        return result


class Bridge:
    def __init__(self, *, uid=None, socket_path='/tmp/sonoscdplayer-control.sock', dry_run=True, sender=send_command):
        if uid is not None and (not isinstance(uid, str) or not UID_PATTERN.fullmatch(uid)):
            raise ValueError('invalid Sonos UID')
        self.uid = uid
        self.socket_path = socket_path
        self.dry_run = dry_run
        self.sender = sender

    def event(self, name, state):
        command = map_event(name, state, self.uid)
        if command is None:
            return None
        if self.dry_run:
            return {'dry_run': True, 'command': command.name, 'ok': None}
        return self.sender(self.socket_path, command)
