"""Read-only projection of player_service status protocol into display state."""
import json
import socket
from dataclasses import replace
from .model import DisplayModel

MAX_RESPONSE = 65536


def read_status(path='/run/sonoscdplayer/control.sock', timeout=0.35):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(timeout)
        conn.connect(path)
        conn.sendall(b'{"command":"status"}\n')
        buf = bytearray()
        while b'\n' not in buf:
            block = conn.recv(4096)
            if not block:
                raise ValueError('incomplete status reply')
            buf.extend(block)
            if len(buf) > MAX_RESPONSE:
                raise ValueError('status reply too long')
    result = json.loads(buf.split(b'\n', 1)[0])
    if result.get('ok') is not True or not isinstance(result.get('state'), dict):
        raise ValueError('invalid status reply')
    return result['state']


def project_status(state, previous=None):
    """Only fields currently supported by player_service; never invent metadata.

    Disc identity is not exposed by schema v1: transient metadata is NOT carried
    over from a previous projection, preventing stale cover/title display.
    """
    if not isinstance(state, dict) or state.get('schema_version') != 1:
        raise ValueError('unsupported player status schema')
    player = state.get('player')
    if not isinstance(player, dict):
        raise ValueError('player status missing')
    mode = player.get('mode')
    if mode not in ('STOPPED', 'PLAYING', 'PAUSED', 'STARTING', 'ERROR'):
        raise ValueError('unknown player mode')
    count = player.get('track_count')
    track = player.get('track')
    if type(count) is not int or count < 0 or type(track) is not int or track < 0:
        raise ValueError('invalid track information')
    playback = {'PLAYING':'playing', 'PAUSED':'paused'}.get(mode, 'stopped')
    return DisplayModel(
        disc_present=count > 0,
        album=None, artist=None, title=None, cover=None,
        track=track, track_count=count, playback=playback,
        speaker=None, elapsed_seconds=None, duration_seconds=None,
        disc_phase=mode if mode in ('STARTING','ERROR') else None,
    )
