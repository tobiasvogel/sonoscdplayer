"""Explicit, opt-in boundary from one selected speaker to the existing player RPC.

No GPIO reads, no Sonos API imports, no automatic command on selection.
A caller must deliberately invoke a transport action. Player-side live
verification and ownership checks remain authoritative.
"""
from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass

from .single_target import TargetIntent, TargetSelectionError, select_one

_ALLOWED = frozenset(('playpause',))
# Other transport RPCs lack a target UID in the existing player protocol.
# Do not permit them until the player can bind them to the selected owner.
_UID_PATTERN = re.compile(r'[A-Za-z0-9_:-]{1,128}\Z')
DEFAULT_SOCKET = '/run/sonoscdplayer/control.sock'


class TransportRejected(RuntimeError):
    """An unconfirmed target or rejected player request; never retried."""


def request_for(action: str, target: TargetIntent) -> dict:
    """Validate a transport *request*, without dispatching or mutating state."""
    if action not in _ALLOWED:
        raise ValueError('unsupported transport action')
    if not isinstance(target, TargetIntent) or not _UID_PATTERN.fullmatch(target.uid):
        raise TransportRejected('missing or invalid single target')
    return {'command':action, 'uid':target.uid}


def send_player_request(request: dict, socket_path=DEFAULT_SOCKET):
    """One bounded attempt to the established private player socket.

    No retries: after a timeout the command may already have been accepted.
    The player service is the sole authority for ownership and group safety.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(str(socket_path))
        client.sendall((json.dumps(request, separators=(',', ':')) + '\n').encode('utf-8'))
        received = bytearray()
        while b'\n' not in received and len(received) <= 16384:
            block = client.recv(4096)
            if not block:
                break
            received.extend(block)
    if len(received) > 16384 or b'\n' not in received:
        raise TransportRejected('invalid player response')
    try:
        reply = json.loads(received.split(b'\n',1)[0])
    except (ValueError, UnicodeError) as exc:
        raise TransportRejected('invalid player response') from exc
    if not isinstance(reply, dict) or reply.get('ok') is not True:
        raise TransportRejected('player rejected transport request')
    return True


@dataclass
class ExplicitTransport:
    """Process-local command boundary, disabled unless explicitly opted in.

    Confirmation stores intent only. All commands require a separate button
    action. On playpause, recheck *freshly discovered* topology and identity;
    the player separately rechecks live transport and ownership. No replay.
    """
    sender: object = send_player_request
    discover: object = None
    enabled: bool = False
    target: TargetIntent | None = None

    def confirm(self, state):
        intent = select_one(state)
        self.target = intent
        return intent.display_name

    def clear(self):
        self.target = None

    def dispatch(self, action):
        if not self.enabled:
            raise TransportRejected('transport disabled')
        if self.target is None:
            raise TransportRejected('no confirmed speaker')
        if action not in _ALLOWED:
            raise ValueError('unsupported transport action')
        if self.discover is None:
            raise TransportRejected('fresh topology check unavailable')
        current = self.discover(timeout=3.0)
        keys = [t for t in current.targets if t.key == self.target.uid]
        if current.error or len(keys) != 1 or keys[0].members != (self.target.uid,):
            self.clear()
            raise TransportRejected('target changed or is grouped')
        return self.sender(request_for(action, self.target))
