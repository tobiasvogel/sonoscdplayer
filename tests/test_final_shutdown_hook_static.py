"""Offline tests of the statically built late-shutdown C hook.

Run tools/build_final_shutdown_hook.sh before these tests.
All invocations use --dry-run and cannot write to SPI/GPIO or shut down the Pi.
"""
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / "build/shutdown-hook/sonoscdplayer-final-shutdown-hook"


class StaticFinalShutdownHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.is_file():
            raise AssertionError("Build first: sh tools/build_final_shutdown_hook.sh")

    def invoke(self, verb):
        return subprocess.run(
            [str(BINARY), verb, "--dry-run"],
            text=True, capture_output=True, timeout=5, check=True).stdout

    def test_reboot_uses_reboot_frame_regardless_of_exitrd(self):
        self.assertIn("phase=reboot", self.invoke("reboot"))

    def test_poweroff_is_final_or_delegated_to_existing_exitrd(self):
        output = self.invoke("poweroff")
        self.assertTrue(
            ("phase=final exitrd_present=0" in output or
             "phase=none exitrd_present=1" in output), output)

    def test_halt_and_kexec_never_draw_poweroff(self):
        for verb in ("halt", "kexec", "unknown"):
            with self.subTest(verb=verb):
                self.assertIn("phase=none", self.invoke(verb))

    def test_safe_to_unplug_is_never_approved(self):
        for verb in ("poweroff", "reboot", "halt"):
            with self.subTest(verb=verb):
                self.assertIn("safe-approved=0", self.invoke(verb))

    def test_invalid_invocation_rejected(self):
        result = subprocess.run([str(BINARY), "poweroff", "--unknown"],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
