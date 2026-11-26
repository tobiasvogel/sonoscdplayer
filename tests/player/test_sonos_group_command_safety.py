"""Keep group discovery resilient without authorizing unverified writes."""
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import Controller, SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode, PlayerState
from test_player_controller import FakePlayer, FakeServer, Group
from test_sonos_group_read_failures import (
    Member, _grouped_controller, _move_playback, _read_failure,
)


class OtherMember(FakePlayer):
    uid = 'TEST_OTHER_MEMBER'


def _three_speakers():
    controller, leader, member, server = _grouped_controller()
    other = OtherMember()
    leader.group.members.append(other)
    other.group = leader.group
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller._joined == {member.uid: member, other.uid: other}
    return controller, leader, member, other, server


def _assert_no_writes(speakers, server, url, track=1):
    assert all(speaker.stops == 0 for speaker in speakers)
    assert speakers[0].plays == [url]
    assert all(not speaker.plays for speaker in speakers[1:])
    assert server.starts == [track]


@pytest.mark.parametrize('method', ['group', 'get_current_transport_info',
                                    'get_current_track_info'])
def test_unreachable_candidate_does_not_hide_reachable_coordinator(method):
    controller, leader, member, other, server = _three_speakers()
    url = controller.owned_url
    # The first former member is standalone and the second now plays our CD.
    # Candidate order follows the observed group membership.
    assert list(controller._joined) == [member.uid, other.uid]
    member.group = Group(member)
    _move_playback(leader, other)
    with _read_failure(member, method):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.player is other and controller.uid == other.uid
    assert controller._sonos_read_failure_since is None
    assert server.alive() and controller.owned_url == url
    assert not controller._joined
    _assert_no_writes((leader, member, other), server, url)


def test_all_replacement_candidates_unreachable_use_bounded_grace():
    controller, leader, member, other, server = _three_speakers()
    url = controller.owned_url
    member.group = Group(member)
    _move_playback(leader, other)
    with (_read_failure(member, 'get_current_transport_info'),
          _read_failure(other, 'get_current_transport_info'),
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        assert controller._sonos_read_failure_since == 100.0
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING
        assert server.alive() and controller.owned_url == url
        assert controller.player is leader
        assert controller._joined == {member.uid: member, other.uid: other}
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive() and controller.owned_url is None
    assert controller._group_uncertain
    assert controller._joined == {member.uid: member, other.uid: other}
    _assert_no_writes((leader, member, other), server, url)


def _playing_middle_track():
    leader = FakePlayer()
    server = FakeServer()
    controller = Controller(
        PlayerState().disc_loaded((1, 2, 3)).select(2),
        leader.uid, '192.0.2.10', 8765, server,
        verify=lambda uid: leader,
    )
    controller.use_queue = False
    controller.play()
    leader.transport = 'PLAYING'
    controller.refresh()
    member = Member()
    leader.group.members.append(member)
    member.group = leader.group
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller._joined == {member.uid: member}
    return controller, leader, member, server


@pytest.mark.parametrize('command', ['stop', 'pause', 'next', 'previous'])
def test_command_requires_live_ownership_during_group_grace(command):
    controller, leader, member, server = _playing_middle_track()
    url = controller.owned_url
    _move_playback(leader, member)
    with _read_failure(member, 'get_current_transport_info'):
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING
        assert controller._sonos_read_failure_since is not None
        before = controller.state
        # Include methods absent from the minimal fake to detect attempted writes.
        with ExitStack() as stack:
            writes = [stack.enter_context(patch.object(speaker, method, create=True))
                      for speaker in (leader, member)
                      for method in ('stop', 'pause', 'play', 'play_uri',
                                     'play_from_queue', 'join', 'unjoin')]
            with pytest.raises(ReadTimeout):
                if command in ('next', 'previous'):
                    controller.change(command)
                else:
                    getattr(controller, command)()
            for write in writes:
                write.assert_not_called()
        assert controller.state == before
        assert controller.player is leader
        assert controller._joined == {member.uid: member}
        assert server.alive() and controller.owned_url == url
        _assert_no_writes((leader, member), server, url, track=2)

    # A successful fresh observation must restore normal control.
    controller.refresh()
    assert controller.player is member and controller.uid == member.uid
    assert controller._sonos_read_failure_since is None
    assert controller.state.mode == Mode.PLAYING
    controller.stop()
    assert controller.state.mode == Mode.STOPPED
    assert not server.alive()
    assert leader.stops == 0 and member.stops == 1
    assert leader.plays == [url] and not member.plays
    assert server.starts == [2]
