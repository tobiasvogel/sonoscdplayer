"""Offline-only shutdown renderer tests. No GPIO, SPI or Sonos required."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShutdownRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.build = Path(cls.directory.name)
        cls.executable = cls.build / 'sonoscdplayer-shutdown-display'
        pin_config = cls.build / 'display.toml'
        pin_config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
        header = subprocess.run(
            ['python3', str(ROOT / 'tools/generate_ssd1327_display_header.py'),
             '--config', str(pin_config), '--output',
             str(cls.build / 'ssd1327_display_config.h')],
            capture_output=True, text=True,
        )
        if header.returncode != 0:
            raise AssertionError(header.stdout + header.stderr)
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
            env=os.environ | {'BUILD_DIR': str(cls.build)},
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_renderer(self, *args):
        return subprocess.run([str(self.executable), *args], capture_output=True, text=True)

    def test_dry_run_does_not_access_hardware(self):
        for phase in ('transition', 'wait', 'final', 'reboot'):
            with self.subTest(phase=phase):
                proc = self.run_renderer(phase, '--dry-run')
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn('size=8192', proc.stdout)
                self.assertIn('backend=ssd1327', proc.stdout)

    def test_rejects_unknown_phase(self):
        self.assertEqual(self.run_renderer('not-a-phase', '--dry-run').returncode, 2)
        self.assertEqual(self.run_renderer('safe', '--dry-run').returncode, 2)
        self.assertEqual(self.run_renderer('wait', '--unknown').returncode, 2)

    def test_frame_sizes_and_metadata(self):
        text = (self.build / 'shutdown_frames.h').read_text()
        self.assertIn('shutdown_frame_wait[8192]', text)
        self.assertIn('shutdown_frame_final[8192]', text)
        self.assertIn('shutdown_frame_reboot[8192]', text)
        self.assertIn('shutdown_frame_transition[8192]', text)
        self.assertIn('SHUTDOWN_FRAME_FORMAT "gray4-packed"', text)

    def test_core_works_with_mock_backend(self):
        mock_source = self.build / 'mock_backend.c'
        mock_source.write_text('''
#include "display_backend.h"
#include <stdio.h>
static int show(const uint8_t *data, size_t length) {
    if (!data || length != 8192) return -1;
    printf("mock frame bytes=%zu first=%u\\n", length, data[0]);
    return 0;
}
const display_backend_t shutdown_display_backend = {
    "mock", 128, 128, 8192, "gray4-packed", show
};
''')
        mock_exe = self.build / 'mock-display'
        subprocess.run([
            shutil.which('cc') or 'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-I' + str(ROOT / 'src/shutdown'), '-I' + str(self.build),
            str(ROOT / 'src/shutdown/display_shutdown.c'), str(mock_source),
            '-o', str(mock_exe),
        ], check=True)
        for phase in ('wait', 'final'):
            proc = subprocess.run([str(mock_exe), phase], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('mock frame bytes=8192', proc.stdout)

    def test_core_rejects_mismatched_backend(self):
        bad_source = self.build / 'bad_backend.c'
        bad_source.write_text('''
#include "display_backend.h"
static int show(const uint8_t *frame, size_t size) {
    (void)frame; (void)size; return 0;
}
const display_backend_t shutdown_display_backend = {
    "wrong", 128, 128, 4096, "gray4-packed", show
};
''')
        exe = self.build / 'bad-display'
        subprocess.run([
            shutil.which('cc') or 'cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
            '-I' + str(ROOT / 'src/shutdown'), '-I' + str(self.build),
            str(ROOT / 'src/shutdown/display_shutdown.c'), str(bad_source),
            '-o', str(exe),
        ], check=True)
        proc = subprocess.run([str(exe), 'wait'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('mismatch', proc.stderr)

    def test_backend_never_resets_or_blanks_controller(self):
        source = (ROOT / 'src/shutdown/backends/ssd1327.c').read_text()
        self.assertIn('0x5c', source.lower())
        self.assertNotIn('0xae', source.lower())
        self.assertNotIn('gpio24', source.lower())


if __name__ == '__main__':
    unittest.main()
