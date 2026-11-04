"""Pure M0.18 policy for the physical PLAY/PAUSE button.

This module has no GPIO, socket, or Sonos access. It only authorizes a
candidate command from fresh player snapshots; the player remains the
authority and repeats its live Sonos ownership checks.
"""


def plan_playpause(status, target):
    """Return one candidate command, or None (fail closed).

    status is the player's cached status RPC response.
    target is a separate get-target response. No UID is read or
    included in the command. Callers must not issue commands when either
    read failed, timed out, or returned malformed/incomplete information.

    A planned command is NOT an authorization to replay on timeout.
    """
    if not isinstance(status, dict) or status.get('ok') is not True:
        return None
    if not isinstance(target, dict) or target.get('ok') is not True:
        return None
    state = status.get('state')
    selection = target.get('target_selection')
    if not isinstance(state, dict) or not isinstance(selection, dict):
        return None
    player, sonos, stream = (state.get('player'), state.get('sonos'),
                             state.get('stream'))
    if not all(isinstance(part, dict) for part in (player, sonos, stream)):
        return None
    if state.get('schema_version') != 1:
        return None
    if sonos.get('group_cleanup_pending') is not False:
        return None

    mode = player.get('mode')
    owned = sonos.get('owned')
    active = stream.get('active')
    if type(owned) is not bool or type(active) is not bool:
        return None

    if mode == 'STOPPED':
        # Selection is only intent, not ownership. An explicit user press
        # may start it, but only when a fresh player ACK exists.
        if owned is False and active is False and selection.get('selected') is True:
            return 'play-selected'
    elif mode == 'PLAYING':
        if owned is True and active is True:
            return 'pause'
    elif mode == 'PAUSED':
        if owned is True and active is True:
            return 'resume'
    return None


def plan_stop(status, target):
    """Authorize STOP only for an acknowledged, owned active playback.

    A selected target alone never authorizes STOP. The player performs its
    independent live URI, topology, transport, and ownership checks.
    """
    if not isinstance(status, dict) or status.get('ok') is not True:
        return None
    if not isinstance(target, dict) or target.get('ok') is not True:
        return None
    state = status.get('state')
    selection = target.get('target_selection')
    if not isinstance(state, dict) or not isinstance(selection, dict):
        return None
    if state.get('schema_version') != 1:
        return None
    player = state.get('player')
    sonos = state.get('sonos')
    stream = state.get('stream')
    if not all(isinstance(part, dict) for part in (player, sonos, stream)):
        return None
    if sonos.get('group_cleanup_pending') is not False:
        return None
    if selection.get('selected') is not True:
        return None
    if player.get('mode') not in ('PLAYING', 'PAUSED'):
        return None
    if sonos.get('owned') is not True or stream.get('active') is not True:
        return None
    return 'stop'
