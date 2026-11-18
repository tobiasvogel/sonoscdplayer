"""Player service: control socket, status schema, pause/resume, startup and fail-closed exit (offline)."""
import contextlib
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from player_service import (
    handle_command,
    serve,
    StatusPublisher,
    refuse_existing_control_socket,
    run,
)
from player_remote import request
from player_controller import Controller
from player_state import PlayerState, Mode



class DummyController:
    def __init__(self):
        self.calls = []
        self.refreshes = 0
    def status(self):
        return 'mode=STOPPED track=2/15 sonos=UNKNOWN owned=False server=False'
    def status_snapshot(self):
        return {'schema_version': 1, 'player': {'mode': 'STOPPED'}}
    def play(self, uid):
        self.calls.append(('play', uid))
    def command(self, cmd):
        self.calls.append(cmd)
        if cmd == 'stop':
            raise ValueError('not owner')
    def refresh(self):
        self.refreshes += 1


class ServiceTests(unittest.TestCase):
    def test_commands_and_rejects(self):
        c = DummyController()
        for name in ('next','previous','select 3'):
            self.assertTrue(handle_command(c, {'command': name})['ok'])
        self.assertTrue(handle_command(c, {'command':'play','uid':'TEST_UID'})['ok'])
        self.assertEqual(c.calls, ['next','previous','select 3',('play','TEST_UID')])
        self.assertFalse(handle_command(c, {'command':'play'})['ok'])
        self.assertFalse(handle_command(c, {'command':'status','uid':'TEST_UID'})['ok'])
        for payload in ({'command': 'quit'}, {'command': 'bad'}, {'command':'status','extra':1},
                        {'command': 22}, [], {'command': 'select 1234'}):
            self.assertFalse(handle_command(c, payload)['ok'])
        self.assertFalse(handle_command(c, {'command': 'stop'})['ok'])
        self.assertEqual(c.calls[-1], 'stop')
        self.assertTrue(handle_command(c, {'command': 'status'})['ok'])
        self.assertTrue(handle_command(c, {'command': 'help'})['ok'])

    def test_live_local_socket_permissions_and_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            c = DummyController()
            thread = threading.Thread(target=serve, args=(c, path), kwargs={'poll_seconds': 0.01}, daemon=True)
            thread.start()
            for _ in range(500):  # up to 5 s on a loaded machine
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertTrue(request(path, 'status')['ok'])
            self.assertTrue(request(path, 'next')['ok'])
            self.assertEqual(c.calls, ['next'])
            with socket.socket(socket.AF_UNIX) as s:
                s.connect(path)
                s.sendall(b'X'*300 + b'\n')
                self.assertFalse(json.loads(s.recv(4096))['ok'])
            self.assertGreater(c.refreshes, 0)

    def test_serve_restores_the_remembered_target(self):
        from target_store import TargetStore
        with tempfile.TemporaryDirectory() as td:
            store = TargetStore(Path(td) / 'state', log=lambda message: None)
            store.save('RINCON_A', ())
            path = str(Path(td) / 'control.sock')
            c = DummyController()
            thread = threading.Thread(target=serve, args=(c, path),
                                      kwargs={'poll_seconds': 0.01, 'target_store': store}, daemon=True)
            with contextlib.redirect_stdout(io.StringIO()):
                thread.start()
                for _ in range(500):  # up to 5 s on a loaded machine
                    if os.path.exists(path):
                        break
                    time.sleep(0.01)
                reply = request(path, 'get-target')
            self.assertTrue(reply['target_selection']['selected'])
            self.assertEqual(reply['target_selection']['speakers'], ['RINCON_A'])
            self.assertNotIn('RINCON_A', json.dumps(request(path, 'status')))
            self.assertEqual(c.calls, [])

    def test_slow_play_receives_reply_beyond_old_five_second_limit(self):
        class SlowController(DummyController):
            def play(self, uid):
                time.sleep(0.20)
                super().play(uid)
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            c = SlowController()
            thread = threading.Thread(target=serve, args=(c, path),
                                      kwargs={'poll_seconds': 0.01}, daemon=True)
            thread.start()
            for _ in range(500):  # up to 5 s on a loaded machine
                if os.path.exists(path):
                    break
                time.sleep(0.01)
            self.assertTrue(request(path, 'play', uid='TEST_UID', response_timeout=1)['ok'])
            self.assertEqual(c.calls, [('play','TEST_UID')])

    def test_response_timeout_is_not_a_command_failure(self):
        from unittest.mock import patch
        import io
        from contextlib import redirect_stderr
        from player_remote import run
        err = io.StringIO()
        with patch('player_remote.request', side_effect=socket.timeout), redirect_stderr(err):
            self.assertEqual(run(['--uid','TEST_UID','play']), 2)
        self.assertIn('outcome UNKNOWN', err.getvalue())
        self.assertIn('Do NOT blindly repeat', err.getvalue())

    def test_existing_path_not_unlinked(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'control.sock'
            p.write_text('keep')
            with self.assertRaises(FileExistsError):
                serve(DummyController(), str(p))
            self.assertEqual(p.read_text(), 'keep')


class Server:
    def __init__(self): self.up = False
    def alive(self): return self.up
    def start(self, track): self.up = True
    def close(self): self.up = False


class Target:
    uid = 'TEST_SPEAKER'
    ip_address = '198.51.100.22'
    def __init__(self):
        self.transport = 'STOPPED'
        self.uri = ''
        self.plays = 0
        self.stops = 0
        self.group = type('Group', (), {'members': [self], 'coordinator': self})()
    def play_uri(self, uri, title):
        self.plays += 1
        self.uri = uri
        self.transport = 'TRANSITIONING'
    def get_current_transport_info(self):
        return {'current_transport_state': self.transport}
    def get_current_track_info(self):
        return {'uri': self.uri}
    def stop(self): self.stops += 1


class StatusAPITests(unittest.TestCase):
    def setUp(self):
        self.target = Target()
        self.server = Server()
        self.verify_calls = 0
        def verify(uid):
            self.verify_calls += 1
            self.assertEqual(uid, 'TEST_SPEAKER')
            return self.target
        self.ctrl = Controller(PlayerState().disc_loaded((1, 2, 3)), None, None,
                               8765, self.server, verify=verify,
                               route=lambda ip: '192.0.2.44')

    def test_stopped_json_and_legacy_status(self):
        reply = handle_command(self.ctrl, {'command':'status'})
        self.assertTrue(reply['ok'])
        self.assertIn('mode=STOPPED track=1/3', reply['status'])
        self.assertEqual(reply['state'], {
            'schema_version':1,
            'player':{'mode':'STOPPED','track':1,'track_count':3,'error':None},
            'sonos':{'target_uid':None,'transport':'UNKNOWN','owned':False,
                     'group_cleanup_pending':False},
            'stream':{'active':False},
            'software':reply['state']['software'],
        })
        self.assertRegex(reply['state']['software']['version'], r'^\d+\.\d+\.\d+')
        self.assertEqual(self.verify_calls, 0)
        json.dumps(reply)

    def test_status_never_calls_sonos_or_stream_start(self):
        for _ in range(50):
            handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(self.verify_calls, 0)
        self.assertEqual(self.target.plays, 0)
        self.assertFalse(self.server.up)

    def test_play_and_stop_status_lifecycle(self):
        result = handle_command(self.ctrl, {'command':'play','uid':'TEST_SPEAKER'})
        self.assertTrue(result['ok'])
        self.assertEqual(result['state']['player']['mode'], 'STARTING')
        self.assertEqual(result['state']['sonos']['target_uid'], 'TEST_SPEAKER')
        self.assertTrue(result['state']['stream']['active'])
        self.assertEqual(self.target.plays, 1)
        self.target.transport = 'PLAYING'
        self.ctrl.refresh()
        result = handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(result['state']['player']['mode'], 'PLAYING')
        self.assertEqual(result['state']['sonos']['transport'], 'PLAYING')
        self.assertEqual(self.target.plays, 1)
        # Simulate external natural end (no destructive command).
        self.target.transport = 'STOPPED'
        self.ctrl.refresh()
        result = handle_command(self.ctrl, {'command':'status'})
        self.assertEqual(result['state']['player']['mode'], 'STOPPED')
        self.assertIsNone(result['state']['sonos']['target_uid'])
        self.assertFalse(result['state']['stream']['active'])
        self.assertEqual(self.target.stops, 0)

    def test_error_reply_retains_structured_and_legacy_fields(self):
        result = handle_command(self.ctrl, {'command':'play'})
        self.assertFalse(result['ok'])
        self.assertIn('status', result)
        self.assertEqual(result['state']['schema_version'], 1)
        self.assertEqual(result['state']['player']['mode'], 'STOPPED')

    def test_select_changes_snapshot_without_sonos(self):
        reply = handle_command(self.ctrl, {'command':'select 3'})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['state']['player']['track'], 3)
        self.assertEqual(self.verify_calls, 0)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.target=Target();self.server=Server()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2,3)),None,None,8765,self.server,
                             verify=lambda uid:self.target,route=lambda ip:'192.0.2.44')

    def test_starting_stopped_can_be_released_without_sonos_stop(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'play','uid':'TEST_SPEAKER'})['ok'])
        self.target.transport='STOPPED'
        result=handle_command(self.ctrl,{'command':'stop'})
        self.assertTrue(result['ok']);self.assertEqual(self.target.stops,0)
        self.assertEqual(result['state']['player']['mode'],'STOPPED')
        self.assertFalse(self.server.alive())

    def test_starting_transitional_stop_is_refused(self):
        self.ctrl.play('TEST_SPEAKER')
        self.assertFalse(handle_command(self.ctrl,{'command':'stop'})['ok'])
        self.assertEqual(self.target.stops,0)

    def test_stopped_start_timeout_closes_local_only(self):
        self.ctrl.play('TEST_SPEAKER')
        self.target.transport='STOPPED'
        with patch('player_controller.time.monotonic',return_value=self.ctrl.start_monotonic+21):
            self.ctrl.refresh()
        with patch('player_controller.time.monotonic',return_value=self.ctrl.start_monotonic+27):
            self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode.value,'ERROR')
        self.assertFalse(self.server.alive())
        self.assertEqual(self.target.stops,0)

    def test_force_stop_requires_explicit_uid_and_solo(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'stop','force':True})['ok'])
        self.assertFalse(handle_command(self.ctrl,{'command':'next','uid':'TEST_SPEAKER','force':True})['ok'])
        self.assertEqual(self.target.stops,0)
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertTrue(r['ok']);self.assertEqual(self.target.stops,1)

    def test_force_stop_clears_detached_error(self):
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl._fail('start aborted')
        self.assertEqual(self.ctrl.state.mode.value,'ERROR')
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertTrue(r['ok'])
        self.assertEqual(r['state']['player']['mode'],'STOPPED')
        self.assertIsNone(r['state']['player']['error'])
        self.assertEqual(self.target.stops,1)
        self.assertTrue(handle_command(self.ctrl,{'command':'play','uid':'TEST_SPEAKER'})['ok'])

    def test_reset_is_local_only_after_error(self):
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl._fail('start aborted')
        r=handle_command(self.ctrl,{'command':'reset'})
        self.assertTrue(r['ok'])
        self.assertEqual(r['state']['player']['mode'],'STOPPED')
        self.assertEqual(self.target.stops,0)

    def test_play_selected_retries_after_released_error(self):
        """18.11.: PLAY on the device after a player error tries again."""
        from player_target_selection import SelectedTarget
        selection = SelectedTarget(verify=lambda uid: True)
        selection._uid = 'TEST_SPEAKER'
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl._fail('resume failed')
        self.assertTrue(self.ctrl.can_reset())
        self.target.transport = 'STOPPED'
        r = handle_command(self.ctrl, {'command': 'play-selected'}, selection)
        self.assertTrue(r['ok'], r)
        self.assertEqual(r['state']['player']['mode'], 'STARTING')
        self.assertEqual(self.target.stops, 0)

    def test_play_selected_does_not_clear_an_unreleased_error(self):
        from player_target_selection import SelectedTarget
        selection = SelectedTarget(verify=lambda uid: True)
        selection._uid = 'TEST_SPEAKER'
        self.ctrl.play('TEST_SPEAKER')
        self.ctrl.state = self.ctrl.state.failed('still attached')   # server still up
        self.assertFalse(self.ctrl.can_reset())
        self.assertFalse(handle_command(self.ctrl, {'command': 'play-selected'}, selection)['ok'])
        self.assertEqual(self.ctrl.state.mode.value, 'ERROR')

    def test_reset_refuses_active_and_stopped_state(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'reset'})['ok'])
        self.ctrl.play('TEST_SPEAKER')
        self.assertFalse(handle_command(self.ctrl,{'command':'reset'})['ok'])
        self.assertEqual(self.target.stops,0)

    def test_force_stop_refuses_group(self):
        other=Target();self.target.group.members.append(other)
        with patch('sonos_parallel_test.find_target',return_value=self.target):
            r=handle_command(self.ctrl,{'command':'stop','uid':'TEST_SPEAKER','force':True})
        self.assertFalse(r['ok']);self.assertEqual(self.target.stops,0)

    def test_force_stop_refuses_other_uid_during_active_session(self):
        self.ctrl.play('TEST_SPEAKER')
        r=handle_command(self.ctrl,{'command':'stop','uid':'ANOTHER_SPEAKER','force':True})
        self.assertFalse(r['ok']);self.assertEqual(self.target.stops,0)


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.target, self.server = Target(), Server()
        self.ctrl = Controller(PlayerState().disc_loaded((1,2,3)),None,None,8765,self.server,
                               verify=lambda uid:self.target, route=lambda ip:'192.0.2.44')
    def playing(self):
        self.ctrl.play('TEST_SPEAKER')
        self.target.transport='PLAYING'
        self.ctrl.refresh()
    def test_owned_playing_is_stopped(self):
        self.playing()
        result=self.ctrl.shutdown_owned()
        self.assertEqual(result['action'],'stopped_owned')
        self.assertEqual(self.target.stops,1)
        self.ctrl.close()
        self.assertFalse(self.server.alive())
    def test_starting_never_force_stops(self):
        self.ctrl.play('TEST_SPEAKER')
        result=self.ctrl.shutdown_owned()
        self.assertEqual(result['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
        self.ctrl.close()
    def test_changed_uri_no_stop(self):
        self.playing()
        self.target.uri='http://example.invalid/foreign.wav'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_group_changed_no_stop(self):
        self.playing()
        self.target.group.members.append(Target())
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_transport_stopped_no_stop(self):
        self.playing()
        self.target.transport='STOPPED'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped')
        self.assertEqual(self.target.stops,0)
    def test_leave_playing_never_stops(self):
        self.playing()
        self.assertEqual(self.ctrl.shutdown_owned('leave_playing')['action'],'left_playing')
        self.assertEqual(self.target.stops,0)
    def test_live_read_only_does_not_attach(self):
        self.playing()
        with patch('sonos_parallel_test.find_target', return_value=self.target):
            reply=handle_command(self.ctrl,{'command':'sonos-status','uid':'TEST_SPEAKER'})
        self.assertTrue(reply['ok'])
        self.assertEqual(reply['live_sonos']['transport'],'PLAYING')
        self.assertTrue(reply['live_sonos']['solo'])
        self.assertEqual(self.target.stops,0)
        self.assertEqual(self.target.plays,1)
    def test_live_status_requires_explicit_uid(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'sonos-status'})['ok'])
    def test_shutdown_twice_never_stops_twice(self):
        self.playing()
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'stopped_owned')
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'nothing_to_stop')
        self.assertEqual(self.target.stops,1)


class PausableTarget(Target):
    def __init__(self):
        super().__init__(); self.pauses=0; self.resumes=0
    def pause(self):
        self.pauses+=1;self.transport='PAUSED_PLAYBACK'
    def play(self):
        self.resumes+=1;self.transport='PLAYING'
    def stop(self):
        super().stop();self.transport='STOPPED'


class PauseTests(unittest.TestCase):
    def setUp(self):
        self.target=PausableTarget();self.server=Server()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2)),None,None,8765,self.server,verify=lambda uid:self.target,route=lambda ip:'192.0.2.44')
    def playing(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.target.transport='PLAYING';self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def paused(self):
        self.playing();self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_full_toggle_and_stop(self):
        self.paused();self.assertTrue(self.server.alive());self.assertEqual(self.target.pauses,1)
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING);self.assertEqual(self.target.resumes,1)
        self.ctrl.stop();self.assertEqual(self.target.stops,1)
    def test_changed_uri_blocks_resume_and_stop(self):
        self.paused();self.target.uri='foreign'
        self.assertFalse(handle_command(self.ctrl,{'command':'resume'})['ok']);self.assertEqual(self.target.resumes,0)
        self.assertFalse(handle_command(self.ctrl,{'command':'stop'})['ok']);self.assertEqual(self.target.stops,0)
    def test_changed_group_blocks_pause(self):
        self.playing();self.target.group.members.append(PausableTarget())
        self.assertFalse(handle_command(self.ctrl,{'command':'pause'})['ok']);self.assertEqual(self.target.pauses,0)
    def test_pause_status_no_server_close(self):
        self.paused();r=handle_command(self.ctrl,{'command':'status'})
        self.assertEqual(r['state']['player']['mode'],'PAUSED');self.assertTrue(r['state']['stream']['active'])
        self.ctrl.refresh();self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_external_resume_observed(self):
        self.paused();self.target.transport='PLAYING';self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def test_paused_shutdown_requires_exact_uri(self):
        self.paused();self.assertEqual(self.ctrl.shutdown_owned()['action'],'stopped_owned')
        self.assertEqual(self.target.stops,1)
    def test_paused_shutdown_denies_foreign(self):
        self.paused();self.target.uri='other'
        self.assertEqual(self.ctrl.shutdown_owned()['action'],'not_stopped');self.assertEqual(self.target.stops,0)
    def test_starting_toggle_denied(self):
        self.assertTrue(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
        self.assertFalse(handle_command(self.ctrl,{'command':'playpause','uid':'TEST_SPEAKER'})['ok'])
    def test_uid_mismatch_denied(self):
        self.paused();self.assertFalse(handle_command(self.ctrl,{'command':'playpause','uid':'OTHER'})['ok'])
        self.assertEqual(self.target.resumes,0)
    def test_service_requires_uid_on_toggle(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'playpause'})['ok'])
    def test_no_pause_on_stopped(self):
        self.assertFalse(handle_command(self.ctrl,{'command':'pause'})['ok'])


class CountingServer(Server):
    def __init__(self):
        super().__init__(); self.starts=0; self.closes=0
    def start(self, track):
        self.starts += 1; super().start(track)
    def close(self):
        self.closes += 1; super().close()


class PauseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.target=PausableTarget(); self.server=CountingServer()
        self.ctrl=Controller(PlayerState().disc_loaded((1,2,3)), None,None,8765,self.server,
                             verify=lambda uid:self.target, route=lambda ip:'192.0.2.44')
        self.server.closes=0  # ignore constructor cleanup
    def send(self, command, uid=False):
        r={'command':command}
        if uid: r['uid']='TEST_SPEAKER'
        return handle_command(self.ctrl,r)
    def playing(self):
        self.assertTrue(self.send('playpause',True)['ok'])
        self.target.transport='PLAYING'; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
    def paused(self):
        self.playing(); self.assertTrue(self.send('pause')['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
    def test_multiple_pause_resume_cycles_keep_same_stream(self):
        self.playing(); url=self.ctrl.owned_url
        for _ in range(5):
            self.assertTrue(self.send('playpause',True)['ok'])
            self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
            self.ctrl.refresh()
            self.assertTrue(self.send('playpause',True)['ok'])
            self.ctrl.refresh()
            self.assertEqual(self.ctrl.state.mode,Mode.PLAYING)
            self.assertEqual(self.ctrl.owned_url,url)
        self.assertEqual(self.server.starts,1)
        self.assertEqual(self.server.closes,1)
        self.assertEqual(self.target.pauses,5)
        self.assertEqual(self.target.resumes,5)
    def test_long_pause_refresh_does_not_expire_session(self):
        self.paused()
        for _ in range(90): self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.PAUSED)
        self.assertTrue(self.send('resume')['ok'])
        self.assertEqual(self.server.starts,1)
    def test_duplicate_pause_and_resume_rejected(self):
        self.paused(); self.assertFalse(self.send('pause')['ok'])
        self.assertEqual(self.target.pauses,1)
        self.assertTrue(self.send('resume')['ok'])
        self.assertFalse(self.send('resume')['ok'])
        self.assertEqual(self.target.resumes,1)
    def test_stop_from_pause_cleans_only_own_session(self):
        self.paused(); self.assertTrue(self.send('stop')['ok'])
        self.assertEqual(self.ctrl.state.mode,Mode.STOPPED)
        self.assertEqual(self.target.stops,1)
        self.assertEqual(self.server.closes,2)
        self.assertFalse(self.send('resume')['ok'])
    def test_next_from_paused_stops_owned_then_starts_new_track(self):
        self.paused(); old=self.ctrl.owned_url
        self.assertTrue(self.send('next')['ok'])
        self.assertEqual(self.ctrl.state.selected_track,2)
        self.assertEqual(self.ctrl.state.mode,Mode.STARTING)
        self.assertNotEqual(self.ctrl.owned_url,old)
        self.assertEqual(self.target.stops,1)
        self.assertEqual(self.server.starts,2)
    def test_previous_from_paused_starts_previous(self):
        self.assertTrue(self.send('select 2')['ok'])
        self.paused(); self.assertTrue(self.send('previous')['ok'])
        self.assertEqual(self.ctrl.state.selected_track,1)
        self.assertEqual(self.target.stops,1)
    def test_foreign_uri_blocks_paused_next(self):
        self.paused(); self.target.uri='http://example.invalid/foreign.wav'
        self.assertFalse(self.send('next')['ok'])
        self.assertEqual(self.target.stops,0)
        self.assertEqual(self.server.starts,1)
    def test_foreign_group_blocks_paused_stop(self):
        self.paused(); self.target.group.members.append(PausableTarget())
        self.assertFalse(self.send('stop')['ok'])
        self.assertEqual(self.target.stops,0)
    def test_external_stop_during_pause_clears_session_without_stop_command(self):
        self.paused(); self.target.transport='STOPPED'; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.STOPPED)
        self.assertFalse(self.server.alive())
        self.assertEqual(self.target.stops,0)
        self.assertFalse(self.send('resume')['ok'])
    def test_reader_server_failure_during_pause_is_error(self):
        self.paused(); self.server.up=False; self.ctrl.refresh()
        self.assertEqual(self.ctrl.state.mode,Mode.ERROR)
        self.assertFalse(self.send('resume')['ok'])


class FakeServer:
    def alive(self): return False
    def reader_active(self): return False


class FakeState:
    def __init__(self):
        self.mode = type('M', (), {'value': 'STOPPED'})()
        self.error = None
        self.selected_track = 1
        self.track_numbers = [1]


class BlockingController:
    def __init__(self):
        self.state = FakeState()
        self.server = FakeServer()
        self.uid = None
        self.owned_url = None
        self.last_transport = 'STOPPED'
        self.status_hook = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def status(self):
        return f'mode={self.state.mode.value} track=1/1 sonos={self.last_transport} owned=False server=False'

    def status_snapshot(self):
        return {'schema_version': 1, 'player': {'mode': self.state.mode.value, 'track': 1, 'track_count': 1, 'error': None},
                'sonos': {'target_uid': None, 'transport': self.last_transport, 'owned': False},
                'stream': {'active': False}}

    def refresh(self):
        self.calls += 1

    def play(self, uid):
        self.state.mode = type('M', (), {'value': 'STARTING'})()
        self.status_hook()
        self.entered.set()
        self.release.wait(10)
        self.state.mode = type('M', (), {'value': 'PLAYING'})()


def raw_request(path, payload, timeout=1.0):
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(path)
        sock.sendall(json.dumps(payload).encode() + b'\n')
        data = b''
        while not data.endswith(b'\n'):
            data += sock.recv(4096)
        return json.loads(data)


class ResponsiveStatusTest(unittest.TestCase):
    def test_status_is_served_during_blocking_play(self):
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'control.sock')
            controller = BlockingController()
            errors = []
            def worker():
                try: serve(controller, path, poll_seconds=0.03)
                except BaseException as ex: errors.append(ex)
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            for _ in range(200):
                if Path(path).exists(): break
                time.sleep(0.01)
            self.assertTrue(Path(path).exists())
            play_result = []
            play_thread = threading.Thread(target=lambda: play_result.append(raw_request(path, {'command':'play','uid':'TEST_UID'},timeout=15)), daemon=True)
            play_thread.start()
            try:
                self.assertTrue(controller.entered.wait(10))  # generous under full-suite load
                start = time.monotonic()
                status = raw_request(path, {'command':'status'})
                elapsed = time.monotonic() - start
                self.assertLess(elapsed, 0.7)
                self.assertEqual(status['state']['player']['mode'], 'STARTING')
                self.assertFalse(status['state']['sonos']['owned'])
                self.assertTrue(play_thread.is_alive())
            finally:
                controller.release.set()
            play_thread.join(15)
            self.assertTrue(play_result[0]['ok'])

    def test_snapshot_copy_cannot_mutate_published_state(self):
        c=BlockingController()
        p=StatusPublisher(c)
        result=p.read();result['state']['player']['mode']='WRONG'
        self.assertEqual(p.read()['state']['player']['mode'],'STOPPED')


class EarlySocketGuardTests(unittest.TestCase):
    def test_existing_socket_is_rejected_before_drive_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('', encoding='utf-8')
            occupied = Path(directory) / 'control.sock'
            occupied.touch()
            with (patch('player_service.detect_drive', side_effect=AssertionError('drive touched')),
                  patch('player_service.read_toc', side_effect=AssertionError('CD read'))):
                self.assertEqual(run(['--config', str(config), '--socket', str(occupied)]), 1)
            self.assertTrue(occupied.exists())

    def test_symlink_is_also_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            dangling = Path(directory) / 'control.sock'
            dangling.symlink_to(Path(directory) / 'missing')
            with self.assertRaises(FileExistsError):
                refuse_existing_control_socket(dangling)
            self.assertTrue(dangling.is_symlink())

    def test_free_socket_path_passes_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            refuse_existing_control_socket(Path(directory) / 'unused.sock')


if __name__ == '__main__':
    unittest.main()
