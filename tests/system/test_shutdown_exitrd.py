"""Experimental exitrd: offline build checks, preflight, player config opt-in, guided and persistent installation (never activates)."""
import contextlib
import hashlib
import io
import os
import platform
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / 'tools'))
import exitrd_autostage as stager
import exitrd_once as staging
import exitrd_player_config as cfg
import exitrd_preflight as preflight
import install_exitrd_persistent as installer
SCRIPT = ROOT / "tools/manage_experimental_exitrd.sh"



def prepare_display_config(directory):
    """Write isolated, non-device-specific TOML for an offline build."""
    config = Path(directory) / 'test_display.toml'
    config.write_text(
        '[display]\ncontroller = "ssd1327"\ninterface = "spi"\n'
        'dc_pin = 22\nrst_pin = 27\n',
        encoding='utf-8',
    )
    return str(config)


class ExitrdOfflineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.executable = Path(cls.temp.name) / 'shutdown'
        cls.display_config = prepare_display_config(cls.temp.name)
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_exitrd.sh')],
            env={**os.environ, 'BUILD_DIR': cls.temp.name, 'DISPLAY_CONFIG': cls.display_config},
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
        from build_shutdown_renderer import compose_frame, configured_language, frame_text

        language = configured_language()
        header = (Path(self.temp.name) / 'shutdown_frames.h').read_text()
        match = re.search(
            r'static const uint8_t shutdown_frame_final_' + language + r'\[8192\] = \{(.*?)\};',
            header, flags=re.S)
        self.assertIsNotNone(match)
        packed = bytes(int(value, 16) for value in
                       re.findall(r'0x([0-9a-f]{2})', match.group(1)))
        self.assertEqual(packed, compose_frame(ROOT / 'src/sonoscdplayer/assets/screens/pleasewait_screen.png',
                                               frame_text('final', language)))
        self.assertNotEqual(packed, compose_frame(ROOT / 'src/sonoscdplayer/assets/screens/safetounplug_screen.png',
                                                  frame_text('safe', language)))
        self.assertNotIn('shutdown_frame_diagnostic', header)

    def test_safety_invariants_of_exitrd_and_build(self):
        """Forbidden constructs only; behaviour is covered by the harness tests."""
        source = (ROOT / 'src/native/shutdown/exitrd.c').read_text()
        self.assertIn('#error Safe-to-unplug rendering is locked', source)
        self.assertNotIn('MNT_DETACH', source)
        self.assertNotIn('MNT_FORCE', source)
        self.assertNotIn('POWER_COMPLETED', source)
        backend = (ROOT / 'src/native/shutdown/backends/ssd1327.c').read_text().lower()
        self.assertNotIn('0xae', backend)  # never switches the display off
        script = (ROOT / 'tools/build_exitrd.sh').read_text()
        self.assertNotIn('systemctl', script)
        self.assertNotIn('/run/initramfs/shutdown', script)

    def test_check_reports_non_diagnostic_unapproved_build(self):
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


class ExitrdDiagnosticOfflineTests(unittest.TestCase):
    """Opt-in graphical proof only; no PID1, SPI, mounts, or poweroff."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temp.name) / 'shutdown'
        cls.display_config = prepare_display_config(cls.temp.name)
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_exitrd.sh')],
            env={**os.environ, 'BUILD_DIR': cls.temp.name, 'DISPLAY_CONFIG': cls.display_config, 'EXITRD_DIAGNOSTIC': '1'},
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
        from build_shutdown_renderer import compose_frame, diagnostic_frame, frame_text
        test_frame = diagnostic_frame()
        normal_frame = compose_frame(ROOT / 'src/sonoscdplayer/assets/screens/pleasewait_screen.png',
                                     frame_text('final', 'de'))
        safe_frame = compose_frame(ROOT / 'src/sonoscdplayer/assets/screens/safetounplug_screen.png',
                                   frame_text('safe', 'de'))
        self.assertEqual(len(test_frame), 8192)
        self.assertNotEqual(test_frame, normal_frame)
        self.assertNotEqual(test_frame, safe_frame)

    def test_default_build_remains_non_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            display_config = prepare_display_config(directory)
            result = subprocess.run(['sh', str(ROOT / 'tools/build_exitrd.sh')],
                env={**os.environ, 'BUILD_DIR': directory, 'DISPLAY_CONFIG': display_config, 'EXITRD_DIAGNOSTIC': '0'},
                capture_output=True, text=True, timeout=40, check=True)
            self.assertIn('diagnostic=0', result.stdout)
            self.assertIn('safe-approved=0', result.stdout)
            frames = (Path(directory) / 'shutdown_frames.h').read_text()
            self.assertNotIn('shutdown_frame_diagnostic', frames)


class ExitrdUnverifiedSafeBuildTests(unittest.TestCase):
    """Experimental early graphic must be deliberate, isolated and never 'approved'."""

    def build(self, directory, **extra):
        display_config = prepare_display_config(directory)
        env = {**os.environ, 'BUILD_DIR': str(directory),
               'EXITRD_DIAGNOSTIC': '0', 'EXITRD_UNVERIFIED_SAFE': '1', 'DISPLAY_CONFIG': display_config, **extra}
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
            self.assertIn('shutdown_frame_safe_de[8192]',
                          (Path(directory) / 'shutdown_frames.h').read_text())

    def test_normal_build_stays_unverified_safe_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            display_config = prepare_display_config(directory)
            result = subprocess.run(['sh', str(ROOT / 'tools/build_exitrd.sh')],
                env={**os.environ, 'BUILD_DIR': directory, 'EXITRD_DIAGNOSTIC': '0',
                     'EXITRD_UNVERIFIED_SAFE': '0', 'DISPLAY_CONFIG': display_config},
                text=True, capture_output=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('unverified-safe=0 delay-seconds=0', result.stdout)
            self.assertIn('safe-approved=0', result.stdout)


def elf_fixture(*, machine=183, interpreter=False, needed=False, executable=True):
    size = 256
    result = bytearray(size)
    result[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", result, 16, 2, machine)
    struct.pack_into("<Q", result, 24, 0x401000)
    struct.pack_into("<Q", result, 32, 64)
    struct.pack_into("<HH", result, 54, 56, 2 if interpreter or needed else 1)
    struct.pack_into("<IIQQQQQQ", result, 64, 1, 5 if executable else 4,
                     192, 0x401000, 0, 32, 64, 0x1000)
    if interpreter:
        struct.pack_into("<IIQQQQQQ", result, 120, 3, 4, 240, 0, 0, 8, 8, 1)
    if needed:
        struct.pack_into("<IIQQQQQQ", result, 120, 2, 4, 224, 0, 0, 16, 16, 8)
        struct.pack_into("<QQ", result, 224, 1, 3)
    return bytes(result)


class ExitrdPreflightTests(unittest.TestCase):
    def binary(self, path, *, machine=183):
        path.write_bytes(elf_fixture(machine=machine))
        path.chmod(0o700)
        return path

    def test_valid_static_aarch64_fixture(self):
        self.assertEqual(preflight.inspect_elf(elf_fixture()), "aarch64")

    def test_refuses_dynamic_loader_and_needed_library(self):
        for data in (elf_fixture(interpreter=True), elf_fixture(needed=True)):
            with self.subTest(data=data[120:124]):
                with self.assertRaises(preflight.PreflightError):
                    preflight.inspect_elf(data)

    def test_refuses_missing_executable_entrypoint(self):
        with self.assertRaisesRegex(preflight.PreflightError, "entrypoint"):
            preflight.inspect_elf(elf_fixture(executable=False))

    def test_refuses_broken_or_unsupported_elf(self):
        for data in (b"invalid", elf_fixture(machine=40), b"\x7fELF\x02\x01" + bytes(50),
                     elf_fixture()[:85]):
            with self.subTest(length=len(data)):
                with self.assertRaises(preflight.PreflightError):
                    preflight.inspect_elf(data)

    def test_refuses_nonexecutable_and_world_writable_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = self.binary(Path(directory) / "shutdown")
            binary.chmod(0o600)
            with self.assertRaises(preflight.PreflightError):
                preflight.read_binary(binary)
            binary.chmod(0o722)
            with self.assertRaises(preflight.PreflightError):
                preflight.read_binary(binary)

    def test_rejects_malformed_mountinfo_escape(self):
        for path in (r"/run/invalid\xy0", r"/run/invalid\000"):
            with self.assertRaises(preflight.PreflightError):
                preflight.unescape_mount_path(path)

    def test_mountinfo_uses_component_aligned_longest_prefix(self):
        # /run-other must not be confused with /run
        mountinfo = ("1 0 0:1 / / rw - ext4 /dev/root rw\n"
                     "2 1 0:3 / /run rw - tmpfs tmpfs rw\n"
                     "3 1 0:4 / /run-other rw - ext4 /dev/root rw\n")
        self.assertEqual(preflight.mount_fs_type(Path("/run"), mountinfo), "tmpfs")

    def test_rejects_live_exitrd_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(preflight.PreflightError, "/run/initramfs"):
                preflight.inspect_stage_dir(Path("/"), "")

    def test_rejects_non_tmpfs_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            with patch.object(preflight, "mount_fs_type", return_value="ext4"):
                with self.assertRaisesRegex(preflight.PreflightError, "tmpfs"):
                    preflight.inspect_stage_dir(target, "")

    @unittest.skipUnless(Path("/dev/shm").is_dir(), "no sandbox tmpfs available")
    def test_check_does_not_stage_and_stage_does_not_overwrite(self):
        with tempfile.TemporaryDirectory(dir="/dev/shm") as directory:
            target = Path(directory)
            source = self.binary(target / "source")
            arch = "aarch64"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.run(["--binary", str(source),
                                                "--expected-arch", arch,
                                                "--stage-dir", str(target)]), 0)
            self.assertFalse((target / "shutdown").exists())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(preflight.run(["--binary", str(source),
                                                "--expected-arch", arch,
                                                "--stage-dir", str(target),
                                                "--stage"]), 0)
            staged = target / "shutdown"
            self.assertEqual(staged.read_bytes(), source.read_bytes())
            self.assertTrue(staged.stat().st_mode & stat.S_IXUSR)
            with self.assertRaises(preflight.PreflightError):
                mountinfo = Path("/proc/self/mountinfo").read_text()
                preflight.inspect_stage_dir(target, mountinfo)

    def test_mismatched_binary_architecture_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.binary(Path(directory) / "shutdown")
            with self.assertRaisesRegex(preflight.PreflightError, "architecture"):
                preflight.run(["--binary", str(source), "--expected-arch", "x86_64"])

    def test_host_check_reports_permission_denied_without_crashing(self):
        original_exists = Path.exists

        def deny_live_exitrd(path):
            if str(path) == "/run/initramfs/shutdown":
                raise PermissionError(13, "Permission denied", str(path))
            return original_exists(path)

        with patch.object(preflight, "mount_fs_type", return_value="tmpfs"), \
             patch.object(Path, "exists", deny_live_exitrd):
            issues = preflight.inspect_host("")
        self.assertTrue(any("cannot inspect existing exitrd" in item for item in issues))

    def test_host_checks_are_explicit_and_read_only(self):
        with patch.object(preflight, "mount_fs_type", return_value="ext4"):
            issues = preflight.inspect_host("")
        self.assertTrue(any("/run is not tmpfs" in issue for issue in issues))


class ExitrdOnceContracts(unittest.TestCase):
    def test_default_is_check_only(self):
        with patch.object(staging, "precheck", return_value=(b"x", "a" * 64, {})):
            with patch.object(staging, "arm_once") as arm:
                self.assertEqual(staging.main([]), 0)
                arm.assert_not_called()

    def test_activate_needs_explicit_acknowledgement(self):
        with patch.object(staging, "precheck") as check:
            self.assertEqual(staging.main(["--activate-once"]), 1)
            check.assert_not_called()

    def test_valid_activation_requires_sha256(self):
        with patch.object(staging, "precheck", side_effect=staging.PreflightError("missing sha")):
            self.assertEqual(staging.main(["--activate-once",
                                          "--acknowledge", staging.ACK]), 1)

    def test_precheck_rejects_existing_separate_mount(self):
        with patch.object(staging, "require_host", return_value="mountinfo"):
            with patch.object(staging, "is_mountpoint", return_value=True):
                with self.assertRaisesRegex(staging.PreflightError, "already separately mounted"):
                    staging.precheck(Path("/nonexistent"), None, activating=False)

    def test_staging_rolls_back_on_failed_binary_check(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "initramfs"
            live.mkdir()
            marker = Path(folder) / "marker"
            commands = []

            def fake_command(*args):
                commands.append(args)
                if args[-1] == "--check":
                    raise staging.PreflightError("simulated check failure")

            with patch.object(staging, "LIVE", live), \
                 patch.object(staging, "MARKER", marker), \
                 patch.object(staging, "run_command", side_effect=fake_command), \
                 patch.object(staging.os, "geteuid", return_value=0):
                payload = b"fake binary for mocked check"
                digest = hashlib.sha256(payload).hexdigest()
                with self.assertRaisesRegex(staging.PreflightError, "simulated"):
                    staging.arm_once(payload, digest, {"fsck-root": b""})
                self.assertEqual(commands[-1], ("umount", str(live)))
                self.assertFalse(marker.exists())

    def test_explicit_deactivation_validates_digest_before_unmount(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "initramfs"
            live.mkdir()
            marker = Path(folder) / "marker"
            (live / "shutdown").write_bytes(b"binary")
            marker.write_text("sha256=" + hashlib.sha256(b"other").hexdigest() + "\n")
            with patch.object(staging, "LIVE", live), \
                 patch.object(staging, "MARKER", marker), \
                 patch.object(staging, "require_host", return_value="fixture"), \
                 patch.object(staging, "is_mountpoint", return_value=True), \
                 patch.object(staging.os, "geteuid", return_value=0), \
                 patch.object(staging, "run_command") as command:
                with self.assertRaisesRegex(staging.PreflightError, "digest"):
                    staging.disarm()
                command.assert_not_called()

    def test_staging_creates_root_handover_mountpoints(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            live = Path(folder) / "initramfs"
            live.mkdir()
            marker = Path(folder) / "marker"
            payload = b"mocked binary"
            digest = hashlib.sha256(payload).hexdigest()
            with patch.object(staging, "LIVE", live), \
                 patch.object(staging, "MARKER", marker), \
                 patch.object(staging, "run_command"), \
                 patch.object(staging.os, "geteuid", return_value=0):
                staging.arm_once(payload, digest, {"fsck-root": b""})
                for name in ("oldroot", "dev", "proc", "sys", "run"):
                    with self.subTest(path=name):
                        self.assertTrue((live / name).is_dir())
                self.assertEqual((live / "shutdown").read_bytes(), payload)
                self.assertEqual((live / "fsck-root").read_bytes(), b"")

    def test_activation_dispatch_can_be_mocked(self):
        with patch.object(staging, "precheck", return_value=(b"payload", "f" * 64, {})):
            with patch.object(staging, "arm_once") as arm:
                self.assertEqual(staging.main(["--activate-once",
                                  "--expected-sha256", "f" * 64,
                                  "--acknowledge", staging.ACK]), 0)
                arm.assert_called_once_with(b"payload", "f" * 64, {})


class ExitrdPlayerConfigTests(unittest.TestCase):
    def fixture(self, content):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        file = Path(temp.name) / "player.toml"
        file.write_text(content)
        return file

    def test_existing_example_defaults_to_disabled(self):
        self.assertFalse(cfg.enabled_from_player_config(ROOT / "config/player.example.toml"))

    def test_missing_option_defaults_off(self):
        self.assertFalse(cfg.enabled_from_player_config(self.fixture('[service]\nshutdown_policy = "stop_owned"\n')))

    def test_existing_service_options_are_accepted(self):
        file = self.fixture('[service]\nshutdown_policy = "stop_owned"\nexperimental_safe_screen = true\n')
        self.assertTrue(cfg.enabled_from_player_config(file))

    def test_invalid_values_and_files_fail_closed(self):
        for data in ('[service]\nexperimental_safe_screen = "true"\n',
                     '[service]\nexperimental_safe_screen = 1\n',
                     '[service]\nexperimental_safe_screen = true\n'
                     'experimental_safe_screen = false\n',
                     '[service\nexperimental_safe_screen = true\n'):
            with self.subTest(data=data):
                with self.assertRaises(cfg.ConfigurationError):
                    cfg.enabled_from_player_config(self.fixture(data))
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(cfg.ConfigurationError):
                cfg.enabled_from_player_config(Path(temp) / 'missing.toml')
            target = Path(temp) / 'real.toml'
            target.write_text('[service]\nexperimental_safe_screen = true\n')
            link = Path(temp) / 'link.toml'
            link.symlink_to(target)
            with self.assertRaises(cfg.ConfigurationError):
                cfg.enabled_from_player_config(link)

    def test_status_cannot_arm_even_when_enabled(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        with patch.object(cfg.exitrd_once, 'precheck') as pre, \
             patch.object(cfg.exitrd_once, 'arm_once') as arm, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cfg.main(['--config', str(file)]), 0)
        self.assertIn('READ-ONLY', output.getvalue())
        pre.assert_not_called()
        arm.assert_not_called()

    def test_disabled_blocks_activation_before_binary_check(self):
        file = self.fixture('[service]\nexperimental_safe_screen = false\n')
        with patch.object(cfg, 'verify_binary') as verify, \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cfg.main(['--config', str(file), '--activate-once']), 1)
        verify.assert_not_called()

    def test_two_acknowledgements_and_sha_required(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        for extra in (
            [],
            ['--acknowledge', cfg.exitrd_once.ACK],
            ['--acknowledge-risk', cfg.RISK_ACK],
            ['--acknowledge', cfg.exitrd_once.ACK, '--acknowledge-risk', cfg.RISK_ACK],
        ):
            with self.subTest(extra=extra), \
                 patch.object(cfg, 'verify_binary') as verify, \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cfg.main(['--config', str(file), '--activate-once', *extra]), 1)
            verify.assert_not_called()

    def test_mode_check_rejects_wrong_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / 'shutdown'
            executable.write_text('mock')
            with patch.object(cfg, 'EXPERIMENTAL', executable), \
                 patch('subprocess.run', return_value=subprocess.CompletedProcess(
                     [], 0, stdout='safe-approved=0 diagnostic=0 unverified-safe=0 delay-seconds=0')):
                with self.assertRaises(cfg.ConfigurationError):
                    cfg.verify_binary()

    def test_approved_activation_delegates_to_one_boot_helper(self):
        file = self.fixture('[service]\nexperimental_safe_screen = true\n')
        data = b'test-static-binary'
        digest = hashlib.sha256(data).hexdigest()
        args = ['--config', str(file), '--activate-once',
                '--expected-sha256', digest,
                '--acknowledge', cfg.exitrd_once.ACK,
                '--acknowledge-risk', cfg.RISK_ACK]
        with patch.object(cfg, 'verify_binary') as verify, \
             patch.object(cfg.exitrd_once, 'precheck',
                          return_value=(data, digest, {})) as pre, \
             patch.object(cfg.exitrd_once, 'arm_once') as arm, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cfg.main(args), 0)
        verify.assert_called_once_with()
        pre.assert_called_once_with(cfg.EXPERIMENTAL, digest, activating=True)
        arm.assert_called_once_with(data, digest, {})

    def test_disarm_is_available_even_when_config_broken(self):
        with patch.object(cfg.exitrd_once, 'disarm') as disarm:
            self.assertEqual(cfg.main(['--config', '/nonexistent/player.toml', '--deactivate']), 0)
        disarm.assert_called_once_with()


class GuidedExitrdTests(unittest.TestCase):
    def call(self, *args, stdin=None, env=None):
        return subprocess.run(
            ["sh", str(SCRIPT), *args],
            input=stdin, text=True, capture_output=True,
            env={**os.environ, **(env or {})}, timeout=10)

    def without_live_stage(self, directory):
        fake_bin = Path(directory) / "fakebin"
        fake_bin.mkdir()
        for name in ("mountpoint", "systemctl"):
            tool = fake_bin / name
            tool.write_text("#!/bin/sh\nexit 1\n")
            tool.chmod(0o755)
        return {"PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", "")}

    def test_help_is_read_only(self):
        result = self.call("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--enable-once", result.stdout)
        self.assertIn("--disable", result.stdout)
        self.assertIn("AT YOUR OWN RISK", SCRIPT.read_text())

    def test_invalid_calls_rejected_before_build(self):
        for args in (("--enable-once", "--config"),
                     ("--status", "--config", ""),
                     ("--unknown",)):
            with self.subTest(args=args):
                self.assertEqual(self.call(*args).returncode, 2)

    def test_status_on_standard_player_config_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "player.toml"
            original = (ROOT / "config/player.example.toml").read_bytes()
            config.write_bytes(original)
            result = self.call("--status", env={"SONOSCDPLAYER_CONFIG_DIR": directory})
            self.assertEqual(config.read_bytes(), original)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("experimental_safe_screen=", result.stdout)
        self.assertIn("READ-ONLY", result.stdout)

    def test_status_on_default_example_is_read_only(self):
        result = self.call("--status", "--config", str(ROOT / "config/player.example.toml"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("experimental_safe_screen=false", result.stdout)
        self.assertIn("RAM exitrd mount:", result.stdout)

    def test_disabled_config_blocks_noninteractive_enable_without_build(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.call("--enable-once", "--config",
                               str(ROOT / "config/player.example.toml"),
                               env=self.without_live_stage(directory))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("experimental_safe_screen = true", result.stderr)

    def test_enabled_config_needs_interactive_terminal_to_activate(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / "player.toml"
            cfg.write_text("[service]\nexperimental_safe_screen = true\n")
            result = self.call("--enable-once", "--config", str(cfg),
                               stdin="YES-I-ACCEPT\n",
                               env=self.without_live_stage(directory))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("interactive terminal required", result.stderr)
            self.assertFalse((Path(directory) / "shutdown").exists())


class PersistentExitrdTests(unittest.TestCase):
    def test_installed_unit_rearms_at_boot_and_not_during_shutdown(self):
        unit = (ROOT / 'systemd/sonoscdplayer-exitrd.service').read_text()
        self.assertIn('WantedBy=multi-user.target', unit)
        self.assertIn('Type=oneshot', unit)
        self.assertIn('After=local-fs.target', unit)
        self.assertIn('exitrd_autostage.py', unit)
        self.assertNotIn('ExecStop=', unit)
        self.assertNotIn('RemainAfterExit=yes', unit)
        self.assertNotIn('poweroff', unit)
        self.assertNotIn('reboot', unit)

    def test_installer_renders_config_path_into_unit(self):
        template = (ROOT / 'systemd/sonoscdplayer-exitrd.service').read_text()
        self.assertIn('--config @SONOSCDPLAYER_PLAYER_CONFIG@', template)
        unit = installer.rendered_unit_text(template, Path('/etc/example/player.toml'))
        self.assertIn('--config /etc/example/player.toml ', unit)
        self.assertNotIn('@SONOSCDPLAYER_', unit)
        self.assertNotIn('#@', unit)
        for invalid in (Path('relative.toml'), Path('/bad path.toml')):
            with self.subTest(invalid=invalid):
                with self.assertRaises(installer.InstallError):
                    installer.rendered_unit_text(template, invalid)
        with self.assertRaises(installer.InstallError):
            installer.rendered_unit_text(template + 'X=@SONOSCDPLAYER_OTHER@\n',
                                         Path('/etc/example/player.toml'))

    def test_shipped_template_defaults_off_and_runtime_is_boolean(self):
        self.assertFalse(stager.enabled(ROOT / 'config/player.example.toml'))
        self.assertIsInstance(stager.enabled(ROOT / 'config/player.example.toml'), bool)

    def test_autostage_does_not_arm_when_config_disabled(self):
        with patch.object(stager, 'enabled', return_value=False), \
             patch.object(stager, 'verify_installed') as verified, \
             patch.object(stager.exitrd_once, 'arm_once') as armed, \
             patch.object(stager.os, 'geteuid', return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            stager.stage(Path('/invalid'), Path('/invalid'), Path('/invalid'))
        verified.assert_not_called()
        armed.assert_not_called()

    def test_autostage_rejects_wrong_toml_type(self):
        with tempfile.TemporaryDirectory() as d:
            file = Path(d) / 'player.toml'
            file.write_text('[service]\nexperimental_safe_screen = "true"\n')
            with self.assertRaises(stager.StageError):
                stager.enabled(file)

    def test_verified_autostage_delegates_to_existing_preflight(self):
        with patch.object(stager, 'enabled', return_value=True), \
             patch.object(stager, 'verify_installed', return_value='a' * 64), \
             patch.object(stager.exitrd_once, 'precheck',
                          return_value=(b'binary', 'a' * 64, {})) as pre, \
             patch.object(stager.exitrd_once, 'arm_once') as arm, \
             patch.object(stager.os, 'geteuid', return_value=0), \
             contextlib.redirect_stdout(io.StringIO()):
            stager.stage(Path('config.toml'), Path('shutdown'), Path('checksum'))
        pre.assert_called_once_with(Path('shutdown'), 'a' * 64, activating=True)
        arm.assert_called_once_with(b'binary', 'a' * 64, {})

    def test_persistent_config_edit_preserves_other_sections(self):
        original = '[streaming]\nmax_buffer_mib = 16\n[service]\nshutdown_policy = "stop_owned"\n[led_outputs]\npower_pin = 12\n'
        enabled = installer.verified_config_text(original, True)
        self.assertIn('experimental_safe_screen = true', enabled)
        self.assertIn('max_buffer_mib = 16', enabled)
        self.assertIn('power_pin = 12', enabled)
        disabled = installer.verified_config_text(enabled, False)
        self.assertIn('experimental_safe_screen = false', disabled)
        self.assertEqual(disabled.count('experimental_safe_screen'), 1)

    def test_config_bad_values_and_missing_service_refused(self):
        for source in ('[streaming]\nx = 1\n',
                       '[service]\nexperimental_safe_screen = "yes"\n',
                       '[service]\nexperimental_safe_screen = 3\n'):
            with self.subTest(source=source):
                with self.assertRaises(installer.InstallError):
                    installer.verified_config_text(source, True)

    def test_uninstall_removes_only_owned_pycache(self):
        with tempfile.TemporaryDirectory() as temp:
            installed = Path(temp) / 'sonoscdplayer-exitrd'
            installed.mkdir()
            cache = installed / '__pycache__'
            cache.mkdir()
            (cache / 'exitrd_once.cpython-313.pyc').write_bytes(b'compiled')
            (cache / 'exitrd_preflight.cpython-313.pyc').write_bytes(b'compiled')
            installer.remove_owned_bytecode(installed)
            self.assertFalse(cache.exists())
            # Safe to repeat following a partially completed uninstall.
            installer.remove_owned_bytecode(installed)

    def test_uninstall_refuses_unknown_cache_file(self):
        with tempfile.TemporaryDirectory() as temp:
            installed = Path(temp) / 'sonoscdplayer-exitrd'
            installed.mkdir()
            cache = installed / '__pycache__'
            cache.mkdir()
            unknown = cache / 'unrelated.cpython-313.pyc'
            unknown.write_bytes(b'keep')
            with self.assertRaises(installer.InstallError):
                installer.remove_owned_bytecode(installed)
            self.assertTrue(unknown.is_file())

    def test_uninstall_refuses_symlinked_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            installed = Path(temp) / 'sonoscdplayer-exitrd'
            installed.mkdir()
            elsewhere = Path(temp) / 'external'
            elsewhere.mkdir()
            (installed / '__pycache__').symlink_to(elsewhere, target_is_directory=True)
            with self.assertRaises(installer.InstallError):
                installer.remove_owned_bytecode(installed)
            self.assertTrue(elsewhere.is_dir())


class ManagementScriptTests(unittest.TestCase):
    def test_install_refuses_without_interactive_terminal(self):
        import subprocess
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/manage_experimental_exitrd.sh'), '--install'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Interactive terminal required', result.stderr)


if __name__ == '__main__':
    unittest.main()
