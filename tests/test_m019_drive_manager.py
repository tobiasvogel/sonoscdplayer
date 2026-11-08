"""M0.19 integration tests; all hardware/USB commands are replaced with fakes."""
import contextlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from drive_power import DrivePowerManager, load_config
from drive_idle_policy import IdleDrivePolicy


@contextlib.contextmanager
def unlocked(*args, **kwargs):
    yield True


class ConfigTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'player.toml'
            p.write_text(text, encoding='utf8')
            return load_config(p)

    def test_spin_down_is_disabled_when_directive_absent(self):
        c = self.load('[drive_power]\nstrategy = "auto"\nkeepalive_interval_seconds = 10\n')
        self.assertIsNone(c['idle_spin_down_seconds'])

    def test_five_minute_directive_is_parsed(self):
        c = self.load('[drive_power]\nstrategy = "auto"\nidle_spin_down_seconds = 300\n')
        self.assertEqual(c['idle_spin_down_seconds'], 300.0)

    def test_invalid_or_conflicting_configuration_rejected(self):
        for value in ('true', '-1', '20', '86401', '"300"'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load('[drive_power]\nidle_spin_down_seconds = ' + value + '\n')
        for text in (
            '[drive_power]\nenabled=false\nidle_spin_down_seconds=300\n',
            '[drive_power]\nstrategy="off"\nidle_spin_down_seconds=300\n',
            '[drive_power]\nstrategy="power_condition"\nidle_spin_down_seconds=300\n',
            '[drive_power]\nidle_spin_down_seconds=300\n'
            '[drive_power.mode_page_overrides]\nIDLE_A=1\n',
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load(text)


class PowerManagerTests(unittest.TestCase):
    def make_manager(self):
        now = [0.0]
        commands = []

        def runner(args, **kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        p = DrivePowerManager(
            '/dev/sr0',
            {'enabled': True, 'strategy': 'keepalive', 'interval': 10.0,
             'overrides': {}, 'idle_spin_down_seconds': 300.0},
            runner=runner, clock=lambda: now[0],
            active_strategy='keepalive', last_start=0.0)
        p.supported['start_stop_unit'] = True
        p.idle_policy = IdleDrivePolicy(idle_seconds=300, last_activity=0)
        return p, now, commands

    def test_normal_keepalive_until_deadline_then_exactly_one_stop(self):
        p, now, commands = self.make_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            for now[0] in range(10, 300, 10):
                p.tick(playback_active=False)
            self.assertEqual([c[1] for c in commands], ['--start'] * 29)
            now[0] = 300
            p.tick()
            now[0] = 400
            p.tick()
        self.assertEqual([c[1] for c in commands][-1], '--stop')
        self.assertEqual([c[1] for c in commands].count('--stop'), 1)
        self.assertTrue(p.idle_policy.spun_down)

    def test_playback_and_reader_suppress_spin_down(self):
        p, now, commands = self.make_manager()
        now[0] = 400
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            p.tick(playback_active=True)
            p.tick(reader_active=True)
        self.assertNotIn('--stop', [a[1] for a in commands])
        self.assertFalse(p.idle_policy.spun_down)

    def test_wake_requires_verified_disc_and_does_not_repeat_start(self):
        p, now, commands = self.make_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            self.assertTrue(p.activity())
            now[0] = 304
            self.assertFalse(p.wake(verify=lambda dev: False))
        self.assertTrue(p.idle_policy.play_must_wait)
        self.assertEqual([a[1] for a in commands], ['--stop', '--start'])
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            self.assertFalse(p.activity())
            self.assertFalse(p.wake(verify=lambda dev: dev == '/dev/sr0'))
        self.assertTrue(p.idle_policy.play_must_wait)
        self.assertTrue(p.idle_policy.wake_fault_latched)
        self.assertEqual([a[1] for a in commands], ['--stop', '--start'])
        self.assertTrue(p.snapshot(reader_active=False)['idle_wake_fault_latched'])

    def failing_wake_manager(self):
        p, now, commands = self.make_manager()

        def fail_wake(args, **kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=35 if '--start' in args else 0,
                                   stdout='', stderr='Transport error')
        p.runner = fail_wake
        return p, now, commands

    def test_transport_failure_latches_fault_and_activity_inside_cooldown_is_safe(self):
        p, now, commands = self.failing_wake_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            self.assertTrue(p.activity())
            self.assertFalse(p.wake(verify=lambda dev: True))
            for tick in (301, 310, 340, 359):   # cooldown is 60 s
                now[0] = tick
                self.assertFalse(p.activity())
                self.assertFalse(p.wake(verify=lambda dev: True))
                p.tick()
        self.assertEqual([a[1] for a in commands], ['--stop', '--start'])
        self.assertTrue(p.idle_policy.wake_fault_latched)
        self.assertIn('Transport error', p.last_error)

    def test_retry_only_after_cooldown_and_only_on_user_activity(self):
        p, now, commands = self.failing_wake_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            p.activity()
            p.wake(verify=lambda dev: True)
            # No activity: ticks alone never retry, however long we wait.
            for now[0] in (400, 500, 900):
                p.tick()
            self.assertEqual([a[1] for a in commands], ['--stop', '--start'])
            self.assertEqual(p.snapshot(reader_active=False)['idle_wake_retry_in_seconds'], 0)
            now[0] = 361   # 61 s after the failure at t=300
            self.assertTrue(p.activity())
            self.assertFalse(p.wake(verify=lambda dev: True))
        self.assertEqual([a[1] for a in commands], ['--stop', '--start', '--start'])

    def test_retries_are_bounded_and_then_final(self):
        p, now, commands = self.failing_wake_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            for now[0] in (300, 400, 500, 600, 1000, 5000):
                if p.activity():
                    p.wake(verify=lambda dev: True)
        self.assertEqual([a[1] for a in commands].count('--start'), 3)
        snap = p.snapshot(reader_active=False)
        self.assertTrue(snap['idle_wake_fault_latched'])
        self.assertTrue(snap['idle_wake_retries_exhausted'])
        self.assertIsNone(snap['idle_wake_retry_in_seconds'])

    def test_successful_retry_clears_the_fault_and_resets_the_count(self):
        p, now, commands = self.failing_wake_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            p.activity()
            p.wake(verify=lambda dev: True)
            p.runner = lambda args, **kw: SimpleNamespace(returncode=0, stdout='', stderr='')
            now[0] = 400
            self.assertTrue(p.activity())
            self.assertTrue(p.wake(verify=lambda dev: True))
        policy = p.idle_policy
        self.assertFalse(policy.wake_fault_latched)
        self.assertFalse(policy.play_must_wait)
        self.assertEqual((policy.wake_failures, policy.fault_at), (0, None))

    def test_failed_verification_after_retry_counts_as_a_failure(self):
        p, now, commands = self.failing_wake_manager()
        p.runner = lambda args, **kw: SimpleNamespace(returncode=0, stdout='', stderr='')
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            p.activity()
            self.assertFalse(p.wake(verify=lambda dev: False))   # disc differs
            now[0] = 400
            self.assertTrue(p.activity())
            self.assertFalse(p.wake(verify=lambda dev: False))
        self.assertEqual(p.idle_policy.wake_failures, 2)
        self.assertTrue(p.idle_policy.play_must_wait)

    def test_clear_fault_after_eject_restores_a_clean_state(self):
        p, now, commands = self.failing_wake_manager()
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            p.activity()
            p.wake(verify=lambda dev: True)
        now[0] = 320
        p.clear_fault_after_eject()
        policy = p.idle_policy
        self.assertFalse(any((policy.wake_fault_latched, policy.spun_down, policy.stop_attempted,
                              policy.wake_in_progress, policy.play_must_wait)))
        self.assertEqual((policy.wake_failures, policy.fault_at, policy.last_activity), (0, None, 320))
        self.assertIsNone(p.last_error)

    def test_stop_error_does_not_keepalive_or_retry_blindly(self):
        p, now, commands = self.make_manager()
        def fail_stop(args, **kwargs):
            commands.append(args)
            return SimpleNamespace(returncode=(1 if '--stop' in args else 0),
                                   stdout='', stderr='rejected')
        p.runner = fail_stop
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            now[0] = 600
            p.tick()
        self.assertEqual([a[1] for a in commands], ['--stop'])
        self.assertFalse(p.idle_policy.spun_down)
        self.assertTrue(p.idle_policy.stop_attempted)

    def test_busy_device_lock_sends_no_stop_and_does_not_block_play(self):
        from player_service import idle_stop_unverified

        @contextlib.contextmanager
        def busy(*args, **kwargs):
            yield False

        p, now, commands = self.make_manager()
        now[0] = 300
        with mock.patch('drive_power.drive_lock', side_effect=busy):
            p.tick()
        self.assertEqual(commands, [])
        self.assertFalse(idle_stop_unverified(p))
        self.assertFalse(p.idle_policy.play_must_wait)
        # The next idle check may try again once the lock is free.
        now[0] = 301
        with mock.patch('drive_power.drive_lock', side_effect=unlocked):
            p.tick()
        self.assertEqual([c[1] for c in commands], ['--stop'])
        self.assertTrue(p.idle_policy.spun_down)


if __name__ == '__main__':
    unittest.main()
