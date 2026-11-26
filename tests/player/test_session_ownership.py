"""Previous streaming sessions must never authorize current Sonos writes."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sonoscdplayer.player.state import Mode
from sonoscdplayer.sonos import groups
from test_player_sonos_queue import FOREIGN, entry
from test_session_controller import make_controller, start, confirm


@pytest.fixture(params=[False, True], ids=['direct', 'queue'])
def restarted(make_controller, request):
    c, p, s = make_controller(request.param)
    start(c, p)
    old = c.owned_url
    c.stop()
    start(c, p)
    return c, p, s, old


@pytest.mark.parametrize('command', ['stop', 'pause', 'resume'])
def test_previous_session_cannot_authorize_transport(restarted, command):
    c, p, s, old = restarted
    if command == 'resume':
        c.pause()
    p.pause.reset_mock()
    p.play.reset_mock()
    p.stop = Mock(wraps=p.stop)
    current = c.owned_url
    mode = c.state.mode
    p.uri = old
    with pytest.raises(RuntimeError):
        getattr(c, command)()
    p.stop.assert_not_called()
    p.pause.assert_not_called()
    p.play.assert_not_called()
    assert c.owned_url == current and c.state.mode == mode
    assert s.alive()


def test_refresh_rejects_previous_session(restarted):
    c, p, s, old = restarted
    p.uri = old
    p.stop = Mock(wraps=p.stop)
    c.refresh()
    assert c.state.mode == Mode.ERROR
    assert c.owned_url is None and not s.alive()
    p.stop.assert_not_called()
    assert p.uri == old


def test_shutdown_refuses_previous_session(restarted):
    c, p, s, old = restarted
    p.uri = old
    p.stop = Mock(wraps=p.stop)
    result = c.shutdown_owned()
    assert result['action'] == 'not_stopped'
    p.stop.assert_not_called()
    assert p.uri == old


@pytest.fixture
def restarted_queue(make_controller):
    c, p, s = make_controller(True)
    start(c, p)
    old = list(c._queue_block['urls'])
    c.stop()
    start(c, p)
    return c, p, s, old


@pytest.mark.parametrize('direction,track', [('next', 3), ('previous', 1)])
def test_jump_refuses_previous_session_destination(restarted_queue, direction, track):
    c, p, s, old = restarted_queue
    c.change('next')
    confirm(c, p)
    current = c.owned_url
    index = c._queue_block['first'] - 1 + track - 1
    p.queue[index] = entry(old[track - 1])
    p.play_from_queue = Mock(wraps=p.play_from_queue)
    with pytest.raises(RuntimeError):
        c.change(direction)
    p.play_from_queue.assert_not_called()
    assert c.state.selected_track == 2 and c.owned_url == current
    assert c.state.mode == Mode.PLAYING and s.alive()


def test_cleanup_preserves_replaced_previous_session_entry(restarted_queue):
    c, p, s, old = restarted_queue
    index = c._queue_block['first']  # second track, zero-based
    p.queue[index] = entry(old[1])
    c.stop()
    assert p.uris() == [FOREIGN, old[1]]
    assert not s.alive() and c.owned_url is None


def test_orphaned_previous_block_stays_in_queue(make_controller):
    c, p, s = make_controller(True)
    start(c, p)
    old = list(c._queue_block['urls'])
    c.stop()
    p.queue.extend(entry(url) for url in old)
    start(c, p)
    current = list(c._queue_block['urls'])
    assert set(old).isdisjoint(current)
    assert p.uris() == [FOREIGN] + old + current
    c.stop()
    assert p.uris() == [FOREIGN] + old
    assert not s.alive()


def test_group_release_rejects_previous_session(restarted_queue, monkeypatch):
    c, p, s, old = restarted_queue
    member = SimpleNamespace(uid='MEMBER', unjoin=Mock())
    group = SimpleNamespace(coordinator=p, members=[p, member])
    p.group = member.group = group
    monkeypatch.setattr(groups, 'refresh_topology', lambda speaker: None)
    p.uri = old[0]
    result = groups.release(p, {member.uid: member}, owned_url=c.owned_url)
    assert result == {member.uid: 'kept: foreign playback'}
    member.unjoin.assert_not_called()
