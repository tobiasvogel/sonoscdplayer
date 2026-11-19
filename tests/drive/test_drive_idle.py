"""Drive idle policy: when the drive may spin down, and STOP/EJECT guards while idle (offline)."""
import contextlib
import json
import multiprocessing
import socket
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock, patch
from sonoscdplayer.drive.idle_policy import IdleDrivePolicy
from sonoscdplayer.drive.power import DrivePowerManager, load_config
from sonoscdplayer.player.service import (
    disc_poll_allowed,
    eject_blocked_by_idle_policy,
    idle_stop_unverified,
    process_drive_activity,
    wake_before_new_play,
)
from sonoscdplayer.player.state import Mode
from sonoscdplayer.ui.display_service import request_status



class IdleDrivePolicyTests(unittest.TestCase):
    def test_idle_deadline_and_one_stop_per_idle_epoch(self):
        p = IdleDrivePolicy(last_activity=10)
        self.assertIsNone(p.idle_action(309, playback_active=False, reader_active=False))
        self.assertEqual(p.idle_action(310, playback_active=False, reader_active=False), 'stop')
        self.assertIsNone(p.idle_action(900, playback_active=False, reader_active=False))

    def test_active_playback_and_reader_never_stop(self):
        for playing, reading in ((True, False), (False, True), (True, True)):
            with self.subTest(playing=playing, reading=reading):
                p = IdleDrivePolicy(last_activity=0)
                self.assertIsNone(p.idle_action(600, playback_active=playing, reader_active=reading))
                self.assertIsNone(p.idle_action(601, playback_active=False, reader_active=False))

    def test_unknown_drive_or_cleanup_blocks_stop(self):
        p = IdleDrivePolicy()
        self.assertIsNone(p.idle_action(600, playback_active=False, reader_active=False, drive_ready=False))
        self.assertIsNone(p.idle_action(600, playback_active=False, reader_active=False, group_cleanup_pending=True))

    def test_stop_failure_does_not_claim_asleep_or_immediately_retry(self):
        p = IdleDrivePolicy()
        self.assertEqual(p.idle_action(301, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=False)
        self.assertFalse(p.spun_down)
        self.assertIsNone(p.idle_action(602, playback_active=False, reader_active=False))
        p.activity(603)
        # An unconfirmed STOP is not cleared by another button press.
        # No automatic motor retry is permitted, even after a new deadline.
        self.assertTrue(p.stop_attempted)
        self.assertFalse(p.spun_down)
        self.assertFalse(p.keepalive_allowed(604))
        self.assertIsNone(p.idle_action(902, playback_active=False, reader_active=False))
        self.assertIsNone(p.idle_action(903, playback_active=False, reader_active=False))
        self.assertTrue(p.stop_attempted)

    def test_keepalive_continues_every_interval_before_idle_deadline(self):
        p = IdleDrivePolicy(idle_seconds=300, last_activity=0)
        # Simulate existing 10-second cadence. Keepalive must never
        # extend the inactivity window by resetting last_activity.
        for seconds in range(10, 300, 10):
            with self.subTest(seconds=seconds):
                self.assertTrue(p.keepalive_allowed(seconds))
                self.assertIsNone(p.idle_action(
                    seconds, playback_active=False, reader_active=False))
        self.assertEqual(p.last_activity, 0)
        self.assertFalse(p.keepalive_allowed(300))
        self.assertEqual(p.idle_action(
            300, playback_active=False, reader_active=False), 'stop')
        self.assertFalse(p.keepalive_allowed(301))
        p.stop_result(acknowledged=True)
        self.assertFalse(p.keepalive_allowed(350))

    def test_new_user_activity_restarts_both_idle_and_keepalive_window(self):
        p = IdleDrivePolicy(idle_seconds=300, last_activity=0)
        self.assertTrue(p.keepalive_allowed(290))
        self.assertIsNone(p.activity(295))
        self.assertTrue(p.keepalive_allowed(590))
        self.assertFalse(p.keepalive_allowed(595))
        self.assertEqual(p.idle_action(
            595, playback_active=False, reader_active=False), 'stop')

    def test_uncertain_stop_disables_keepalive_without_blind_retry(self):
        p = IdleDrivePolicy(idle_seconds=300)
        self.assertEqual(p.idle_action(
            300, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=False)
        self.assertFalse(p.keepalive_allowed(301))
        self.assertIsNone(p.idle_action(
            900, playback_active=False, reader_active=False))

    def test_button_and_encoder_reset_idle_timer(self):
        p = IdleDrivePolicy()
        self.assertIsNone(p.activity(299))
        self.assertIsNone(p.idle_action(300, playback_active=False, reader_active=False))
        self.assertEqual(p.idle_action(599, playback_active=False, reader_active=False), 'stop')

    def test_first_play_after_stop_requires_wake_then_verified_drive(self):
        p = IdleDrivePolicy()
        self.assertEqual(p.idle_action(301, playback_active=False, reader_active=False), 'stop')
        p.stop_result(acknowledged=True)
        self.assertTrue(p.play_must_wait)
        self.assertEqual(p.activity(320), 'wake')
        self.assertIsNone(p.activity(321))
        self.assertTrue(p.play_must_wait)
        self.assertFalse(p.wake_result(325, drive_verified=False))
        self.assertTrue(p.play_must_wait)
        self.assertTrue(p.wake_fault_latched)
        self.assertIsNone(p.activity(330))  # No implicit retry after failure.
        self.assertTrue(p.play_must_wait)
        self.assertFalse(p.keepalive_allowed(331))
        self.assertIsNone(p.idle_action(999, playback_active=False,
                                        reader_active=False))

    def test_stop_while_waiting_to_wake_is_forbidden(self):
        p = IdleDrivePolicy()
        p.stop_result(acknowledged=True)
        self.assertEqual(p.activity(400), 'wake')
        self.assertIsNone(p.idle_action(900, playback_active=False, reader_active=False))
        self.assertTrue(p.play_must_wait)

    def test_config_bounds(self):
        for value in (False, 29, 86401, '300', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                IdleDrivePolicy(idle_seconds=value)
        self.assertEqual(IdleDrivePolicy(idle_seconds=300).idle_seconds, 300)


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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
            p.tick(playback_active=True)
            p.tick(reader_active=True)
        self.assertNotIn('--stop', [a[1] for a in commands])
        self.assertFalse(p.idle_policy.spun_down)

    def test_wake_requires_verified_disc_and_does_not_repeat_start(self):
        p, now, commands = self.make_manager()
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            self.assertTrue(p.activity())
            now[0] = 304
            self.assertFalse(p.wake(verify=lambda dev: False))
        self.assertTrue(p.idle_policy.play_must_wait)
        self.assertEqual([a[1] for a in commands], ['--stop', '--start'])
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
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
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
            now[0] = 300
            p.tick()
            now[0] = 600
            p.tick()
        self.assertEqual([a[1] for a in commands], ['--stop'])
        self.assertFalse(p.idle_policy.spun_down)
        self.assertTrue(p.idle_policy.stop_attempted)

    def test_busy_device_lock_sends_no_stop_and_does_not_block_play(self):
        from sonoscdplayer.player.service import idle_stop_unverified

        @contextlib.contextmanager
        def busy(*args, **kwargs):
            yield False

        p, now, commands = self.make_manager()
        now[0] = 300
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=busy):
            p.tick()
        self.assertEqual(commands, [])
        self.assertFalse(idle_stop_unverified(p))
        self.assertFalse(p.idle_policy.play_must_wait)
        # The next idle check may try again once the lock is free.
        now[0] = 301
        with mock.patch('sonoscdplayer.drive.power.drive_lock', side_effect=unlocked):
            p.tick()
        self.assertEqual([c[1] for c in commands], ['--stop'])
        self.assertTrue(p.idle_policy.spun_down)


class DiscIdleGuardTests(unittest.TestCase):

    def make_power(self, **overrides):
        flags = dict(
            stop_attempted=False,
            spun_down=False,
            wake_in_progress=False,
            wake_fault_latched=False,
        )
        flags.update(overrides)
        return SimpleNamespace(
            idle_policy=SimpleNamespace(**flags)
        )

    def test_disabled_feature_preserves_monitor(self):
        self.assertTrue(disc_poll_allowed(None))
        self.assertTrue(disc_poll_allowed(
            SimpleNamespace(idle_policy=None)
        ))

    def test_normal_idle_monitor_remains_enabled(self):
        self.assertTrue(disc_poll_allowed(self.make_power()))

    def test_stop_attempt_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(stop_attempted=True)
        ))

    def test_confirmed_spin_down_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(spun_down=True)
        ))

    def test_wake_in_progress_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(wake_in_progress=True)
        ))

    def test_wake_fault_blocks_media_probe(self):
        self.assertFalse(disc_poll_allowed(
            self.make_power(wake_fault_latched=True)
        ))

    def test_verified_wake_reenables_monitor(self):
        power = self.make_power(spun_down=True)
        self.assertFalse(disc_poll_allowed(power))
        power.idle_policy.spun_down = False
        self.assertTrue(disc_poll_allowed(power))


class UncertainStopTests(unittest.TestCase):

    def make_uncertain_power(self):
        policy = IdleDrivePolicy(
            idle_seconds=300,
            last_activity=0,
        )

        action = policy.idle_action(
            300,
            playback_active=False,
            reader_active=False,
        )
        self.assertEqual(action, 'stop')

        policy.stop_result(acknowledged=False)

        power = SimpleNamespace(idle_policy=policy)
        power.activity = Mock(
            side_effect=lambda: policy.activity(301)
        )
        power.wake = Mock(return_value=True)

        return power

    def test_button_does_not_clear_uncertain_stop(self):
        power = self.make_uncertain_power()

        power.activity()

        self.assertTrue(power.idle_policy.stop_attempted)
        self.assertFalse(power.idle_policy.spun_down)
        self.assertTrue(idle_stop_unverified(power))
        self.assertFalse(
            power.idle_policy.keepalive_allowed(302)
        )

    def test_uncertain_stop_does_not_retry_stop(self):
        power = self.make_uncertain_power()

        power.activity()

        action = power.idle_policy.idle_action(
            700,
            playback_active=False,
            reader_active=False,
        )

        self.assertIsNone(action)
        self.assertTrue(power.idle_policy.stop_attempted)

    def test_uncertain_stop_keeps_the_disc_removable(self):
        # PLAY stays blocked (tests below); EJECT may proceed to its own checks.
        power = self.make_uncertain_power()

        power.activity()

        self.assertFalse(
            eject_blocked_by_idle_policy(power)
        )

    def test_uncertain_stop_blocks_play_without_wake(self):
        power = self.make_uncertain_power()

        result = wake_before_new_play(
            power,
            object(),
            {'command': 'play-selected'},
            stopped=True,
        )

        self.assertFalse(result)
        power.wake.assert_not_called()

    def test_uncertain_stop_blocks_activity_without_wake(self):
        power = self.make_uncertain_power()

        result = process_drive_activity(
            power, object()
        )

        self.assertFalse(result)
        power.wake.assert_not_called()

    def test_acknowledged_stop_can_be_verified_and_cleared(self):
        policy = IdleDrivePolicy(
            idle_seconds=300, last_activity=0
        )

        self.assertEqual(
            policy.idle_action(
                300,
                playback_active=False,
                reader_active=False,
            ),
            'stop',
        )

        policy.stop_result(acknowledged=True)

        self.assertEqual(policy.activity(301), 'wake')
        self.assertTrue(policy.stop_attempted)
        self.assertTrue(policy.spun_down)

        self.assertTrue(
            policy.wake_result(302, drive_verified=True)
        )
        self.assertFalse(policy.stop_attempted)
        self.assertFalse(policy.spun_down)


class EjectIdlePolicyTests(unittest.TestCase):

    def power(self, **flags):
        defaults = {
            'stop_attempted': False,
            'spun_down': False,
            'wake_in_progress': False,
            'wake_fault_latched': False,
        }
        defaults.update(flags)
        return SimpleNamespace(
            idle_policy=SimpleNamespace(**defaults)
        )

    def test_disabled_idle_feature_keeps_eject(self):
        self.assertFalse(eject_blocked_by_idle_policy(None))
        self.assertFalse(eject_blocked_by_idle_policy(
            SimpleNamespace(idle_policy=None)
        ))

    def test_awake_drive_can_eject(self):
        self.assertFalse(
            eject_blocked_by_idle_policy(self.power())
        )

    def test_unconfirmed_stop_keeps_the_disc_removable(self):
        self.assertFalse(eject_blocked_by_idle_policy(
            self.power(stop_attempted=True)))

    def test_unconfirmed_stop_with_wake_in_progress_still_blocks(self):
        self.assertTrue(eject_blocked_by_idle_policy(
            self.power(stop_attempted=True, wake_in_progress=True)))

    def test_spun_down_drive_blocks_eject(self):
        # An acknowledged STOP is healthy: the button path wakes the drive first.
        for extra in ({}, {'stop_attempted': True}):
            with self.subTest(extra=extra):
                self.assertTrue(eject_blocked_by_idle_policy(
                    self.power(spun_down=True, **extra)))

    def test_wake_in_progress_blocks_eject(self):
        self.assertTrue(
            eject_blocked_by_idle_policy(
                self.power(wake_in_progress=True)
            )
        )

    def test_wake_fault_keeps_the_disc_removable(self):
        # A failed wake leaves the drive marked spun down; EJECT stays possible
        # because the eject path verifies drive and media itself.
        for extra in ({}, {'spun_down': True}, {'spun_down': True, 'stop_attempted': True}):
            with self.subTest(extra=extra):
                self.assertFalse(eject_blocked_by_idle_policy(
                    self.power(wake_fault_latched=True, **extra)))

    def test_wake_in_progress_blocks_even_with_a_latched_fault(self):
        self.assertTrue(eject_blocked_by_idle_policy(
            self.power(wake_fault_latched=True, wake_in_progress=True)))


class FakeController:
    def __init__(self):
        self.state = SimpleNamespace(mode=Mode.STOPPED)
        self.server = SimpleNamespace(
            reader_active=lambda: False,
            alive=lambda: False,
            device="/dev/fake-drive-eject",
        )
        self.disc_status = None
        self.owned_url = None
        self.player = None
        self._joined = False
        self._group_uncertain = False
        self.status_hook = None

    def status(self):
        return "STOPPED"

    def status_snapshot(self):
        return {"player": {"mode": "STOPPED"}}

    def refresh(self):
        pass


class FakeMonitor:
    def poll(self, controller):
        return False


class FakePower:
    def __init__(self, flags):
        self.idle_policy = (
            SimpleNamespace(**flags)
            if flags is not None else None
        )

    def snapshot(self, *, reader_active):
        policy = self.idle_policy
        return {
            "idle_stop_attempted": (
                bool(policy.stop_attempted) if policy else False
            ),
            "idle_spun_down": (
                bool(policy.spun_down) if policy else False
            ),
            "idle_waking": (
                bool(policy.wake_in_progress) if policy else False
            ),
            "idle_wake_fault_latched": (
                bool(policy.wake_fault_latched) if policy else False
            ),
        }

    def tick(self, **kwargs):
        pass

    def activity(self):
        raise AssertionError("EJECT must not request activity")

    def wake(self, **kwargs):
        raise AssertionError("EJECT must not start the motor")


def fake_service(path, flags, handler_calls):
    from sonoscdplayer.player.service import serve, response

    controller = FakeController()

    def fake_handle(controller, request, selection=None, eject=None):
        with handler_calls.get_lock():
            handler_calls.value += 1
        return response(controller, ok=True)

    def forbidden_eject(*args, **kwargs):
        raise AssertionError("actual EJECT must never run in this test")

    with patch(
        "sonoscdplayer.player.service.handle_command",
        side_effect=fake_handle,
    ), patch(
        "sonoscdplayer.player.service.eject_verified_idle",
        side_effect=forbidden_eject,
    ):
        serve(
            controller,
            path,
            poll_seconds=0.05,
            power=FakePower(flags),
            disc_monitor=FakeMonitor(),
        )


def rpc(path, command):
    with socket.socket(
        socket.AF_UNIX, socket.SOCK_STREAM
    ) as conn:
        conn.settimeout(3)
        conn.connect(path)
        conn.sendall(
            (json.dumps({"command": command}) + "\n").encode()
        )
        data = bytearray()
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                raise RuntimeError("socket closed before reply")
            data.extend(chunk)

    return json.loads(data.split(b"\n", 1)[0])


class EjectServeGuardTests(unittest.TestCase):

    def flags(self, **overrides):
        values = {
            "stop_attempted": False,
            "spun_down": False,
            "wake_in_progress": False,
            "wake_fault_latched": False,
        }
        values.update(overrides)
        return values

    def scenario(self, flags, *, allowed=False, command="eject"):
        ctx = multiprocessing.get_context("fork")
        handler_calls = ctx.Value("i", 0)

        with tempfile.TemporaryDirectory(
            prefix="drive-eject-guard-"
        ) as directory:
            path = str(Path(directory) / "control.sock")
            process = ctx.Process(
                target=fake_service,
                args=(path, flags, handler_calls),
                daemon=True,
            )
            process.start()

            try:
                deadline = time.monotonic() + 4.0
                while True:
                    if process.exitcode is not None:
                        self.fail(
                            f"fake service exited: {process.exitcode}"
                        )
                    try:
                        request_status(path, timeout=0.2)
                        break
                    except (OSError, ValueError, TimeoutError):
                        if time.monotonic() >= deadline:
                            self.fail("fake service did not start")
                        time.sleep(0.02)

                result = rpc(path, command)

                self.assertIs(
                    result["ok"], allowed,
                    result,
                )

                if allowed:
                    self.assertEqual(handler_calls.value, 1)
                else:
                    self.assertIn(
                        "EJECT blocked", result.get("error", "")
                    )
                    self.assertEqual(handler_calls.value, 0)

                self.assertIsNone(process.exitcode)

            finally:
                if process.is_alive():
                    process.terminate()
                process.join(3)
                if process.is_alive():
                    process.kill()
                    process.join(3)

    def test_unconfirmed_stop_still_reaches_the_eject_handler(self):
        self.scenario(
            self.flags(stop_attempted=True),
            allowed=True,
        )

    def test_sleeping_drive_blocks_eject(self):
        self.scenario(
            self.flags(stop_attempted=True, spun_down=True)
        )

    def test_wake_in_progress_blocks_eject(self):
        self.scenario(
            self.flags(wake_in_progress=True)
        )

    def test_wake_fault_still_reaches_the_eject_handler(self):
        # The handler (safe eject checks) is faked here; it decides on the real drive.
        self.scenario(
            self.flags(wake_fault_latched=True, spun_down=True),
            allowed=True,
        )

    def test_wake_in_progress_blocks_eject_even_with_a_fault(self):
        self.scenario(
            self.flags(wake_fault_latched=True, wake_in_progress=True)
        )

    def test_awake_drive_reaches_the_eject_handler(self):
        # The eject handler itself is replaced by a fake.
        # Its detailed safety checks have their own tests.
        self.scenario(self.flags(), allowed=True)

    def test_disabled_idle_feature_keeps_the_eject_route(self):
        self.scenario(None, allowed=True)

    def test_normalized_eject_is_also_blocked(self):
        self.scenario(
            self.flags(spun_down=True),
            command=" EJECT ",
        )


class DriveActivityRpcTests(unittest.TestCase):

    def make_power(self, *, waiting=False, wake_result=True):
        policy = SimpleNamespace(play_must_wait=waiting)
        power = SimpleNamespace(idle_policy=policy)
        power.activity = Mock()
        power.wake = Mock(return_value=wake_result)
        return power

    def test_disabled_feature_does_nothing(self):
        self.assertTrue(process_drive_activity(None, None))
        self.assertTrue(process_drive_activity(
            SimpleNamespace(idle_policy=None), None
        ))

    def test_normal_activity_only_updates_timer(self):
        power = self.make_power()
        self.assertTrue(process_drive_activity(power, None))
        power.activity.assert_called_once()
        power.wake.assert_not_called()

    def test_sleeping_drive_requires_monitor(self):
        power = self.make_power(waiting=True)
        self.assertFalse(process_drive_activity(power, None))
        power.wake.assert_not_called()

    def test_verified_wake_accepted(self):
        power = self.make_power(waiting=True)
        monitor = object()

        with patch(
            "sonoscdplayer.player.service.wait_for_woken_disc",
            return_value=True,
        ) as verification:
            def wake(*, verify):
                return verify("/dev/sr0")

            power.wake.side_effect = wake

            self.assertTrue(
                process_drive_activity(power, monitor)
            )

        power.activity.assert_called_once()
        power.wake.assert_called_once()
        verification.assert_called_once()
        self.assertEqual(
            verification.call_args.args, (monitor, "/dev/sr0")
        )
        self.assertIsInstance(
            verification.call_args.kwargs["diagnostics"], dict
        )

    def test_failed_wake_rejected(self):
        power = self.make_power(
            waiting=True, wake_result=False
        )
        self.assertFalse(
            process_drive_activity(power, object())
        )
        power.wake.assert_called_once()

    def test_activity_never_calls_wake_without_need(self):
        power = self.make_power(waiting=False)
        for _ in range(3):
            self.assertTrue(
                process_drive_activity(power, object())
            )
        self.assertEqual(power.activity.call_count, 3)
        power.wake.assert_not_called()


if __name__ == '__main__':
    unittest.main()
