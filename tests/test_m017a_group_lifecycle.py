"""Simulated controller lifecycle for explicit temporary groups."""
import contextlib
import io
import unittest
from player_controller import Controller
from player_state import PlayerState, Mode

class Topology:
    def __init__(self):
        self.leaders = {'A':'A','B':'B','C':'C'}
        self.devices = {uid: Speaker(uid,self) for uid in self.leaders}

class Speaker:
    def __init__(self, uid, topology):
        self.uid=uid
        self.topology=topology
        self.ip_address='192.0.2.20'
        self.transport='STOPPED'
        self.uri=''
        self.stop_calls=0
        self.unjoin_calls=0
        self.play_calls=0
    @property
    def group(self):
        leader=self.topology.leaders[self.uid]
        coord=self.topology.devices[leader]
        members=[self.topology.devices[u] for u,v in self.topology.leaders.items() if v==leader]
        return type('Group',(),{'coordinator':coord,'members':members})()
    def get_current_transport_info(self):
        return {'current_transport_state':self.transport}
    def get_current_track_info(self):
        return {'uri':self.uri}
    def join(self,coordinator):
        self.topology.leaders[self.uid]=coordinator.uid
    def unjoin(self):
        self.unjoin_calls+=1
        self.topology.leaders[self.uid]=self.uid
    def play_uri(self,url,title):
        self.play_calls+=1
        self.uri=url
        self.transport='TRANSITIONING'
    def stop(self):
        self.stop_calls+=1
        self.transport='STOPPED'
    def pause(self): self.transport='PAUSED_PLAYBACK'
    def play(self): self.transport='PLAYING'

class Server:
    def __init__(self): self.running=False
    def alive(self): return self.running
    def start(self,track): self.running=True
    def close(self): self.running=False

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.topo=Topology()
        self.a,self.b,self.c=(self.topo.devices[u] for u in ('A','B','C'))
        self.server=Server()
        def verify(uid):
            p=self.topo.devices[uid]
            if p.group.coordinator.uid!=uid or len(p.group.members)!=1 or p.transport!='STOPPED':
                raise RuntimeError('not stopped solo')
            return p
        self.player=Controller(PlayerState().disc_loaded((1,2,3)),
                               'A','192.0.2.10',8765,self.server,
                               verify=verify,
                               find_many=lambda uids:{uid:self.topo.devices[uid] for uid in uids})
    def start(self):
        self.player.play('A',['B'])
        self.a.transport='PLAYING'
        self.player.refresh()
    def test_explicit_group_only_coordinator_plays(self):
        self.start()
        self.assertEqual((self.a.play_calls,self.b.play_calls),(1,0))
        self.assertEqual(self.topo.leaders['B'],'A')
    def test_pause_resume_owned_temporary_group(self):
        self.start()
        self.player.pause()
        self.assertEqual(self.player.state.mode, Mode.PAUSED)
        self.assertEqual(self.a.transport, 'PAUSED_PLAYBACK')
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.player.resume()
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.assertEqual(self.a.transport, 'PLAYING')
        self.assertEqual(self.topo.leaders['B'], 'A')

    def test_pause_blocks_unexpected_member_without_sonos_write(self):
        self.start()
        self.topo.leaders['C'] = 'A'
        with self.assertRaisesRegex(RuntimeError, 'unexpected topology'):
            self.player.pause()
        self.assertEqual(self.a.transport, 'PLAYING')
        self.assertEqual(self.player.state.mode, Mode.PLAYING)

    def test_resume_blocks_unexpected_member_without_sonos_write(self):
        self.start()
        self.player.pause()
        self.topo.leaders['C'] = 'A'
        with self.assertRaisesRegex(RuntimeError, 'unexpected topology'):
            self.player.resume()
        self.assertEqual(self.a.transport, 'PAUSED_PLAYBACK')
        self.assertEqual(self.player.state.mode, Mode.PAUSED)

    def test_pause_blocks_foreign_uri_without_sonos_write(self):
        self.start()
        self.a.uri = 'x-sonos-spotify:foreign'
        with self.assertRaisesRegex(RuntimeError, 'exact URI mismatch'):
            self.player.pause()
        self.assertEqual(self.a.transport, 'PLAYING')

    def test_stop_releases_only_owned_group(self):
        self.start()
        self.player.stop()
        self.assertEqual(self.topo.leaders['B'],'B')
        self.assertEqual((self.a.stop_calls,self.b.unjoin_calls),(1,1))
    def test_external_group_change_blocks_stop(self):
        self.start()
        self.topo.leaders['C']='A'
        with self.assertRaises(RuntimeError):
            self.player.stop()
        self.assertEqual((self.a.stop_calls,self.b.unjoin_calls),(0,0))
    def test_track_switch_keeps_group(self):
        self.start()
        self.player.change('next')
        self.assertEqual(self.topo.leaders['B'],'A')
        self.assertEqual(self.b.unjoin_calls,0)
        self.assertEqual(self.a.play_calls,2)
    def test_failed_play_uri_leaves_group_for_manual_recovery(self):
        def fail(url,title):
            raise OSError('ambiguous send')
        self.a.play_uri=fail
        with self.assertRaises(OSError):
            self.player.play('A',['B'])
        self.assertEqual(self.topo.leaders['B'],'A')
        self.assertTrue(self.player._group_uncertain)
        self.assertEqual(self.b.unjoin_calls,0)
        with self.assertRaises(RuntimeError):
            self.player.reset()
    def test_refresh_failure_preserves_group_and_reports_recovery(self):
        self.start()
        self.a.uri = 'x-sonos-spotify:foreign'
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.ERROR)
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.assertEqual(self.b.unjoin_calls, 0)
        self.assertTrue(self.player.status_snapshot()['sonos']['group_cleanup_pending'])
        with self.assertRaises(RuntimeError):
            self.player.reset()

    def test_member_identity_mismatch_never_unjoins(self):
        from sonos_group import release, unresolved_members
        self.start()
        self.b.uid = 'SPOOFED'
        result = release(self.a, {'B': self.b}, owned_url=self.a.uri)
        self.assertNotEqual(result['B'], 'released')
        self.assertEqual(self.b.unjoin_calls, 0)
        self.assertIn('B', unresolved_members({'B': self.b}, result))

    def test_successful_release_has_no_pending_cleanup(self):
        self.start()
        self.player.stop()
        self.assertFalse(self.player.status_snapshot()['sonos']['group_cleanup_pending'])
        self.assertEqual(self.player._joined, {})

    def test_track_switch_new_uri_failure_preserves_group(self):
        self.start()
        previous_uri = self.a.uri
        def fail_new_uri(url, title):
            if url != previous_uri:
                raise OSError('ambiguous new track write')
        self.a.play_uri = fail_new_uri
        with self.assertRaises(OSError):
            self.player.change('next')
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.assertEqual(self.b.unjoin_calls, 0)
        self.assertTrue(self.player._group_uncertain)
        self.assertIsNone(self.player._recovery_url)
        self.assertTrue(self.player.status_snapshot()['sonos']['group_cleanup_pending'])
        self.assertEqual(self.player.state.mode, Mode.ERROR)
        with self.assertRaises(ValueError):
            self.player.play('A', ['B'])
        with self.assertRaises(RuntimeError):
            self.player.reset()

    def test_track_switch_preflight_failure_keeps_group(self):
        self.start()
        original = self.player._verify_retained_group
        calls = [0]
        def fail_on_recheck(uid):
            calls[0] += 1
            if calls[0] >= 2:
                raise RuntimeError('topology unreadable')
            return original(uid)
        self.player._verify_retained_group = fail_on_recheck
        with self.assertRaises(RuntimeError):
            self.player.change('next')
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.assertEqual(self.b.unjoin_calls, 0)
        self.assertTrue(self.player._group_uncertain)

    def test_track_switch_does_not_detach_when_stream_server_fails(self):
        self.start()
        self.server.start = lambda track: (_ for _ in ()).throw(OSError('reader unavailable'))
        with self.assertRaises(OSError):
            self.player.change('next')
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.assertEqual(self.b.unjoin_calls, 0)
        self.assertTrue(self.player._group_uncertain)

    def test_paused_stop_releases_exact_owned_group(self):
        self.start()
        self.player.pause()
        self.player.stop()
        self.assertEqual(self.player.state.mode, Mode.STOPPED)
        self.assertEqual((self.a.stop_calls, self.b.unjoin_calls), (1, 1))
        self.assertEqual(self.topo.leaders['B'], 'B')
        self.assertFalse(self.player.status_snapshot()['sonos']['group_cleanup_pending'])

    def test_paused_stop_rejects_foreign_uri_without_writes(self):
        self.start()
        self.player.pause()
        self.a.uri = 'x-sonos-spotify:foreign'
        with self.assertRaisesRegex(RuntimeError, 'exact URI mismatch'):
            self.player.stop()
        self.assertEqual((self.a.stop_calls, self.b.unjoin_calls), (0, 0))
        self.assertEqual(self.topo.leaders['B'], 'A')

    def test_resume_rejects_foreign_uri_without_writes(self):
        self.start()
        self.player.pause()
        self.a.uri = 'x-sonos-spotify:foreign'
        with self.assertRaisesRegex(RuntimeError, 'exact URI mismatch'):
            self.player.resume()
        self.assertEqual(self.a.transport, 'PAUSED_PLAYBACK')
        self.assertEqual(self.player.state.mode, Mode.PAUSED)

    def test_paused_stop_rejects_external_group_member(self):
        self.start()
        self.player.pause()
        self.topo.leaders['C'] = 'A'
        with self.assertRaisesRegex(RuntimeError, 'unexpected topology'):
            self.player.stop()
        self.assertEqual((self.a.stop_calls, self.b.unjoin_calls), (0, 0))

    def test_stop_does_not_unjoin_after_member_release_failure(self):
        self.start()
        def fail_unjoin():
            self.b.unjoin_calls += 1
            raise OSError('simulated Sonos unjoin failure')
        self.b.unjoin = fail_unjoin
        self.player.stop()
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.assertTrue(self.player.status_snapshot()['sonos']['group_cleanup_pending'])
        self.assertEqual(self.player._joined.keys(), {'B'})
        with self.assertRaisesRegex(RuntimeError, 'group cleanup uncertain; manual recovery required'):
            self.player.play('A', ['C'])

    def test_shutdown_paused_owned_group_stops_and_releases(self):
        self.start()
        self.player.pause()
        result = self.player.shutdown_owned('stop_owned')
        self.assertEqual(result['action'], 'stopped_owned')
        self.assertEqual((self.a.stop_calls, self.b.unjoin_calls), (1, 1))
        self.assertEqual(self.topo.leaders['B'], 'B')

    def test_shutdown_changed_uri_leaves_group_untouched(self):
        self.start()
        self.a.uri = 'x-sonos-spotify:foreign'
        result = self.player.shutdown_owned('stop_owned')
        self.assertEqual(result['action'], 'not_stopped')
        self.assertEqual((self.a.stop_calls, self.b.unjoin_calls), (0, 0))
        self.assertEqual(self.topo.leaders['B'], 'A')

    def test_solo_play_unchanged(self):
        self.player.play('A')
        self.assertEqual(self.topo.leaders,{'A':'A','B':'B','C':'C'})

if __name__=='__main__':
    unittest.main()
