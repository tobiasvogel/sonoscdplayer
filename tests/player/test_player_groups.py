"""Temporary Sonos groups: staging, lifecycle, recovery and controller ownership (simulated)."""
import unittest
from types import SimpleNamespace as NS, SimpleNamespace
from unittest import mock
UID = 'RINCON_REVIEW_COORD'
MEMBER_UID = 'RINCON_REVIEW_MEMBER'
URL = 'http://192.0.2.10:8765/track/1.wav'
FOREIGN_URL = 'https://example.invalid/foreign.mp3'
from sonoscdplayer.sonos import groups
from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import PlayerState, Mode
from sonoscdplayer.player.service import handle_command
from sonoscdplayer.sonos.target_selection import SelectedTarget
from sonoscdplayer.sonos.groups import (
    release,
    unresolved_members,
)
from sonoscdplayer.ui.menu.speakers.selection_plan import plan_from_applied



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
    def start(self, track, *, session_id=None): self.running=True
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

    def test_pause_goes_along_with_a_speaker_added_in_app(self):
        self.start()
        self.topo.leaders['C'] = 'A'
        self.player.pause()
        self.assertEqual(self.a.transport, 'PAUSED_PLAYBACK')
        self.assertEqual(self.player.state.mode, Mode.PAUSED)
        self.assertEqual(set(self.player._joined), {'B', 'C'})

    def test_resume_goes_along_with_a_speaker_added_in_app(self):
        self.start()
        self.player.pause()
        self.topo.leaders['C'] = 'A'
        self.player.resume()
        self.assertEqual(self.a.transport, 'PLAYING')
        self.assertEqual(set(self.player._joined), {'B', 'C'})

    def test_menu_adds_and_removes_members_without_interrupting(self):
        self.start()
        self.player.retarget('A', ['C'])
        self.assertEqual(self.topo.leaders, {'A': 'A', 'B': 'B', 'C': 'A'})
        self.assertEqual((self.a.stop_calls, self.a.play_calls), (0, 1))
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.player.stop()
        self.assertEqual(self.topo.leaders, {'A': 'A', 'B': 'B', 'C': 'C'})

    def test_menu_rejects_a_busy_new_member_and_plays_on(self):
        self.start()
        self.c.transport = 'PLAYING'                 # C plays something else
        with self.assertRaisesRegex(RuntimeError, 'active transport'):
            self.player.retarget('A', ['B', 'C'])
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.assertEqual(self.topo.leaders['C'], 'C')

    def test_menu_new_coordinator_restarts_the_track_there(self):
        self.start()
        self.player.retarget('C', [])
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.c.play_calls, 1)
        self.assertEqual(self.player.uid, 'C')
        self.assertEqual(self.player.state.mode, Mode.STARTING)
        self.assertEqual(self.topo.leaders, {'A': 'A', 'B': 'B', 'C': 'C'})

    def test_selection_keeps_the_playing_coordinator(self):
        self.start()
        selection = SelectedTarget(verify=lambda uid: None,
                                   find_many=lambda uids: {u: self.topo.devices[u] for u in uids})
        selection.set(self.player, 'B', ['A', 'C'])      # menu order put B first
        self.assertEqual(selection.speakers(), ['A', 'B', 'C'])
        self.assertEqual(self.a.stop_calls, 0)
        self.assertEqual(self.topo.leaders['C'], 'A')

    def test_selection_follows_a_group_change_in_the_app(self):
        seen = []
        self.player.selection_hook = lambda uid, members: seen.append((uid, members))
        self.start()
        self.topo.leaders['C'] = 'A'
        self.player.refresh()
        self.player.refresh()                       # no second report for the same group
        self.assertEqual(seen, [('A', ('B', 'C'))])

    def test_paused_speaker_counts_as_idle_for_a_new_playback(self):
        self.b.transport = 'PAUSED_PLAYBACK'        # paused queue on B
        self.player.play('A', ['B'])
        self.assertEqual(self.topo.leaders['B'], 'A')

    def test_stop_releases_the_group_before_emptying_the_queue(self):
        """Removing our disc from the queue changes the coordinator's URI;
        the group must already be released by then (else EJECT stays blocked)."""
        self.start()
        self.player._queue_block = {'first': 1, 'tracks': [1], 'urls': [self.a.uri]}
        def empty_queue(player, block):
            player.uri = ''
            return 1
        with mock.patch('sonoscdplayer.player.controller.remove_queue_block', empty_queue):
            self.player.stop()
        self.assertEqual(self.topo.leaders['B'], 'B')
        self.assertEqual(self.player._joined, {})
        self.assertFalse(self.player._group_uncertain)

    def test_track_change_between_two_reads_is_not_an_error(self):
        """At a track change Sonos can report the next track of our block
        on the second read of a check; the next refresh follows it."""
        self.start()
        first, second = self.a.uri, self.a.uri.replace('/track/1.wav', '/track/2.wav')
        self.player._queue_block = {'first': 1, 'tracks': [1, 2], 'urls': [first, second]}
        reads = iter([first, second])                       # moves on during the check
        self.a.get_current_track_info = lambda: {'uri': next(reads, second)}
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.player.refresh()
        self.assertEqual(self.player.owned_url, second)
        self.assertEqual(self.player.state.selected_track, 2)

    def test_foreign_uri_still_fails(self):
        self.start()
        self.a.uri = 'https://example.invalid/radio'
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.ERROR)

    def test_stop_releases_an_idle_group_even_without_stream_proof(self):
        """Seen on the device: after STOP the coordinator no longer showed our
        URI, every member was kept and PLAY, EJECT and spin-down were blocked."""
        self.start()
        original_stop = self.a.stop
        def stop_and_forget_uri():
            original_stop()
            self.a.uri = ''
        self.a.stop = stop_and_forget_uri
        self.player.stop()
        self.assertEqual(self.topo.leaders['B'], 'B')
        self.assertEqual((self.player._joined, self.player._group_uncertain), ({}, False))

    def test_pending_group_that_plays_something_stays_pending(self):
        self.start()
        self.player._release_group(owned_url='http://other.invalid/x')   # kept
        self.assertTrue(self.player._group_uncertain)
        self.player.state = self.player.state.natural_end()
        self.player.owned_url = None
        self.a.transport = 'PLAYING'                     # someone plays on the group now
        self.assertFalse(self.player.resolve_idle_group())
        self.assertEqual(self.topo.leaders['B'], 'A')
        self.a.transport = 'STOPPED'
        self.assertTrue(self.player.resolve_idle_group())
        self.assertEqual(self.topo.leaders['B'], 'B')

    def test_pending_group_whose_members_left_on_their_own_is_cleared(self):
        self.start()
        self.player._release_group(owned_url='http://other.invalid/x')   # kept
        self.player.state = self.player.state.natural_end()
        self.player.owned_url = None
        self.topo.leaders['B'] = 'B'                     # left in the Sonos app
        self.assertTrue(self.player.resolve_idle_group())
        self.assertFalse(self.player._group_uncertain)

    def test_recovery_updates_the_stored_playback_group(self):
        """Audit SON-002: after recovery the store must not keep old members."""
        saved = []
        self.player.group_store = SimpleNamespace(save=saved.append, load=lambda: frozenset())
        self.start()
        self.player._release_group(owned_url='http://other.invalid/x')   # kept
        self.player.state = self.player.state.natural_end()
        self.player.owned_url = None
        self.topo.leaders['B'] = 'B'                      # left on its own
        self.player.resolve_group_recovery()
        self.assertEqual(saved[-1], set())

    def test_speaker_removed_in_app_is_no_longer_released(self):
        self.start()
        self.topo.leaders['B'] = 'B'                # taken out in the Sonos app
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.player.stop()
        self.assertEqual(self.b.unjoin_calls, 0)

    def test_playback_moved_to_a_member_is_followed(self):
        """The app takes the coordinator out; Sonos plays on on B."""
        self.start()
        self.topo.leaders.update({'A': 'A', 'B': 'B'})
        self.b.uri, self.b.transport = self.a.uri, 'PLAYING'
        self.a.transport = 'STOPPED'
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.PLAYING)
        self.assertEqual(self.player.uid, 'B')
        self.assertTrue(self.server.alive())

    def test_unrelated_group_change_still_fails_closed(self):
        self.start()
        self.topo.leaders.update({'A': 'C', 'B': 'C'})   # A joined someone else's group
        self.player.refresh()
        self.assertEqual(self.player.state.mode, Mode.ERROR)

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
    def test_speaker_added_in_app_is_released_at_stop(self):
        self.start()
        self.topo.leaders['C']='A'                  # added in the Sonos app
        self.player.stop()
        self.assertEqual(self.a.stop_calls,1)
        self.assertEqual(self.topo.leaders,{'A':'A','B':'B','C':'C'})
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
        from sonoscdplayer.sonos.groups import release, unresolved_members
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

    def test_paused_stop_releases_a_speaker_added_in_app(self):
        self.start()
        self.player.pause()
        self.topo.leaders['C'] = 'A'
        self.player.stop()
        self.assertEqual(self.a.stop_calls, 1)
        self.assertEqual(self.topo.leaders, {'A': 'A', 'B': 'B', 'C': 'C'})

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


_LifecycleFixture = LifecycleTests


class RecoveryTests(unittest.TestCase):
    setUp = _LifecycleFixture.setUp
    start = _LifecycleFixture.start
    def test_partial_unjoin_failure_requires_confirmation(self):
        self.start()
        self.b.unjoin = lambda: (_ for _ in ()).throw(OSError('offline'))
        self.player.stop()
        self.assertTrue(self.player.status_snapshot()['sonos']['group_cleanup_pending'])
        self.assertEqual(self.player.inspect_group_recovery()['state'], 'attached')
        self.assertEqual(self.topo.leaders['B'], 'A')
        bad = handle_command(self.player, {'command':'resolve-group-recovery'})
        self.assertFalse(bad['ok'])
        self.assertEqual(self.topo.leaders['B'], 'A')

    def test_confirmed_release_finishes_recovery(self):
        self.start()
        original = self.b.unjoin
        self.b.unjoin = lambda: (_ for _ in ()).throw(OSError('offline'))
        self.player.stop()
        self.b.unjoin = original
        reply = handle_command(self.player, {'command':'resolve-group-recovery','confirm':True})
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(self.topo.leaders['B'], 'B')
        self.assertEqual(reply['group_recovery']['state'], 'clear')

    def test_external_manual_detach_can_clear_without_write(self):
        self.start()
        self.b.unjoin = lambda: (_ for _ in ()).throw(OSError('offline'))
        self.player.stop()
        self.topo.leaders['B'] = 'B'
        before = self.b.unjoin_calls
        status = self.player.inspect_group_recovery()
        self.assertEqual(status['state'], 'clear')
        self.assertEqual(self.b.unjoin_calls, before)
        reply = handle_command(self.player, {'command':'resolve-group-recovery','confirm':True})
        self.assertTrue(reply['ok'], reply)
        self.assertFalse(self.player._group_uncertain)
        self.assertEqual(self.b.unjoin_calls, before)

    def test_foreign_group_change_blocks_resolve(self):
        self.start()
        self.b.unjoin = lambda: (_ for _ in ()).throw(OSError('offline'))
        self.player.stop()
        self.topo.leaders['C'] = 'A'
        self.assertEqual(self.player.inspect_group_recovery()['state'],'blocked')
        reply = handle_command(self.player, {'command':'resolve-group-recovery','confirm':True})
        self.assertFalse(reply['ok'])
        self.assertEqual(self.topo.leaders['B'],'A')

    def test_unknown_play_uri_outcome_not_safe_to_detach(self):
        self.a.play_uri = lambda url,title: (_ for _ in ()).throw(OSError('unknown'))
        with self.assertRaises(OSError):
            self.player.play('A',['B'])
        self.assertFalse(self.player.inspect_group_recovery()['can_resolve'])
        reply=handle_command(self.player,{'command':'resolve-group-recovery','confirm':True})
        self.assertFalse(reply['ok'])
        self.assertEqual(self.topo.leaders['B'],'A')

    def test_inspection_is_read_only_and_does_not_expose_uid(self):
        self.start()
        self.b.unjoin = lambda: (_ for _ in ()).throw(OSError('offline'))
        self.player.stop()
        reply=handle_command(self.player,{'command':'inspect-group-recovery'})
        self.assertTrue(reply['ok'],reply)
        self.assertNotIn('B',str(reply['group_recovery']))
        self.assertEqual(self.topo.leaders['B'],'A')


class FakeController:
    state=NS(mode=Mode.STOPPED)
    owned_url=None
    def status(self): return 'stopped'
    def status_snapshot(self): return {'schema_version':1}


class StagingSpeaker:
    def __init__(self, uid, group=None):
        self.uid=uid
        self._group=group
        self.transport='STOPPED'
        self.unjoins=0
        self.raise_unjoin=False
    @property
    def group(self): return self._group
    def get_current_transport_info(self): return {'current_transport_state':self.transport}
    def get_current_track_info(self): return {'uri':'own'}
    def unjoin(self):
        if self.raise_unjoin: raise OSError('temporary failure')
        self.unjoins+=1
        self._group=NS(coordinator=self,members=(self,))


class StagedGroupTests(unittest.TestCase):
    def test_group_intent_from_solo_checkboxes(self):
        a=NS(key='A',members=('A',))
        b=NS(key='B',members=('B',))
        self.assertEqual(plan_from_applied((a,b),{'A','B'}).members,('B',))

    def test_grouped_speaker_is_left_to_the_player(self):
        # The player checks groups live (and splits leftovers of its own).
        a=NS(key='A',members=('A','B'))
        self.assertEqual(plan_from_applied((a,),{'A'}).coordinator,'A')

    def test_set_target_preflights_all_members_without_join(self):
        member=StagingSpeaker('B')
        member._group=NS(coordinator=member,members=(member,))
        checked=[]
        sel=SelectedTarget(verify=lambda uid:checked.append(uid),
                           find_many=lambda uids:{'B':member})
        reply=handle_command(FakeController(),{'command':'set-target','uid':'A','members':['B']},sel)
        self.assertTrue(reply['ok'],reply)
        self.assertEqual(checked,['A'])
        self.assertEqual(sel.member_uids(),('B',))
        self.assertNotIn('B',str(reply))
        self.assertEqual(member.unjoins,0)

    def test_play_with_members_is_forbidden(self):
        sel=SelectedTarget(verify=lambda uid:True)
        reply=handle_command(FakeController(),{'command':'play','uid':'A','members':['B']},sel)
        self.assertFalse(reply['ok'])
        self.assertIsNone(sel.coordinator_uid())

    def test_member_of_a_playing_group_rejected_without_mutation(self):
        member=StagingSpeaker('B')
        coord=StagingSpeaker('Z')
        coord.transport='PLAYING'
        member._group=NS(coordinator=coord,members=(coord,member))
        sel=SelectedTarget(verify=lambda uid:True,find_many=lambda uids:{'B':member})
        reply=handle_command(FakeController(),{'command':'set-target','uid':'A','members':['B']},sel)
        self.assertFalse(reply['ok'])
        self.assertIsNone(sel.coordinator_uid())
        self.assertEqual(member.unjoins,0)

    def test_chosen_member_is_taken_out_of_an_idle_group(self):
        for transport in ('STOPPED','PAUSED_PLAYBACK'):
            with self.subTest(transport=transport):
                member=StagingSpeaker('B')
                coord=StagingSpeaker('Z')
                coord.transport=transport
                member._group=NS(coordinator=coord,members=(coord,member))
                sel=SelectedTarget(verify=lambda uid:True,find_many=lambda uids:{'B':member})
                reply=handle_command(FakeController(),{'command':'set-target','uid':'A','members':['B']},sel)
                self.assertTrue(reply['ok'])
                self.assertEqual(member.unjoins,1)
                self.assertEqual(sel.speakers(),['A','B'])

    def test_uncertain_release_remains_tracked(self):
        a,b=StagingSpeaker('A'),StagingSpeaker('B')
        group=NS(coordinator=a,members=(a,b))
        a._group=b._group=group
        b.raise_unjoin=True
        result=release(a,{'B':b},owned_url='own')
        self.assertIn('B',unresolved_members({'B':b},result))
        self.assertEqual(b.unjoins,0)

    def test_foreign_expansion_prevents_release(self):
        a,b,c=StagingSpeaker('A'),StagingSpeaker('B'),StagingSpeaker('C')
        group=NS(coordinator=a,members=(a,b,c))
        a._group=b._group=c._group=group
        result=release(a,{'B':b},owned_url='own')
        self.assertEqual(result['B'],'kept: topology changed')
        self.assertEqual(b.unjoins,0)


class FakeSonos:
    def __init__(self, uid=UID, *, transport='PLAYING', uri=URL):
        self.uid = uid
        self.transport = transport
        self.uri = uri
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.operations = []
        self.transport_reads = 0
        self.uri_reads = 0
        self.on_transport_read = None
        self.on_uri_read = None

    def get_current_transport_info(self):
        self.transport_reads += 1
        if self.on_transport_read:
            self.on_transport_read(self.transport_reads)
        return {'current_transport_state': self.transport}

    def get_current_track_info(self):
        self.uri_reads += 1
        if self.on_uri_read:
            self.on_uri_read(self.uri_reads)
        return {'uri': self.uri}

    def pause(self):
        self.operations.append('pause')

    def play(self):
        self.operations.append('play')

    def stop(self):
        self.operations.append('stop')

    def unjoin(self):
        self.operations.append('unjoin')


class FakeServer:
    def __init__(self):
        self.running = True
        self.closed = False

    def alive(self):
        return self.running

    def close(self):
        self.closed = True
        self.running = False


def owned_controller(mode=Mode.PLAYING, player=None):
    speaker = player or FakeSonos(transport=(
        'PAUSED_PLAYBACK' if mode == Mode.PAUSED else 'PLAYING'))
    server = FakeServer()
    ctrl = Controller(PlayerState(mode, (1,), 1), UID, '192.0.2.10',
                      8765, server)
    ctrl.player = speaker
    ctrl.owned_url = URL
    ctrl.seen_playing = True
    ctrl._group_members = frozenset()
    return ctrl, speaker, server


class OwnershipChangeTests(unittest.TestCase):
    def test_foreign_uri_between_two_checks_blocks_pause_resume_stop(self):
        for mode, action in ((Mode.PLAYING, 'pause'),
                             (Mode.PAUSED, 'resume'),
                             (Mode.PLAYING, 'stop'),
                             (Mode.PAUSED, 'stop')):
            with self.subTest(mode=mode, action=action):
                ctrl, speaker, server = owned_controller(mode)
                speaker.on_uri_read = lambda n: setattr(
                    speaker, 'uri', FOREIGN_URL) if n == 2 else None
                with self.assertRaises(RuntimeError):
                    getattr(ctrl, action)()
                self.assertEqual(speaker.operations, [])
                self.assertEqual(speaker.uri_reads, 2)
                self.assertFalse(server.closed)

    def test_foreign_transport_before_command_blocks_mutation(self):
        for mode, action in ((Mode.PLAYING, 'pause'),
                             (Mode.PAUSED, 'resume'),
                             (Mode.PLAYING, 'stop')):
            with self.subTest(mode=mode, action=action):
                ctrl, speaker, _ = owned_controller(mode)
                speaker.on_transport_read = lambda n: setattr(
                    speaker, 'transport', 'STOPPED') if n == 2 else None
                with self.assertRaises(RuntimeError):
                    getattr(ctrl, action)()
                self.assertEqual(speaker.operations, [])

    def test_changed_group_topology_blocks_stop_without_unjoin(self):
        ctrl, speaker, server = owned_controller()
        other = FakeSonos(uid='RINCON_FOREIGN')
        # Change the topology after proof #1, before proof #2 begins.
        speaker.on_uri_read = lambda n: setattr(
            speaker.group, 'members', [speaker, other]) if n == 1 else None
        with self.assertRaises(RuntimeError):
            ctrl.stop()
        self.assertEqual(speaker.operations, [])
        self.assertFalse(server.closed)

    def test_wrong_uid_blocks_without_side_effects(self):
        ctrl, speaker, _ = owned_controller()
        speaker.uid = 'RINCON_FOREIGN'
        with self.assertRaises(RuntimeError):
            ctrl.stop()
        self.assertEqual(speaker.operations, [])

    def test_shutdown_refuses_changed_uri_and_does_not_stop(self):
        ctrl, speaker, _ = owned_controller()
        speaker.on_uri_read = lambda n: setattr(
            speaker, 'uri', FOREIGN_URL) if n == 2 else None
        result = ctrl.shutdown_owned()
        self.assertEqual(result['action'], 'not_stopped')
        self.assertEqual(speaker.operations, [])

    def test_player_service_command_ignores_stale_button_plan(self):
        # A button may have read owned PLAYING, but the actual controller
        # must independently reject a foreign URI before a later STOP.
        from sonoscdplayer.player.service import handle_command
        ctrl, speaker, _ = owned_controller()
        speaker.uri = FOREIGN_URL
        reply = handle_command(ctrl, {'command': 'stop'})
        self.assertIs(reply['ok'], False)
        self.assertEqual(speaker.operations, [])


class TrackChangeOwnershipTests(unittest.TestCase):
    """A track button may not stop an unknown Sonos session."""

    def test_foreign_uri_race_blocks_track_change_without_stop(self):
        for mode, direction in ((Mode.PLAYING, 'next'),
                                (Mode.PAUSED, 'previous')):
            with self.subTest(mode=mode, direction=direction):
                ctrl, speaker, server = owned_controller(mode)
                # Choose a non-boundary track in both directions.
                ctrl.state = PlayerState(mode, (1, 2, 3), 2)
                speaker.on_uri_read = lambda n: setattr(
                    speaker, 'uri', FOREIGN_URL) if n == 2 else None
                with self.assertRaises(RuntimeError):
                    ctrl.change(direction)
                self.assertEqual(speaker.operations, [])
                self.assertFalse(server.closed)

    def test_foreign_transport_race_blocks_track_change(self):
        for mode in (Mode.PLAYING, Mode.PAUSED):
            with self.subTest(mode=mode):
                ctrl, speaker, server = owned_controller(mode)
                ctrl.state = PlayerState(mode, (1, 2, 3), 2)
                speaker.on_transport_read = lambda n: setattr(
                    speaker, 'transport', 'STOPPED') if n == 2 else None
                with self.assertRaises(RuntimeError):
                    ctrl.change('next')
                self.assertEqual(speaker.operations, [])
                self.assertFalse(server.closed)

    def test_changed_group_blocks_track_change(self):
        ctrl, speaker, server = owned_controller()
        ctrl.state = PlayerState(Mode.PLAYING, (1, 2, 3), 2)
        outsider = FakeSonos(uid='RINCON_FOREIGN')
        speaker.on_uri_read = lambda n: speaker.group.members.append(
            outsider) if n == 1 else None
        with self.assertRaises(RuntimeError):
            ctrl.change('next')
        self.assertEqual(speaker.operations, [])
        self.assertFalse(server.closed)


class GroupRecoveryTests(unittest.TestCase):
    def _group(self):
        coordinator = FakeSonos()
        member = FakeSonos(uid=MEMBER_UID)
        coordinator.group = SimpleNamespace(
            coordinator=coordinator, members=[coordinator, member])
        member.group = coordinator.group
        return coordinator, member

    def test_foreign_coordinator_uri_never_unjoins_member(self):
        coordinator, member = self._group()
        coordinator.uri = FOREIGN_URL
        outcome = groups.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        self.assertEqual(outcome[MEMBER_UID], 'kept: foreign playback')
        self.assertEqual(member.operations, [])

    def test_changed_group_topology_never_unjoins_member(self):
        coordinator, member = self._group()
        coordinator.group.members.append(FakeSonos(uid='RINCON_FOREIGN'))
        outcome = groups.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        self.assertEqual(outcome[MEMBER_UID], 'kept: topology changed')
        self.assertEqual(member.operations, [])

    def test_failed_member_unjoin_remains_pending(self):
        coordinator, member = self._group()
        def fail():
            raise TimeoutError('unjoin outcome unknown')
        member.unjoin = fail
        outcome = groups.release(
            coordinator, {MEMBER_UID: member}, owned_url=URL)
        remaining = groups.unresolved_members(
            {MEMBER_UID: member}, outcome)
        self.assertEqual(list(remaining), [MEMBER_UID])
        self.assertEqual(outcome[MEMBER_UID], 'kept: unjoin failed')

    def test_controller_does_not_recover_without_exact_owned_uri(self):
        coordinator, member = self._group()
        ctrl, _, _ = owned_controller()
        ctrl.state = PlayerState(Mode.STOPPED, (1,), 1)
        ctrl.player = None
        ctrl.owned_url = None
        ctrl._joined = {MEMBER_UID: member}
        ctrl._group_coord = coordinator
        ctrl._group_uncertain = True
        ctrl._recovery_url = URL
        coordinator.uri = FOREIGN_URL
        with mock.patch('sonoscdplayer.sonos.groups.inspect_recovery', return_value={
            'state': 'attached', 'standalone': (),
            'attached': (MEMBER_UID,), 'blocked': (),
        }), mock.patch('sonoscdplayer.sonos.groups.release') as release:
            with self.assertRaisesRegex(RuntimeError, 'ownership changed'):
                ctrl.resolve_group_recovery()
        release.assert_not_called()
        self.assertEqual(list(ctrl._joined), [MEMBER_UID])
        self.assertTrue(ctrl._group_uncertain)

    def test_blocked_recovery_never_mutates_group(self):
        coordinator, member = self._group()
        ctrl, _, _ = owned_controller()
        ctrl.state = PlayerState(Mode.STOPPED, (1,), 1)
        ctrl.player = None
        ctrl.owned_url = None
        ctrl._joined = {MEMBER_UID: member}
        ctrl._group_coord = coordinator
        ctrl._group_uncertain = True
        ctrl._recovery_url = URL
        with mock.patch('sonoscdplayer.sonos.groups.inspect_recovery', return_value={
            'state': 'blocked', 'standalone': (),
            'attached': (), 'blocked': (MEMBER_UID,),
        }), mock.patch('sonoscdplayer.sonos.groups.release') as release:
            with self.assertRaisesRegex(RuntimeError, 'blocked'):
                ctrl.resolve_group_recovery()
        release.assert_not_called()
        self.assertEqual(list(ctrl._joined), [MEMBER_UID])


if __name__ == '__main__':
    unittest.main()
