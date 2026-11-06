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
    if mode not in ('NO_DISC', 'STOPPED', 'PLAYING', 'PAUSED', 'STARTING', 'ERROR'):
        raise ValueError('unknown player mode')
    count = player.get('track_count')
    track = player.get('track')
    # PlayerState.NO_DISC legitimately exposes selected_track=None and
    # track_count=0. Preserve that state without inventing a disc.
    if type(count) is not int or count < 0:
        raise ValueError('invalid track information')
    if mode == 'NO_DISC':
        if count != 0 or track is not None:
            raise ValueError('NO_DISC must not contain selected tracks')
        track = 0  # DisplayModel requires an integer, but disc_present=False.
    elif mode == 'ERROR' and count == 0 and track is None:
        # ERROR is also legal before a disc has been loaded.
        track = 0
    elif type(track) is not int or track < 0 or (count == 0 and track != 0):
        raise ValueError('invalid track information')
    playback = {'PLAYING':'playing', 'PAUSED':'paused'}.get(mode, 'stopped')
    elapsed = duration = None
    # Optional data from the player's last verified Sonos timing read.
    # Never accept progress from inactive states or from unowned playback.
    if playback in ('playing', 'paused') and (state.get('sonos') or {}).get('owned') is True:
        progress = state.get('progress')
        if isinstance(progress, dict):
            e, d = progress.get('elapsed_seconds'), progress.get('duration_seconds')
            if type(e) is int and 0 <= e <= 359999:
                if d is None or (type(d) is int and 0 < d <= 359999 and e <= d):
                    elapsed, duration = e, d
    return DisplayModel(
        view='playback' if playback in ('playing', 'paused') else 'idle',
        disc_present=count > 0,
        album=None, artist=None, title=None, cover=None,
        track=track, track_count=count, playback=playback,
        speaker=None, elapsed_seconds=elapsed, duration_seconds=duration,
        disc_phase=mode,  # state label only, never authorizes transport actions
    )
