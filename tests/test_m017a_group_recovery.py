"""Conservative recovery: only inspection without confirmation; no foreign writes."""
import unittest
from player_state import Mode
from player_service import handle_command
from test_m017a_group_lifecycle import LifecycleTests

class RecoveryTests(unittest.TestCase):
    setUp = LifecycleTests.setUp
    start = LifecycleTests.start
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

if __name__=='__main__':
    unittest.main()
