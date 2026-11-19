"""Late systemd-shutdown OLED hook: contract tests and the statically built binary."""
import subprocess
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "systemd" / "sonoscdplayer-final-shutdown-hook"
RENDERER_LINE = "renderer=/usr/local/libexec/sonoscdplayer-shutdown-display"
BINARY = ROOT / "build/shutdown-hook/sonoscdplayer-final-shutdown-hook"



class FinalShutdownHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="sonoscdplayer-hook-test-")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.calls = self.directory / "calls"
        self.renderer = self.directory / "mock-renderer"
        # Shell script is intentionally run with /bin/sh, no real GPIO/SPI.
        self.renderer.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$1\" >> " + str(self.calls) + "\n",
            encoding="utf-8")
        self.renderer.chmod(0o700)
        self.exitrd = self.directory / "mock-exitrd"
        hook = HOOK.read_text(encoding="utf-8")
        self.assertEqual(hook.count(RENDERER_LINE), 1)
        hook = hook.replace(RENDERER_LINE, f"renderer={self.renderer}")
        self.hook = self.directory / "hook"
        self.hook.write_text(hook, encoding="utf-8")

    def execute(self, verb):
        result = subprocess.run(
            ["/bin/sh", str(self.hook), verb],
            capture_output=True, text=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.calls.read_text(encoding="utf-8").splitlines() if self.calls.exists() else []

    def test_poweroff_shows_final(self):
        self.assertEqual(self.execute("poweroff"), ["final"])

    def test_reboot_shows_reboot(self):
        self.assertEqual(self.execute("reboot"), ["reboot"])

    def test_halt_kexec_unknown_and_missing_verb_show_nothing(self):
        for verb in ("halt", "kexec", "unknown", ""):
            with self.subTest(verb=verb):
                self.assertEqual(self.execute(verb), [])

    def test_active_exitrd_keeps_fallback_final(self):
        self.exitrd.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.exitrd.chmod(0o700)
        self.assertEqual(self.execute("poweroff"), ["final"])

    def test_active_exitrd_does_not_suppress_reboot(self):
        self.exitrd.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.exitrd.chmod(0o700)
        self.assertEqual(self.execute("reboot"), ["reboot"])

    def test_missing_renderer_is_best_effort(self):
        self.renderer.unlink()
        self.assertEqual(self.execute("poweroff"), [])


class StaticFinalShutdownHookTests(unittest.TestCase):
    def setUp(self):
        if not BINARY.is_file():
            self.skipTest("Build first: sh tools/build_final_shutdown_hook.sh")

    def invoke(self, verb):
        return subprocess.run(
            [str(BINARY), verb, "--dry-run"],
            text=True, capture_output=True, timeout=5, check=True).stdout

    def test_reboot_uses_reboot_frame_regardless_of_exitrd(self):
        self.assertIn("phase=reboot", self.invoke("reboot"))

    def test_poweroff_always_preserves_fallback_final(self):
        output = self.invoke("poweroff")
        self.assertIn("phase=final", output)

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


if __name__ == '__main__':
    unittest.main()
