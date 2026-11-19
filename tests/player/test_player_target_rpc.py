"""Target selection and play-selected RPCs on the control socket (local-only, fail-closed)."""
import unittest
from dataclasses import replace
from types import SimpleNamespace, SimpleNamespace as NS
from sonoscdplayer.player.service import handle_command
from sonoscdplayer.sonos.target_selection import SelectedTarget, verify_solo_stopped
from sonoscdplayer.player.state import Mode
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.menu.speakers.multi_target import from_devices



class FakeController:
    def __init__(self, mode=Mode.STOPPED):
        self.state = SimpleNamespace(mode=mode)
        self.owned_url = None
        self.calls = []

    def status(self):
        return 'stopped'

    def status_snapshot(self):
        return {'schema_version':1,'player':{'mode':self.state.mode.value}}

    def command(self, command):
        self.calls.append(command)


class TargetSelectionRPCTests(unittest.TestCase):
    def setUp(self):
        self.controller = FakeController()
        self.checked = []
        self.selection = SelectedTarget(verify=lambda uid:self.checked.append(uid))

    def request(self, command, **kwargs):
        return handle_command(self.controller, {'command':command,**kwargs}, self.selection)

    def test_set_target_checks_and_does_not_play(self):
        reply=self.request('set-target',uid='RINCON_123')
        self.assertTrue(reply['ok'])
        self.assertTrue(reply['target_selection']['selected'])
        self.assertNotIn('RINCON_123',str(reply))
        self.assertEqual(self.checked,['RINCON_123'])
        self.assertEqual(self.controller.calls,[])

    def test_only_get_target_reports_the_selected_speakers(self):
        reply=self.request('set-target',uid='RINCON_123')
        self.assertNotIn('RINCON_123',str(reply))
        reply=self.request('get-target')
        self.assertEqual(reply['target_selection'],
                         {'selected':True,'ready_for_playback':False,'speakers':['RINCON_123']})
        self.assertNotIn('RINCON_123',str(self.request('status')))
        self.request('clear-target')
        self.assertEqual(self.request('get-target')['target_selection']['speakers'],[])

    def test_clear_target_no_audio_operation(self):
        self.request('set-target',uid='RINCON_123')
        reply=self.request('clear-target')
        self.assertTrue(reply['ok'])
        self.assertFalse(reply['target_selection']['selected'])
        self.assertEqual(self.controller.calls,[])

    def test_rejected_verification_does_not_change_selection(self):
        self.request('set-target',uid='RINCON_123')
        self.selection._verify=lambda uid: (_ for _ in ()).throw(RuntimeError('grouped'))
        reply=self.request('set-target',uid='RINCON_234')
        self.assertFalse(reply['ok'])
        self.assertEqual(self.selection.uid_for_explicit_action(),'RINCON_123')

    def test_active_session_refuses_selection_and_clear(self):
        self.request('set-target',uid='RINCON_123')
        self.controller.state.mode=Mode.PLAYING
        self.assertFalse(self.request('clear-target')['ok'])
        self.assertFalse(self.request('set-target',uid='RINCON_234')['ok'])
        self.assertEqual(self.selection.uid_for_explicit_action(),'RINCON_123')

    def test_uid_not_allowed_in_get_or_clear(self):
        self.assertFalse(self.request('get-target',uid='RINCON_123')['ok'])
        self.assertFalse(self.request('clear-target',uid='RINCON_123')['ok'])

    def test_no_implicit_playpause_target(self):
        self.request('set-target',uid='RINCON_123')
        self.assertFalse(self.request('playpause')['ok'])
        self.assertEqual(self.controller.calls,[])

    def test_bad_uid_rejected(self):
        self.assertFalse(self.request('set-target',uid='bad uid')['ok'])
        self.assertFalse(self.selection.summary()['selected'])

    def test_solo_live_preflight_checks_stopped(self):
        from unittest.mock import patch
        player=SimpleNamespace(uid='RINCON_123')
        player.group=SimpleNamespace(coordinator=player,members=(player,))
        player.get_current_transport_info=lambda: {'current_transport_state':'STOPPED'}
        with patch('sonoscdplayer.sonos.playback_start.find_target',return_value=player):
            self.assertTrue(verify_solo_stopped('RINCON_123'))
            player.group.members=(player,SimpleNamespace(uid='other'))
            with self.assertRaises(RuntimeError):
                verify_solo_stopped('RINCON_123')


def accepted_notice(menu, now):
    current = menu.connect_notices.current(now=now)
    return (current.key, current.severity) if current is not None else None


class Controller:
    def __init__(self):
        self.state = NS(mode=Mode.STOPPED)
        self.owned_url = None
        self.calls = []
    def status(self): return 'idle'
    def status_snapshot(self): return {'schema_version': 1}
    def play(self, uid, members=None):
        self.calls.append((uid, tuple(members or ())))


class GroupPlaySelectedTests(unittest.TestCase):
    def test_group_selection_is_not_playback(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True, find_many=lambda ids: {
            'B': NS(uid='B', group=NS(coordinator=NS(uid='B'),members=[NS(uid='B')]),
                    get_current_transport_info=lambda:{'current_transport_state':'STOPPED'})})
        reply = handle_command(c, {'command':'set-target','uid':'A','members':['B']},s)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(c.calls,[])
        self.assertEqual(s.members_for_explicit_action(),('B',))

    def test_only_explicit_play_selected_uses_saved_members(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True)
        s.set(c,'A')
        self.assertEqual(c.calls,[])
        self.assertTrue(handle_command(c, {'command':'play-selected'},s)['ok'])
        self.assertEqual(c.calls,[('A',())])

    def test_group_play_selected_passes_members_to_controller(self):
        c = Controller()
        s = SelectedTarget(verify=lambda uid: True)
        s._uid = 'A'
        s._members = ('B','C')
        reply = handle_command(c, {'command':'play-selected'},s)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(c.calls,[('A',('B','C'))])

    def test_play_selected_requires_prior_selection(self):
        c=Controller()
        s=SelectedTarget(verify=lambda uid: True)
        self.assertFalse(handle_command(c,{'command':'play-selected'},s)['ok'])
        self.assertEqual(c.calls,[])

    def test_play_selected_rejects_uid_in_request(self):
        c=Controller()
        s=SelectedTarget(verify=lambda uid: True)
        self.assertFalse(handle_command(c,{'command':'play-selected','uid':'A'},s)['ok'])
        self.assertEqual(c.calls,[])

    def test_normal_play_cannot_supply_members(self):
        c=Controller()
        self.assertFalse(handle_command(c,{'command':'play','uid':'A','members':['B']})['ok'])
        self.assertEqual(c.calls,[])

    def test_oled_group_confirm_sends_only_selection_intent(self):
        a=NS(uid='A',player_name='Alpha')
        b=NS(uid='B',player_name='Beta')
        a.group=NS(coordinator=a,members=(a,))
        b.group=NS(coordinator=b,members=(b,))
        topology=from_devices((a,b))
        seen=[]
        menu=InteractiveMenu(discover=lambda **kw:topology, target_rpc=lambda uid:True,
                             group_target_rpc=lambda uid,members:seen.append((uid,members)) or True)
        menu.active=True
        menu.state=menu.state.update(topology)
        menu.state=replace(menu.state,draft=frozenset(('A','B')),applied=frozenset(('A','B')))
        menu._send_target()
        menu._target_worker.join(timeout=2)
        menu.poll(now=0)
        self.assertEqual(seen,[('A',('B',))])
        self.assertEqual(accepted_notice(menu,0),('status.target_accepted','success'))



class RememberedTargetTests(unittest.TestCase):
    """the selection survives a restart but never acts on its own."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from sonoscdplayer.sonos.target_store import TargetStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name) / 'state'
        self.logs = []
        self.store = TargetStore(self.state_dir, log=self.logs.append)
        self.controller = FakeController()

    def selection(self, verify=lambda uid: None):
        return SelectedTarget(verify=verify, find_many=lambda uids: {uid: NS(uid=uid) for uid in uids},
                              store=self.store)

    def request(self, selection, command, **kwargs):
        return handle_command(self.controller, {'command': command, **kwargs}, selection)

    def test_selection_is_restored_after_restart_without_contacting_sonos(self):
        from unittest import mock
        with mock.patch('sonoscdplayer.sonos.target_selection.check_member'):
            first = self.selection()
            self.assertTrue(self.request(first, 'set-target', uid='RINCON_A',
                                         members=['RINCON_C', 'RINCON_B'])['ok'])
        contacted = []
        restarted = self.selection(verify=contacted.append)
        self.assertTrue(restarted.restore())
        self.assertEqual(restarted.uid_for_explicit_action(), 'RINCON_A')
        self.assertEqual(restarted.members_for_explicit_action(), ('RINCON_B', 'RINCON_C'))
        self.assertEqual(contacted, [])
        self.assertEqual(self.controller.calls, [])

    def test_restored_selection_is_used_only_by_explicit_play(self):
        self.store.save('RINCON_A', ())
        selection = self.selection()
        selection.restore()
        plays = []
        self.controller.play = lambda uid, members=(): plays.append((uid, members))
        self.assertEqual(plays, [])
        self.assertTrue(self.request(selection, 'play-selected')['ok'])
        self.assertEqual(plays, [('RINCON_A', ())])

    def test_failed_play_keeps_the_remembered_selection(self):
        self.store.save('RINCON_A', ())
        selection = self.selection()
        selection.restore()
        def unreachable(uid, members=()):
            raise RuntimeError('target not found')
        self.controller.play = unreachable
        self.assertFalse(self.request(selection, 'play-selected')['ok'])
        self.assertEqual(selection.uid_for_explicit_action(), 'RINCON_A')
        self.assertEqual(self.store.load(), ('RINCON_A', ()))

    def test_clear_forgets_the_selection(self):
        selection = self.selection()
        self.request(selection, 'set-target', uid='RINCON_A')
        self.assertTrue(self.request(selection, 'clear-target')['ok'])
        self.assertIsNone(self.store.load())
        self.assertFalse(self.selection().restore())

    def test_rejected_selection_does_not_overwrite_the_file(self):
        selection = self.selection()
        self.request(selection, 'set-target', uid='RINCON_A')
        selection._verify = lambda uid: (_ for _ in ()).throw(RuntimeError('grouped'))
        self.assertFalse(self.request(selection, 'set-target', uid='RINCON_B')['ok'])
        self.assertEqual(self.store.load(), ('RINCON_A', ()))

    def test_file_is_private_and_contains_no_extra_data(self):
        import json
        self.store.save('RINCON_A', ('RINCON_B',))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(self.store.path.read_text()),
                         {'version': 1, 'coordinator': 'RINCON_A', 'members': ['RINCON_B']})
        self.assertEqual(list(self.state_dir.iterdir()), [self.store.path])

    def test_invalid_or_foreign_files_mean_nothing_selected(self):
        self.state_dir.mkdir()
        for content in ('{', '[]', '{"version": 2, "coordinator": "RINCON_A"}',
                        '{"version": 1, "coordinator": "bad uid!"}',
                        '{"version": 1, "coordinator": "RINCON_A", "members": ["RINCON_A"]}'):
            with self.subTest(content=content):
                self.store.path.write_text(content)
                self.assertIsNone(self.store.load())
                self.assertFalse(self.selection().restore())
        self.store.path.unlink()
        self.store.path.symlink_to(self.state_dir / 'elsewhere.json')
        (self.state_dir / 'elsewhere.json').write_text('{"version": 1, "coordinator": "RINCON_A"}')
        self.assertIsNone(self.store.load())

    def test_unwritable_state_directory_only_loses_persistence(self):
        self.state_dir.parent.joinpath('blocker').write_text('')
        store = type(self.store)(self.state_dir.parent / 'blocker', log=self.logs.append)
        selection = SelectedTarget(verify=lambda uid: None, store=store)
        self.assertTrue(self.request(selection, 'set-target', uid='RINCON_A')['ok'])
        self.assertEqual(selection.uid_for_explicit_action(), 'RINCON_A')
        self.assertTrue(any('not saved' in line for line in self.logs))

    def test_state_directory_follows_systemd(self):
        from unittest import mock
        from pathlib import Path
        from sonoscdplayer.sonos.target_store import default_state_dir
        with mock.patch.dict('os.environ', {'STATE_DIRECTORY': '/var/lib/example'}):
            self.assertEqual(default_state_dir(), Path('/var/lib/example'))
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(default_state_dir(), Path('/var/lib/sonoscdplayer'))

    def test_player_unit_provides_the_state_directory(self):
        from pathlib import Path
        unit = (Path(__file__).resolve().parents[2] / 'systemd/sonoscdplayer-player.service').read_text()
        self.assertIn('StateDirectory=sonoscdplayer', unit)
        self.assertIn('StateDirectoryMode=0700', unit)


class RememberedTargetMenuTests(unittest.TestCase):
    """After a restart the menu shows the player's remembered speakers as checked."""

    def topology(self):
        devices = []
        for uid, name in (('RINCON_A', 'Hobbyraum'), ('RINCON_B', 'Küche'), ('RINCON_C', 'Wohnzimmer')):
            device = NS(uid=uid, player_name=name)
            device.group = NS(coordinator=device, members=[device])
            devices.append(device)
        return from_devices(devices)

    def menu(self, current):
        return InteractiveMenu(discover=lambda **kw: self.topology(), target_rpc=lambda uid: True,
                               group_target_rpc=lambda uid, members: True, current_target=current)

    def settle(self, menu, now=0.0):
        import time as _time
        for step in range(200):
            menu.poll(now=now + step * 0.01)
            if menu._current_loaded and menu.state.topology.targets:
                return
            _time.sleep(0.005)
        self.fail('menu did not settle')

    def test_remembered_group_is_checked(self):
        menu = self.menu(lambda: frozenset({'RINCON_A', 'RINCON_C'}))
        self.settle(menu)
        self.assertEqual(menu.state.applied, frozenset({'RINCON_A', 'RINCON_C'}))
        self.assertEqual(menu.state.draft, frozenset({'RINCON_A', 'RINCON_C'}))

    def test_player_without_selection_leaves_menu_empty(self):
        menu = self.menu(lambda: frozenset())
        self.settle(menu)
        self.assertEqual(menu.state.applied, frozenset())

    def test_unreachable_player_is_retried_later(self):
        calls = []
        def reader():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionRefusedError('player not up yet')
            return frozenset({'RINCON_B'})
        menu = self.menu(reader)
        import time as _time
        for step in range(100):
            menu.poll(now=step * 0.01)
            _time.sleep(0.005)
        self.assertFalse(menu._current_loaded)
        for step in range(200):
            menu.poll(now=10 + step * 0.01)
            if menu._current_loaded:
                break
            _time.sleep(0.005)
        self.assertEqual(menu.state.applied, frozenset({'RINCON_B'}))
        self.assertEqual(len(calls), 2)

    def test_local_choice_is_never_overwritten(self):
        import threading
        release = threading.Event()
        def slow():
            release.wait(2)
            return frozenset({'RINCON_A'})
        menu = self.menu(slow)
        menu.poll(now=0.0)
        menu.state = replace(menu.state, draft=frozenset({'RINCON_B'}))
        release.set()
        self.settle(menu, now=1.0)
        self.assertEqual(menu.state.draft, frozenset({'RINCON_B'}))
        self.assertEqual(menu.state.applied, frozenset())

    def test_read_target_client_over_socket(self):
        import json, socket, tempfile, threading
        from pathlib import Path
        from sonoscdplayer.ui.menu.speakers.target_rpc_client import read_target
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'control.sock')
            server = socket.socket(socket.AF_UNIX)
            server.bind(path); server.listen(1)
            def answer():
                conn, _ = server.accept()
                with conn:
                    self.assertEqual(json.loads(conn.recv(4096)), {'command': 'get-target'})
                    conn.sendall(json.dumps({'ok': True, 'target_selection': {
                        'selected': True, 'ready_for_playback': False,
                        'speakers': ['RINCON_A', 'RINCON_C']}}).encode() + b'\n')
            thread = threading.Thread(target=answer, daemon=True); thread.start()
            try:
                self.assertEqual(read_target(socket_path=path), frozenset({'RINCON_A', 'RINCON_C'}))
            finally:
                thread.join(2); server.close()

if __name__ == '__main__':
    unittest.main()
