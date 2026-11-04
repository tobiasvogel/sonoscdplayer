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

    def test_binary_offline_check_never_renders(self):
        result = self.call('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('safe-approved=0', result.stdout)
        self.assertIn('backend=ssd1327', result.stdout)

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

    def test_build_does_not_install_or_stage_exitrd(self):
        script = (ROOT / 'tools/build_exitrd.sh').read_text()
        self.assertNotIn('systemctl', script)
        self.assertNotIn('/run/initramfs/shutdown', script)
        self.assertNotIn('SONOSCD_EXITRD_SAFE_APPROVED=1', script)


if __name__ == '__main__':
    unittest.main()
