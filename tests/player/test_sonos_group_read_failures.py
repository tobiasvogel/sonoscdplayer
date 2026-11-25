"""Network failures while observing a Sonos group or coordinator change."""
from contextlib import contextmanager
from unittest.mock import PropertyMock, patch

import pytest
from requests.exceptions import ReadTimeout

from sonoscdplayer.player.controller import SONOS_READ_GRACE_SECONDS
from sonoscdplayer.player.state import Mode
from test_aud05a_sonos_transient_read_characterization import _playing_controller
from test_player_controller import FakePlayer, Group


class Member(FakePlayer):
    uid = 'TEST_MEMBER'


def _grouped_controller():
    controller, leader, server = _playing_controller()
    member = Member()
    leader.group.members.append(member)
    member.group = leader.group
    controller.refresh()  # Observe a member added in the Sonos app.
    assert controller.state.mode == Mode.PLAYING
    assert controller._joined == {member.uid: member}
    return controller, leader, member, server


def _move_playback(leader, member):
    leader.group = Group(leader)
    member.group = Group(member)
    leader.transport = 'STOPPED'
    member.transport = 'PLAYING'
    member.uri = leader.uri


@contextmanager
def _read_failure(speaker, method):
    error = ReadTimeout('temporary group read failure')
    if method == 'group':
        with patch.object(type(speaker), 'group', create=True,
                          new_callable=PropertyMock, side_effect=error):
            yield
    else:
        with patch.object(speaker, method, side_effect=error):
            yield


def _assert_no_writes(leader, member, server, url):
    assert leader.stops == member.stops == 0
    assert leader.plays == [url]
    assert member.plays == []
    assert server.starts == [1]


@pytest.mark.parametrize('method', ['group', 'get_current_transport_info',
                                    'get_current_track_info'])
def test_coordinator_change_with_read_failure_preserves_stream_then_recovers(method):
    controller, leader, member, server = _grouped_controller()
    url = controller.owned_url
    _move_playback(leader, member)
    with _read_failure(member, method):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert server.alive() and controller.owned_url == url
    assert controller.player is leader
    assert controller._joined == {member.uid: member}
    assert controller._sonos_read_failure_since is not None
    _assert_no_writes(leader, member, server, url)

    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.player is member and controller.uid == member.uid
    assert controller._sonos_read_failure_since is None
    assert server.alive() and controller.owned_url == url
    _assert_no_writes(leader, member, server, url)


def test_sustained_coordinator_read_failure_expires_without_sonos_stop():
    controller, leader, member, server = _grouped_controller()
    url = controller.owned_url
    _move_playback(leader, member)
    with (_read_failure(member, 'get_current_transport_info'),
          patch('sonoscdplayer.player.controller.time.monotonic') as clock):
        clock.return_value = 100.0
        controller.refresh()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS - 0.1
        controller.refresh()
        assert controller.state.mode == Mode.PLAYING and server.alive()
        clock.return_value = 100.0 + SONOS_READ_GRACE_SECONDS
        controller.refresh()
    assert controller.state.mode == Mode.ERROR
    assert not server.alive() and controller.owned_url is None
    assert controller._joined == {member.uid: member}
    assert controller._group_uncertain
    _assert_no_writes(leader, member, server, url)


def test_coordinator_change_without_read_failure_is_followed():
    controller, leader, member, server = _grouped_controller()
    url = controller.owned_url
    _move_playback(leader, member)
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING
    assert controller.player is member and controller.uid == member.uid
    assert server.alive() and controller.owned_url == url
    _assert_no_writes(leader, member, server, url)


def test_current_group_topology_timeout_recovers():
    controller, leader, member, server = _grouped_controller()
    url = controller.owned_url
    with _read_failure(leader, 'group'):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING and server.alive()
    assert controller._sonos_read_failure_since is not None
    controller.refresh()
    assert controller.state.mode == Mode.PLAYING and server.alive()
    assert controller._joined == {member.uid: member}
    assert controller._sonos_read_failure_since is None
    _assert_no_writes(leader, member, server, url)


def test_foreign_uri_after_group_topology_timeout_still_fails_closed():
    controller, leader, member, server = _grouped_controller()
    url = controller.owned_url
    with _read_failure(leader, 'group'):
        controller.refresh()
    assert controller.state.mode == Mode.PLAYING and server.alive()
    leader.uri = 'http://external.example/foreign'
    controller.refresh()
    assert controller.state.mode == Mode.ERROR and not server.alive()
    assert controller._group_uncertain
    _assert_no_writes(leader, member, server, url)
