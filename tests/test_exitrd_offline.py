"""Exitrd offline checks: no PID1 execution, mounts, or poweroff performed."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ExitrdOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.temp.name) / 'shutdown'
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_exitrd.sh')],
            env={**os.environ, 'BUILD_DIR': cls.temp.name},
            text=True, capture_output=True, timeout=40)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, *args):
        return subprocess.run([str(self.executable), *args], text=True,
                              capture_output=True, timeout=5)

    def probe(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mountinfo'
            path.write_text(content)
            return self.call('--probe-mountinfo', str(path))

    def test_build_output_is_not_group_or_world_writable(self):
        import stat
        mode = self.executable.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertEqual(mode & (stat.S_IWGRP | stat.S_IWOTH), 0)

    def test_binary_offline_check_never_renders(self):
        result = self.call('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('safe-approved=0', result.stdout)
        self.assertIn('backend=ssd1327', result.stdout)

    def test_default_frame_matches_normal_final_artwork(self):
        """Ensure production exitrd uses the known final frame, not diagnostic/safe."""
        import re
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        from build_shutdown_renderer import pack_ssd1327

        header = (Path(self.temp.name) / 'shutdown_frames.h').read_text()
        match = re.search(
            r'static const uint8_t shutdown_frame_final\[8192\] = \{(.*?)\};',
            header, flags=re.S)
        self.assertIsNotNone(match)
        packed = bytes(int(value, 16) for value in
                       re.findall(r'0x([0-9a-f]{2})', match.group(1)))
        self.assertEqual(packed, pack_ssd1327(ROOT / 'src/data/pleasewait_screen.png'))
        self.assertNotEqual(packed, pack_ssd1327(ROOT / 'src/data/safetounplug_screen.png'))
        self.assertNotIn('shutdown_frame_diagnostic', header)

    def test_normal_exitrd_uses_final_only_after_successful_poweroff_unmount(self):
        source = (ROOT / 'src/shutdown/exitrd.c').read_text()
        self.assertIn('const uint8_t *frame = shutdown_frame_final;', source)
        self.assertIn('#if SONOSCD_EXITRD_SAFE_APPROVED != 0', source)
        self.assertIn('#error Safe-to-unplug rendering is locked', source)
        self.assertIn('#if SONOSCD_EXITRD_UNVERIFIED_SAFE', source)
        self.assertIn('const uint8_t *frame = shutdown_frame_safe;', source)
        self.assertIn('if (experimental_frame_allowed(&evidence, poweroff))', source)
        self.assertIn('e->unmount == UNMOUNT_OK', source)
        self.assertIn('e->audit == AUDIT_CLEAN', source)
        self.assertIn('if (experimental_delay()) show_experimental_frame();', source)
        self.assertIn('#if SONOSCD_EXITRD_DIAGNOSTIC', source)
        self.assertIn('if (poweroff) show_post_unmount();', source)
        self.assertIn('if (release_oldroot() == 0)', source)
        check = self.call('--check')
        self.assertEqual(check.returncode, 0)
        self.assertIn('diagnostic=0', check.stdout)
        self.assertIn('safe-approved=0', check.stdout)

    def test_refuses_regular_process_poweroff_reboot(self):
        for action in ('poweroff', 'reboot', 'halt', 'kexec', 'unknown'):
            with self.subTest(action=action):
                self.assertEqual(self.call(action).returncode, 2)

    def test_mountinfo_only_tracks_oldroot_and_descendants(self):
        info = (
            '1 0 8:1 / / rw - ext4 /dev/sda1 rw\n'
            '2 1 8:1 / /oldroot rw - ext4 /dev/sda1 rw\n'
            '3 2 0:5 / /oldroot/boot\\040firmware rw - vfat /dev/sda2 rw\n'
            '4 1 0:1 / /oldroot-other rw - tmpfs tmpfs rw\n'
        )
        result = self.probe(info)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('oldroot_mounts=2 oldroot_present=1', result.stdout)

    def audit(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mountinfo'
            path.write_text(content)
            return self.call('--audit-mountinfo', str(path))

    def simulate(self, unmount, audit, power):
        return self.call('--simulate-safety', unmount, audit, power)

    def test_shutdown_state_successful_software_path_still_denies_safety(self):
        result = self.simulate('ok', 'clean', 'requested')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('unmount=ok audit=clean poweroff=requested safe-eligible=0',
                      result.stdout)

    def test_shutdown_state_unmount_failure_skips_audit(self):
        for audit in ('clean', 'dirty', 'failed'):
            with self.subTest(audit=audit):
                result = self.simulate('failed', audit, 'requested')
                self.assertEqual(result.returncode, 0)
                self.assertIn('unmount=failed audit=skipped', result.stdout)
                self.assertIn('safe-eligible=0', result.stdout)

    def test_shutdown_state_dirty_and_unreadable_audits(self):
        for audit, expected in (('dirty', 'dirty'), ('failed', 'failed')):
            with self.subTest(audit=audit):
                result = self.simulate('ok', audit, 'requested')
                self.assertEqual(result.returncode, 0)
                self.assertIn('audit=' + expected, result.stdout)
                self.assertIn('safe-eligible=0', result.stdout)

    def test_shutdown_state_reboot_syscall_return_is_failure(self):
        for unmount, audit in (('ok', 'clean'), ('ok', 'dirty'),
                               ('ok', 'failed'), ('failed', 'clean')):
            with self.subTest(unmount=unmount, audit=audit):
                result = self.simulate(unmount, audit, 'returned-error')
                self.assertEqual(result.returncode, 0)
                self.assertIn('poweroff=returned-error safe-eligible=0',
                              result.stdout)

    def test_shutdown_state_invalid_simulated_events_rejected(self):
        for args in (('maybe', 'clean', 'requested'),
                     ('ok', 'unknown', 'requested'),
                     ('ok', 'clean', 'confirmed'),
                     ('ok', 'clean')):
            with self.subTest(args=args):
                result = self.call('--simulate-safety', *args)
                self.assertNotEqual(result.returncode, 0)

    def test_shutdown_state_never_claims_poweroff_completed(self):
        source = (ROOT / 'src/shutdown/exitrd.c').read_text()
        self.assertIn('POWER_RETURNED_ERROR', source)
        self.assertIn('evidence_power_returned(&evidence);', source)
        self.assertNotIn('POWER_COMPLETED', source)
        self.assertIn('return false; /* controller durability', source)
        self.assertIn('#if SONOSCD_EXITRD_UNVERIFIED_SAFE', source)
        self.assertIn('const uint8_t *frame = shutdown_frame_safe;', source)
        self.assertIn('if (experimental_frame_allowed(&evidence, poweroff))', source)
        self.assertIn('e->unmount == UNMOUNT_OK', source)
        self.assertIn('e->audit == AUDIT_CLEAN', source)
        self.assertIn('if (experimental_delay()) show_experimental_frame();', source)

    def test_mount_audit_all_volatile_still_denies_safety(self):
        info = (
            '1 0 0:1 / / rw - tmpfs tmpfs rw\n'
            '2 1 0:2 / /proc rw - proc proc rw\n'
            '3 1 0:3 / /dev rw - devtmpfs devtmpfs rw\n'
        )
        result = self.audit(info)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('persistent_mounts=0 oldroot_mounts=0 safe-eligible=0',
                      result.stdout)

    def test_mount_audit_detects_residual_storage_and_oldroot(self):
        info = (
            '1 0 0:1 / / rw - tmpfs tmpfs rw\n'
            '2 1 179:2 / /oldroot rw - ext4 /dev/mmcblk0p2 rw\n'
            '3 2 179:1 / /oldroot/boot rw - vfat /dev/mmcblk0p1 rw\n'
        )
        result = self.audit(info)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('persistent_mounts=2 oldroot_mounts=2 safe-eligible=0',
                      result.stdout)

    def test_mount_audit_unknown_filesystem_is_conservatively_persistent(self):
        info = ('1 0 0:1 / / rw - tmpfs tmpfs rw\n'
                '2 1 0:42 / /mystery ro - surprise_fs none ro\n')
        result = self.audit(info)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('persistent_mounts=1 oldroot_mounts=0 safe-eligible=0',
                      result.stdout)

    def test_mount_audit_rejects_malformed_or_missing_input(self):
        for content in ('broken\n',
                        '1 0 0:1 / / rw - tmpfs\n',
                        '1 0 0:1 / / rw - tmpfs tmpfs\n',
                        '1 0 0:1 / /bad\\0xx rw - tmpfs tmpfs rw\n'):
            with self.subTest(content=repr(content[:45])):
                self.assertNotEqual(self.audit(content).returncode, 0)

    def test_mount_audit_empty_inventory_never_authorizes_safety(self):
        result = self.audit('')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('persistent_mounts=0 oldroot_mounts=0 safe-eligible=0',
                      result.stdout)

    def test_mount_audit_ignores_similar_non_oldroot_prefix(self):
        result = self.audit(
            '1 0 0:1 / / rw - tmpfs tmpfs rw\n'
            '2 1 0:2 / /oldroot-other rw - tmpfs tmpfs rw\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn('oldroot_mounts=0 safe-eligible=0', result.stdout)

    def test_missing_oldroot_is_not_accepted(self):
        result = self.probe('1 0 8:1 / / rw - ext4 /dev/sda1 rw\n')
        self.assertEqual(result.returncode, 0)
        self.assertIn('oldroot_mounts=0 oldroot_present=0', result.stdout)

    def test_malformed_mountinfo_fails_closed(self):
        for content in ('bad\n',
                        '2 1 8:1 / /oldroot/invalid\\0x0 rw - ext4 x rw\n',
                        '2 1 8:1 / /oldroot/' + 'a' * 5000 + ' rw - ext4 x rw\n'):
            with self.subTest(content=content[:30]):
                self.assertEqual(self.probe(content).returncode, 1)

    def test_only_normal_unmount_after_full_oldroot_detection(self):
        source = (ROOT / 'src/shutdown/exitrd.c').read_text()
        self.assertIn('umount2(points[i].path, 0)', source)
        self.assertNotIn('MNT_DETACH', source)
        self.assertNotIn('MNT_FORCE', source)
        self.assertIn('if (poweroff) show_post_unmount()', source)

    def test_late_hook_keeps_final_fallback_with_staged_exitrd(self):
        script = (ROOT / 'systemd/sonoscdplayer-final-shutdown-hook').read_text()
        self.assertNotIn('if [ "$phase" = final ] && [ -x /run/initramfs/shutdown ]; then', script)
        with tempfile.TemporaryDirectory() as directory:
            staged = Path(directory) / 'exitrd'
            staged.write_text('#!/bin/sh\\nexit 0\\n')
            staged.chmod(0o755)
            renderer = Path(directory) / 'renderer'
            calls = Path(directory) / 'calls'
            renderer.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' +
                                str(calls) + '"\n')
            renderer.chmod(0o755)
            hook = Path(directory) / 'hook'
            hook.write_text(script.replace('/run/initramfs/shutdown', str(staged))
                            .replace('/usr/local/libexec/sonoscdplayer-shutdown-display',
                                     str(renderer)))
            for action in ('poweroff', 'reboot'):
                self.assertEqual(subprocess.run(['sh', str(hook), action],
                                                check=False, timeout=3).returncode, 0)
            self.assertEqual(calls.read_text().splitlines(), ['final', 'reboot'])

    def test_build_does_not_install_or_stage_exitrd(self):
        script = (ROOT / 'tools/build_exitrd.sh').read_text()
        self.assertNotIn('systemctl', script)
        self.assertNotIn('/run/initramfs/shutdown', script)
        self.assertNotIn('SONOSCD_EXITRD_SAFE_APPROVED=1', script)



class ExitrdDiagnosticOfflineTests(unittest.TestCase):
    """Opt-in graphical proof only; no PID1, SPI, mounts, or poweroff."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'shutdown'
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_exitrd.sh')],
            env={**os.environ, 'BUILD_DIR': cls.temp.name, 'EXITRD_DIAGNOSTIC': '1'},
            text=True, capture_output=True, timeout=40)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_diagnostic_build_is_explicit_and_not_safe_approved(self):
        check = subprocess.run([str(self.binary), '--check'],
                               capture_output=True, text=True, timeout=5, check=True)
        self.assertIn('diagnostic=1', check.stdout)
        self.assertIn('safe-approved=0', check.stdout)
        frames = (Path(self.temp.name) / 'shutdown_frames.h').read_text()
        self.assertIn('shutdown_frame_diagnostic[8192]', frames)

    def test_diagnostic_binary_never_runs_poweroff_outside_pid1(self):
        result = subprocess.run([str(self.binary), 'poweroff'],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)

    def test_diagnostic_frame_is_distinct_and_has_no_safe_message(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        from build_shutdown_renderer import diagnostic_frame, pack_ssd1327
        test_frame = diagnostic_frame()
        normal_frame = pack_ssd1327(ROOT / 'src/data/pleasewait_screen.png')
        safe_frame = pack_ssd1327(ROOT / 'src/data/safetounplug_screen.png')
        self.assertEqual(len(test_frame), 8192)
        self.assertNotEqual(test_frame, normal_frame)
        self.assertNotEqual(test_frame, safe_frame)

    def test_diagnostic_render_only_after_oldroot_success(self):
        source = (ROOT / 'src/shutdown/exitrd.c').read_text()
        success = source.index('if (release_oldroot() == 0)')
        draw = source.index('if (poweroff) show_post_unmount()', success)
        failure = source.index('oldroot_unmount=failed', draw)
        self.assertLess(success, draw)
        self.assertLess(draw, failure)

    def test_default_build_remains_non_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['sh', str(ROOT / 'tools/build_exitrd.sh')],
                env={**os.environ, 'BUILD_DIR': directory, 'EXITRD_DIAGNOSTIC': '0'},
                capture_output=True, text=True, timeout=40, check=True)
            self.assertIn('diagnostic=0', result.stdout)
            self.assertIn('safe-approved=0', result.stdout)
            frames = (Path(directory) / 'shutdown_frames.h').read_text()
            self.assertNotIn('shutdown_frame_diagnostic', frames)


class ExitrdUnverifiedSafeBuildTests(unittest.TestCase):
    """Experimental early graphic must be deliberate, isolated and never 'approved'."""

    def build(self, directory, **extra):
        env = {**os.environ, 'BUILD_DIR': str(directory),
               'EXITRD_DIAGNOSTIC': '0', 'EXITRD_UNVERIFIED_SAFE': '1', **extra}
        return subprocess.run(['sh', str(ROOT / 'tools/build_exitrd.sh')],
                              env=env, text=True, capture_output=True, timeout=40)

    def test_unverified_build_requires_explicit_acknowledgement(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.build(directory)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('BEFORE final kernel poweroff', result.stderr)
            self.assertFalse((Path(directory) / 'shutdown').exists())

    def test_unverified_build_is_not_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.build(
                directory, EXITRD_DIAGNOSTIC='1',
                EXITRD_UNVERIFIED_SAFE_ACK='I_ACCEPT_EARLY_SAFE_SCREEN_RISK')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('cannot be combined', result.stderr)

    def test_explicit_build_remains_unapproved_and_offline_only(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.build(
                directory, EXITRD_UNVERIFIED_SAFE_ACK='I_ACCEPT_EARLY_SAFE_SCREEN_RISK')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('safe-approved=0', result.stdout)
            self.assertIn('unverified-safe=1 delay-seconds=3', result.stdout)
            self.assertIn('Do NOT unplug until ACT LED', result.stderr)
            binary = Path(directory) / 'shutdown'
            self.assertEqual(subprocess.run([str(binary), 'poweroff'],
                            capture_output=True, timeout=5).returncode, 2)
            self.assertIn('shutdown_frame_safe[8192]',
                          (Path(directory) / 'shutdown_frames.h').read_text())

    def test_normal_build_stays_unverified_safe_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['sh', str(ROOT / 'tools/build_exitrd.sh')],
                env={**os.environ, 'BUILD_DIR': directory, 'EXITRD_DIAGNOSTIC': '0',
                     'EXITRD_UNVERIFIED_SAFE': '0'},
                text=True, capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('unverified-safe=0 delay-seconds=0', result.stdout)
            self.assertIn('safe-approved=0', result.stdout)

    def test_source_requires_clean_audit_and_reverts_after_syscall_failure(self):
        source = (ROOT / 'src/shutdown/exitrd.c').read_text()
        self.assertIn('e->unmount == UNMOUNT_OK', source)
        self.assertIn('e->audit == AUDIT_CLEAN', source)
        self.assertIn('return poweroff &&', source)
        self.assertIn('if (experimental_delay()) show_experimental_frame();', source)
        self.assertIn('if (poweroff) show_post_unmount();', source)
        self.assertIn('evidence_power_returned(&evidence);', source)
        self.assertIn('SONOSCD_EXITRD_EXPERIMENT_DELAY_SECONDS 3', source)


if __name__ == '__main__':
    unittest.main()
