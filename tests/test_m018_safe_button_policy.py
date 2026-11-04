"""M0.18 policy tests: no RPC, GPIO, or Sonos dependencies."""
import unittest

from integrations.interfaces.safe_button_policy import plan_playpause, plan_stop


def snapshot(mode='STOPPED', *, owned=False, active=False, pending=False):
    return {
        'ok': True,
        'state': {
            'schema_version': 1,
            'player': {'mode': mode},
            'sonos': {'owned': owned, 'group_cleanup_pending': pending},
            'stream': {'active': active},
        },
    }


def target(selected=True):
    return {'ok': True, 'target_selection': {'selected': selected}}


class SafeButtonPolicyTests(unittest.TestCase):
    def test_stopped_requires_player_acknowledged_target(self):
        self.assertEqual(plan_playpause(snapshot(), target()), 'play-selected')
        self.assertIsNone(plan_playpause(snapshot(), target(False)))

    def test_pause_and_resume_require_owned_active_stream(self):
        self.assertEqual(plan_playpause(
            snapshot('PLAYING', owned=True, active=True), target(False)), 'pause')
        self.assertEqual(plan_playpause(
            snapshot('PAUSED', owned=True, active=True), target(False)), 'resume')
        for mode in ('PLAYING', 'PAUSED'):
            for owned, active in ((False, True), (True, False), (False, False)):
                with self.subTest(mode=mode, owned=owned, active=active):
                    self.assertIsNone(plan_playpause(
                        snapshot(mode, owned=owned, active=active), target()))

    def test_pending_cleanup_blocks_every_command(self):
        for mode, owned, active in (
            ('STOPPED', False, False),
            ('PLAYING', True, True),
            ('PAUSED', True, True),
        ):
            self.assertIsNone(plan_playpause(
                snapshot(mode, owned=owned, active=active, pending=True), target()))

    def test_transitional_error_and_no_disc_modes_fail_closed(self):
        for mode in ('STARTING', 'ERROR', 'NO_DISC', 'UNKNOWN'):
            with self.subTest(mode=mode):
                self.assertIsNone(plan_playpause(snapshot(mode), target()))

    def test_rejected_or_malformed_status_never_authorizes(self):
        invalid = [None, [], {}, {'ok': False}, {'ok': True},
                   {'ok': True, 'state': {'schema_version': 1}}]
        for item in invalid:
            with self.subTest(status=item):
                self.assertIsNone(plan_playpause(item, target()))
        for item in (None, [], {}, {'ok': False}, {'ok': True}):
            with self.subTest(target=item):
                self.assertIsNone(plan_playpause(snapshot(), item))
        broken = snapshot()
        broken['state']['schema_version'] = 2
        self.assertIsNone(plan_playpause(broken, target()))

    def test_unknown_ownership_or_stream_flags_fail_closed(self):
        for field, value in (('owned', None), ('owned', 1),
                             ('active', None), ('active', 1),
                             ('group_cleanup_pending', None)):
            snap = snapshot()
            container = 'stream' if field == 'active' else 'sonos'
            snap['state'][container][field] = value
            with self.subTest(field=field, value=value):
                self.assertIsNone(plan_playpause(snap, target()))

    def test_selection_flag_must_be_exact_true(self):
        for value in (None, 1, 'true', False):
            self.assertIsNone(plan_playpause(
                snapshot(), {'ok': True, 'target_selection': {'selected': value}}))

    def test_stop_only_for_owned_active_selected_playback(self):
        for mode in ('PLAYING', 'PAUSED'):
            self.assertEqual(plan_stop(
                snapshot(mode, owned=True, active=True), target()), 'stop')
        for mode, owned, active, pending, selected in (
                ('STOPPED', False, False, False, True),
                ('PLAYING', False, True, False, True),
                ('PAUSED', True, False, False, True),
                ('PLAYING', True, True, True, True),
                ('PLAYING', True, True, False, False),
                ('ERROR', True, True, False, True)):
            self.assertIsNone(plan_stop(
                snapshot(mode, owned=owned, active=active, pending=pending),
                target(selected)))

    def test_stop_fail_closed_on_bad_snapshots(self):
        for item in (None, {}, {'ok': False}, {'ok': True}):
            self.assertIsNone(plan_stop(item, target()))
            self.assertIsNone(plan_stop(snapshot('PLAYING', owned=True, active=True), item))


if __name__ == '__main__':
    unittest.main()
