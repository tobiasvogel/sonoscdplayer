"""Single-purpose JSON client for the existing private player Unix socket."""
from __future__ import annotations

import json
import socket
from pathlib import Path


MAX_REPLY = 65536


class PlayerUnavailable(RuntimeError):
    """The player did not return a complete response; operation may be unknown."""


class PlayerClient:
    """Send one bounded request; never retry side-effecting commands."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    def request(self, payload: dict, *, timeout: float = 35) -> dict:
        """Exchange one newline-delimited JSON object with the player."""
        if not isinstance(payload, dict):
            raise ValueError('invalid player payload')
        raw = (json.dumps(payload, separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > 1024:
            raise ValueError('player request too large')
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(3)
                conn.connect(self.path)
                conn.settimeout(timeout)
                conn.sendall(raw)
                data = bytearray()
                while b'\n' not in data:
                    part = conn.recv(4096)
                    if not part:
                        break
                    data.extend(part)
                    if len(data) > MAX_REPLY:
                        raise PlayerUnavailable('player reply too large')
        except (OSError, socket.timeout) as exc:
            raise PlayerUnavailable('player unavailable or response unknown') from exc
        if b'\n' not in data:
            raise PlayerUnavailable('incomplete player response; outcome unknown')
        try:
            result = json.loads(data.split(b'\n', 1)[0])
        except (UnicodeError, ValueError) as exc:
            raise PlayerUnavailable('invalid player reply') from exc
        if not isinstance(result, dict):
            raise PlayerUnavailable('invalid player reply')
        return result


# Expose ONLY deliberate, already-supported controls, never raw arbitrary RPC.
SIMPLE_COMMANDS = frozenset({
    'play-selected', 'pause', 'resume', 'stop', 'next', 'previous',
    'eject', 'reset-disc', 'volume +1', 'volume -1',
})


def command_payload(data: dict) -> dict:
    """Validate the web API subset of the player's command protocol."""
    if not isinstance(data, dict) or set(data) - {'command', 'uid', 'members'}:
        raise ValueError('invalid command fields')
    command = data.get('command')
    if not isinstance(command, str):
        raise ValueError('invalid command')
    if command in SIMPLE_COMMANDS:
        if set(data) != {'command'}:
            raise ValueError('unexpected command fields')
        return {'command': command}
    if command == 'set-target':
        uid = data.get('uid')
        members = data.get('members', [])
        import re
        valid_uid = lambda x: isinstance(x, str) and 1 <= len(x) <= 128 and bool(re.fullmatch(r'[A-Za-z0-9_:-]+', x))
        if not valid_uid(uid) or not isinstance(members, list) or len(members) > 32 or any(not valid_uid(x) for x in members):
            raise ValueError('invalid target')
        if set(data) - {'command', 'uid', 'members'}:
            raise ValueError('invalid target fields')
        return {'command': command, 'uid': uid, 'members': members}
    raise ValueError('unsupported command')


def metadata_edit_payload(data: dict) -> dict:
    """Bounded, single-field edit forwarded to the player with disc identity proof."""
    from sonoscdplayer.metadata.manual_edits import validate_edit
    if not isinstance(data, dict) or set(data) - {'token', 'field', 'track', 'value'}:
        raise ValueError('invalid metadata edit fields')
    token = data.get('token')
    if not isinstance(token, str) or len(token) != 32 or any(c not in '0123456789abcdef' for c in token):
        raise ValueError('invalid disc token')
    if 'value' not in data or 'field' not in data:
        raise ValueError('missing metadata value or field')
    field, track, value = validate_edit(data['field'], data.get('track'), data['value'])
    result = {'command': 'edit-metadata', 'token': token, 'field': field, 'value': value}
    if track is not None:
        result['track'] = track
    return result
