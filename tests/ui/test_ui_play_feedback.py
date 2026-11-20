"""Display feedback after PLAY/PAUSE: starting, refused, or not even sent."""
import pytest

from sonoscdplayer.hardware.inputs.play_button_execute import SelectedTargetExecutor
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.menu.play_feedback import rejection_notice
from sonoscdplayer.ui.menu.ui_event_bridge import encode_event


def status(mode='STOPPED', rejection=None):
    state = {'player': {'mode': mode}}
    if rejection is not None:
        state['last_rejection'] = rejection
    return state


@pytest.fixture
def menu():
    m = InteractiveMenu(discover=lambda **_: None)
    m.player_status(status(), now=0)
    return m


def shown(menu, now):
    current = menu.connect_notices.current(now=now)
    return current.key if current is not None else None


def test_press_shows_starting_until_the_player_starts(menu):
    assert menu.event('play', 'requested', now=1)
    assert shown(menu, 2) == 'play.requested'
    assert menu.player_status(status('STARTING'), now=3)
    assert shown(menu, 3) is None


def test_refusal_replaces_starting_with_the_reason(menu):
    menu.event('play', 'requested', now=1)
    reason = 'RuntimeError: PLAY failed: SpeakerGrouped: speaker is not alone in its group'
    assert menu.player_status(status(rejection={'seq': 1, 'reason': reason}), now=4)
    assert shown(menu, 5) == 'play.rejected_grouped'
    assert not menu.player_status(status(rejection={'seq': 1, 'reason': reason}), now=6)


def test_old_refusal_from_before_the_display_started_is_not_shown():
    m = InteractiveMenu(discover=lambda **_: None)
    assert not m.player_status(status(rejection={'seq': 7, 'reason': 'x'}), now=0)
    assert shown(m, 1) is None


def test_pause_press_needs_no_notice(menu):
    menu.player_status(status('PLAYING'), now=0)
    assert not menu.event('play', 'requested', now=1)


@pytest.mark.parametrize('event, key', [('no_target', 'play.no_target'),
                                        ('blocked', 'play.blocked')])
def test_button_service_reasons(menu, event, key):
    assert encode_event('play', event) is not None
    menu.event('play', event, now=1)
    assert shown(menu, 2) == key


@pytest.mark.parametrize('reason, key', [
    ('ValueError: no confirmed target', 'play.no_target'),
    ('RuntimeError: speaker is not stopped (PLAYING)', 'play.rejected_busy'),
    ('RuntimeError: member discovery incomplete', 'play.rejected_not_found'),
    ('disc unavailable, unreadable or unverified; inspect status before PLAY', 'play.rejected_disc'),
    ('drive wake unverified; PLAY blocked', 'play.rejected_drive'),
    ('ValueError: PLAY requires STOPPED', 'play.blocked'),
])
def test_reasons_map_to_notices(reason, key):
    assert rejection_notice(reason) == key


def run_executor(target_selected, *, command_ok=True):
    events, sent = [], []
    status_reply = {'ok': True, 'state': {'schema_version': 1,
                    'player': {'mode': 'STOPPED'}, 'sonos': {'owned': False,
                    'group_cleanup_pending': False}, 'stream': {'active': False}}}
    target_reply = {'ok': True, 'target_selection': {'selected': target_selected}}
    reader = lambda path, what: status_reply if what == 'status' else target_reply
    executor = SelectedTargetExecutor('/x', reader=reader, report=lambda *_: None,
                                      sender=lambda path, cmd, uid: sent.append(cmd),
                                      notify=events.append)
    executor.submit()
    import time
    deadline = time.monotonic() + 2
    while executor._busy and time.monotonic() < deadline:
        time.sleep(0.01)
    return events, sent


def test_executor_reports_the_press_and_a_missing_selection():
    events, sent = run_executor(False)
    assert events == ['requested', 'no_target'] and sent == []


def test_executor_sends_play_when_a_selection_exists():
    events, sent = run_executor(True)
    assert events == ['requested'] and sent == ['play-selected']


def test_player_reports_refusals_without_uids():
    from sonoscdplayer.player.controller import Controller
    from sonoscdplayer.player.state import PlayerState

    class Server:
        def alive(self): return False
        def reader_active(self): return False
    ctrl = Controller(PlayerState().disc_loaded((1,)), None, '192.0.2.10', 8765, Server())
    ctrl.note_rejection('play-selected', 'RuntimeError: speaker RINCON_AB12 not found')
    ctrl.note_rejection('play-selected', 'ValueError: no confirmed target')
    second = ctrl.status_snapshot()['last_rejection']
    assert second == {'seq': 2, 'command': 'play-selected',
                      'reason': 'ValueError: no confirmed target'}
    ctrl.last_rejection = None
    ctrl.note_rejection('play', 'RuntimeError: speaker RINCON_AB12 not found')
    assert ctrl.status_snapshot()['last_rejection']['reason'] == \
        'RuntimeError: speaker <speaker> not found'
