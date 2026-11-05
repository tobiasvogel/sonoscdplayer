"""Fail-closed, non-activating exitrd preflight regression tests.

All ELF fixtures are synthetic. No system poweroff or live exitrd staging.
"""
import contextlib
import io
import os
from pathlib import Path
import platform
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import exitrd_preflight as preflight  # noqa: E402


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



# Additional non-activating contracts for the explicit one-boot staging helper.
import exitrd_once as staging  # noqa: E402


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

    def test_activation_dispatch_can_be_mocked(self):
        with patch.object(staging, "precheck", return_value=(b"payload", "f" * 64, {})):
            with patch.object(staging, "arm_once") as arm:
                self.assertEqual(staging.main(["--activate-once",
                                  "--expected-sha256", "f" * 64,
                                  "--acknowledge", staging.ACK]), 0)
                arm.assert_called_once_with(b"payload", "f" * 64, {})

if __name__ == "__main__":
    unittest.main()
