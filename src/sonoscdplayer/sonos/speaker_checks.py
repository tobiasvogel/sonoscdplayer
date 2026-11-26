"""Find a Sonos speaker by its UID and check it before and during playback.

The player only ever controls a speaker it has identified by UID, and only
touches a stream it started itself. These checks raise instead of guessing:
a speaker that is grouped differently, plays something else or has another
UID is left alone.
"""
from __future__ import annotations

from sonoscdplayer.sonos.groups import IDLE_TRANSPORTS, is_expected_group, visible_members
from sonoscdplayer.sonos.network_timing import measure_network


class TransportChanged(RuntimeError):
    """The speaker's transport state differs from the expected one.

    Usually someone pressed play or pause in the Sonos app between two reads.
    This is not a sign that someone else took over the speaker.
    """


def find_speaker(uid: str, *, discover=None):
    """Return the one speaker in the network with this UID.

    Raises:
        RuntimeError: SoCo is missing, or not exactly one speaker has this UID.
    """
    if discover is None:
        try:
            from soco import discover
        except ImportError as exc:
            raise RuntimeError('SoCo is not installed; start the player with its Python '
                               'environment') from exc
    speakers = measure_network('discovery', lambda: discover(timeout=5)) or set()
    matches = [speaker for speaker in speakers if speaker.uid == uid]
    if len(matches) != 1:
        raise RuntimeError(f'speaker {uid} not found exactly once ({len(matches)} matches)')
    return matches[0]


class UriChanged(RuntimeError):
    """The speaker plays another URI than ours; ``uri`` is what it plays."""

    def __init__(self, uri):
        super().__init__('ownership denied: exact URI mismatch')
        self.uri = uri


class SpeakerGrouped(RuntimeError):
    """The speaker is not alone in its group; ``speaker`` is the found speaker."""

    def __init__(self, speaker):
        super().__init__('speaker is not alone in its group')
        self.speaker = speaker


def require_solo_and_stopped(speaker, uid: str) -> None:
    """Check that the speaker is alone in its group and idle (stopped or paused).

    Called before the player starts a new stream on a single speaker.

    Raises:
        SpeakerGrouped: the speaker is grouped with others.
        RuntimeError: wrong UID, or the speaker is playing.
    """
    if speaker.uid != uid:
        raise RuntimeError('speaker UID does not match')
    group = speaker.group
    members = visible_members(group)
    if len(members) != 1 or members[0].uid != uid or group.coordinator.uid != uid:
        raise SpeakerGrouped(speaker)
    transport = speaker.get_current_transport_info()['current_transport_state']
    if transport not in IDLE_TRANSPORTS:
        raise RuntimeError(f'speaker is not stopped ({transport})')


def find_solo_stopped_speaker(uid: str, *, discover=None):
    """find_speaker() followed by require_solo_and_stopped()."""
    speaker = find_speaker(uid, discover=discover)
    require_solo_and_stopped(speaker, uid)
    return speaker


def require_our_stream(speaker, uid: str, url: str, expected_transport: str,
                       members=frozenset()) -> None:
    """Check that the speaker still plays the stream the player started.

    Args:
        speaker: the group coordinator the player started the stream on.
        uid: its UID.
        url: the exact stream URL the player started.
        expected_transport: 'PLAYING' or 'PAUSED_PLAYBACK'.
        members: UIDs of the other speakers the player joined to the group.

    Raises:
        TransportChanged: everything matches except the transport state.
        UriChanged: the speaker plays another URI.
        RuntimeError: another UID or another group.
    """
    if speaker.uid != uid:
        raise RuntimeError('ownership denied: UID changed')
    if not is_expected_group(speaker, uid, members):
        raise RuntimeError('ownership denied: unexpected topology')
    transport = speaker.get_current_transport_info()['current_transport_state']
    if transport != expected_transport:
        raise TransportChanged(
            f'ownership denied: transport {transport}, expected {expected_transport}')
    uri = speaker.get_current_track_info().get('uri')
    if uri != url:
        raise UriChanged(uri)
