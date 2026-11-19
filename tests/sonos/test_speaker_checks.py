"""Finding a speaker by UID and checking it before and during playback."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sonoscdplayer.sonos.speaker_checks import (TransportChanged, find_solo_stopped_speaker,
                                                find_speaker, require_our_stream,
                                                require_solo_and_stopped)

UID = 'TEST_TARGET_UID'
OUR_URL = 'http://192.0.2.12:8765/track/2.wav'
OTHER_URL = 'http://192.0.2.12:8765/track/3.wav'


def speaker(uid=UID, transport='STOPPED', uri=OUR_URL, alone=True, coordinator=True):
    """A simulated Sonos speaker; by default alone in its group."""
    s = Mock()
    s.uid = uid
    s.player_name = 'Test Speaker'
    s.group = SimpleNamespace(members=[s] if alone else [s, Mock(uid='OTHER')],
                              coordinator=s if coordinator else Mock(uid='OTHER'))
    s.get_current_transport_info.return_value = {'current_transport_state': transport}
    s.get_current_track_info.return_value = {'uri': uri}
    return s


# ---- finding ---------------------------------------------------------------

def test_find_speaker_returns_the_speaker_with_the_uid():
    target = speaker()
    assert find_speaker(UID, discover=lambda timeout: {speaker(uid='A'), target}) is target


@pytest.mark.parametrize('found', [set(), None])
def test_find_speaker_without_match_raises(found):
    with pytest.raises(RuntimeError, match='not found exactly once'):
        find_speaker(UID, discover=lambda timeout: found)


def test_find_solo_stopped_speaker_checks_the_found_speaker():
    with pytest.raises(RuntimeError, match='not stopped'):
        find_solo_stopped_speaker(UID, discover=lambda timeout: {speaker(transport='PLAYING')})


# ---- before starting a stream ------------------------------------------------

def test_a_solo_stopped_speaker_passes():
    require_solo_and_stopped(speaker(), UID)


@pytest.mark.parametrize('candidate, reason', [
    (speaker(uid='WRONG'), 'UID'),
    (speaker(alone=False), 'not alone'),
    (speaker(coordinator=False), 'not alone'),
    (speaker(transport='PLAYING'), 'not stopped'),
])
def test_other_speakers_are_refused(candidate, reason):
    with pytest.raises(RuntimeError, match=reason):
        require_solo_and_stopped(candidate, UID)


# ---- while our stream plays ------------------------------------------------

def test_our_playing_stream_passes():
    require_our_stream(speaker(transport='PLAYING'), UID, OUR_URL, 'PLAYING')


def test_joined_members_are_expected():
    s = speaker(transport='PLAYING')
    member = Mock(uid='MEMBER')
    s.group.members.append(member)
    require_our_stream(s, UID, OUR_URL, 'PLAYING', frozenset({'MEMBER'}))


@pytest.mark.parametrize('uri', ['', OTHER_URL, 'x-rincon:other', None])
def test_another_stream_is_not_ours(uri):
    with pytest.raises(RuntimeError, match='URI'):
        require_our_stream(speaker(transport='PLAYING', uri=uri), UID, OUR_URL, 'PLAYING')


@pytest.mark.parametrize('transport', ['STOPPED', 'TRANSITIONING', 'PAUSED_PLAYBACK'])
def test_another_transport_state_is_reported_separately(transport):
    # Pausing in the Sonos app is not a takeover: a distinct exception.
    with pytest.raises(TransportChanged):
        require_our_stream(speaker(transport=transport), UID, OUR_URL, 'PLAYING')


@pytest.mark.parametrize('candidate', [speaker(transport='PLAYING', alone=False),
                                       speaker(transport='PLAYING', coordinator=False)])
def test_another_group_is_not_ours(candidate):
    with pytest.raises(RuntimeError, match='topology') as error:
        require_our_stream(candidate, UID, OUR_URL, 'PLAYING')
    assert not isinstance(error.value, TransportChanged)


def test_another_uid_is_not_ours():
    with pytest.raises(RuntimeError, match='UID'):
        require_our_stream(speaker(transport='PLAYING'), 'WRONG', OUR_URL, 'PLAYING')
