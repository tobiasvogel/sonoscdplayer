"""Offline-only shutdown renderer tests. No GPIO, SPI or Sonos required."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ShutdownRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.build = Path(cls.directory.name)
        cls.executable = cls.build / 'sonoscdplayer-shutdown-display'
        pin_config = cls.build / 'display.toml'
        pin_config.write_text('[display]\ndc_pin = 22\nrst_pin = 27\n',
                              encoding='utf-8')
        cls.state = cls.build / 'state'
        cls.state.mkdir()
        result = subprocess.run(
            ['sh', str(ROOT / 'tools/build_shutdown_renderer.sh')],
            env=os.environ | {'BUILD_DIR': str(cls.build), 'DISPLAY_CONFIG': str(pin_config),
                              # built-in UI defaults (de), never an installed ui.toml
                              'SONOSCDPLAYER_CONFIG_DIR': str(cls.build / 'no-config')},
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_renderer(self, *args):
        # Never read the language of an installed device.
        return subprocess.run([str(self.executable), *args], capture_output=True, text=True,
                              env=os.environ | {'SONOSCDPLAYER_STATE_DIR': str(self.state)})

    def set_menu_language(self, content):
        path = self.state / 'ui.json'
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding='utf-8')
        self.addCleanup(path.unlink, missing_ok=True)

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
        import sys
        sys.path.insert(0, str(ROOT / 'src'))
        from sonoscdplayer.ui.i18n import available_languages
        text = (self.build / 'shutdown_frames.h').read_text()
        for language in available_languages():
            for phase in ('wait', 'final', 'reboot', 'transition'):
                self.assertIn(f'shutdown_frame_{phase}_{language}[8192]', text)
            self.assertIn(f'{{"{language}", shutdown_frame_wait_{language},', text)
        self.assertIn(f'SHUTDOWN_FRAME_SET_COUNT {len(available_languages())}u', text)
        self.assertIn('SHUTDOWN_DEFAULT_LANGUAGE "de"', text)
        self.assertIn('SHUTDOWN_FRAME_FORMAT "gray4-packed"', text)

    def test_language_chosen_in_menu_selects_the_frames(self):
        cases = (
            (None, 'de'),                                         # nothing chosen: ui.toml
            ('{"language": "en", "version": 1}', 'en'),
            ('{\n  "language": "de",\n  "version": 1\n}\n', 'de'),
            ('{"language": "xx", "version": 1}', 'de'),           # no such locale file
            ('{"language": "en\\"; rm", "version": 1}', 'de'),  # not a language code
            ('not json', 'de'),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                self.set_menu_language(content)
                proc = self.run_renderer('reboot', '--dry-run')
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f'language={expected} ', proc.stdout)

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
            '-I' + str(ROOT / 'src/native/shutdown'), '-I' + str(self.build),
            str(ROOT / 'src/native/shutdown/display_shutdown.c'), str(mock_source),
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
            '-I' + str(ROOT / 'src/native/shutdown'), '-I' + str(self.build),
            str(ROOT / 'src/native/shutdown/display_shutdown.c'), str(bad_source),
            '-o', str(exe),
        ], check=True)
        proc = subprocess.run([str(exe), 'wait'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn('mismatch', proc.stderr)


class InstallScreensTests(unittest.TestCase):
    """tools/install_screens.sh: boot setup plus installed shutdown programs (dry run)."""

    def fake_system(self, directory, *, installed=True, splash_tool=True):
        root = Path(directory) / 'root'
        home = Path(directory) / 'home'
        config = Path(directory) / 'etc'
        config.mkdir()
        (config / 'player.toml').write_text(
            '[led_outputs]\npower_pin = 13\nconnection_pin = 12\n'
            '[display]\ndc_pin = 22\nrst_pin = 27\n', encoding='utf-8')
        boot = root / 'boot/firmware'
        boot.mkdir(parents=True)
        (boot / 'config.txt').write_text('dtparam=spi=on\ngpio=13=op,dh\n', encoding='utf-8')
        if splash_tool:
            tool = home / 'src/utils/splashasm/splash_assembler.py'
            tool.parent.mkdir(parents=True)
            tool.write_text('# fake\n')
        if installed:
            libexec = root / 'usr/local/libexec'
            hooks = root / 'usr/lib/systemd/system-shutdown'
            (libexec / 'sonoscdplayer-exitrd').mkdir(parents=True)
            hooks.mkdir(parents=True)
            (libexec / 'sonoscdplayer-shutdown-display').write_bytes(b'\x7fELF')
            (libexec / 'sonoscdplayer-exitrd/shutdown').write_bytes(b'\x7fELF')
            (hooks / 'sonoscdplayer-final-screen').write_bytes(b'\x7fELF static')
            (hooks / 'sonoscdplayer-script').write_text('#!/bin/sh\nexit 0\n')
            (hooks / 'other-vendor-hook').write_bytes(b'\x7fELF')
        env = os.environ | {'SHUTDOWN_SCREENS_ROOT': str(root), 'HOME': str(home),
                            'SONOSCDPLAYER_CONFIG_DIR': str(config)}
        env.pop('SPLASH_ASSEMBLER', None)
        return root, env

    def run_script(self, env, *args):
        return subprocess.run(['sh', str(ROOT / 'tools/install_screens.sh'), *args], env=env,
                              capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def test_updates_boot_setup_and_exactly_the_installed_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            root, env = self.fake_system(directory)
            result = self.run_script(env, '--dry-run')
            config_txt = (root / 'boot/firmware/config.txt').read_text()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn('build_ssd1327_splash.py', out)
        self.assertIn('splashasm/splash_assembler.py', out)
        self.assertIn(f'{root}/boot/firmware/ssd1327.bin', out)
        # gpio=13 was already set by hand: it moves into the block unchanged (diff context)
        for line in ('+[all]', ' gpio=13=op,dh', '+gpio=12=op,dl', '+gpio=27=op,dh', '+splash_screen=ssd1327.bin'):
            self.assertIn(line, out)
        self.assertEqual(config_txt, 'dtparam=spi=on\ngpio=13=op,dh\n')  # dry run: untouched
        self.assertIn('build_shutdown_renderer.sh', out)
        self.assertEqual(out.count('build_final_shutdown_hook.sh'), 1)
        self.assertIn(f'{root}/usr/lib/systemd/system-shutdown/sonoscdplayer-final-screen', out)
        self.assertIn('unchanged: ' + str(root) + '/usr/lib/systemd/system-shutdown/sonoscdplayer-script', out)
        self.assertNotIn('other-vendor-hook', out)
        self.assertIn('would rebuild the experimental exitrd', out)

    def test_without_splash_tool_only_pins_are_set(self):
        with tempfile.TemporaryDirectory() as directory:
            _, env = self.fake_system(directory, installed=False, splash_tool=False)
            result = self.run_script(env, '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('splash_assembler.py not found', result.stderr)
        self.assertIn('+gpio=27=op,dh', result.stdout)
        self.assertNotIn('splash_screen', result.stdout)
        self.assertIn('No installed shutdown screen programs found', result.stderr)

    def test_conflicting_manual_pin_setting_stops_before_any_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root, env = self.fake_system(directory)
            (root / 'boot/firmware/config.txt').write_text('gpio=13=op,dl\n')
            result = self.run_script(env, '--dry-run')
        self.assertEqual(result.returncode, 3)
        self.assertIn('gpio=13=op,dl', result.stderr)
        self.assertNotIn('build_shutdown_renderer.sh', result.stdout)

    def test_only_dry_run_option(self):
        with tempfile.TemporaryDirectory() as directory:
            _, env = self.fake_system(directory)
            self.assertEqual(self.run_script(env, '--install').returncode, 2)


class BootConfigTests(unittest.TestCase):
    """tools/boot_config.py keeps one managed block in config.txt."""

    LINES = ['gpio=13=op,dh', 'gpio=12=op,dl', 'gpio=27=op,dh', 'splash_screen=ssd1327.bin']

    def update(self, text, lines=None):
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        import boot_config
        return boot_config.update(text, self.LINES if lines is None else lines)

    def test_manual_lines_move_into_one_block_and_rerun_is_stable(self):
        text = '[pi4]\narm_boost=1\n[all]\ndtparam=spi=on\ngpio=13=op,dh\ngpio=12=op,dl\n' \
               'gpio=27=op,dh\nsplash_screen=ssd1327.bin\n'
        first = self.update(text)
        self.assertEqual(first.count('gpio=13=op,dh'), 1)
        self.assertEqual(first.count('splash_screen='), 1)
        self.assertIn('[pi4]\narm_boost=1\n[all]\ndtparam=spi=on\n', first)
        self.assertTrue(first.rstrip().endswith('# END sonoscdplayer'))
        self.assertIn('[all]\ngpio=13=op,dh\ngpio=12=op,dl\ngpio=27=op,dh\nsplash_screen=ssd1327.bin\n', first)
        self.assertEqual(self.update(first), first)

    def test_changed_pins_replace_the_old_block(self):
        first = self.update('dtparam=spi=on\n')
        second = self.update(first, ['gpio=12=op,dh', 'gpio=13=op,dl', 'gpio=24=op,dh'])
        self.assertNotIn('gpio=27', second)
        self.assertNotIn('splash_screen', second)
        self.assertEqual(second.count('# BEGIN sonoscdplayer'), 1)

    def test_conflicts_are_reported_not_overwritten(self):
        import boot_config
        for text in ('gpio=13=op,dl\n', 'gpio=12-13=op,dh\n', 'splash_screen=other.bin\n'):
            with self.subTest(text=text), self.assertRaises(boot_config.ConflictError):
                self.update(text)

    def test_other_pins_and_comments_are_left_alone(self):
        text = '# gpio=13=op,dl\ngpio=5=op,dh\n'
        result = self.update(text)
        self.assertTrue(result.startswith(text))

    def test_pins_come_from_player_toml(self):
        import sys, tempfile as tf
        sys.path.insert(0, str(ROOT / 'tools'))
        import boot_config
        with tf.TemporaryDirectory() as directory:
            config = Path(directory) / 'player.toml'
            config.write_text('[led_outputs]\npower_pin = 12\nconnection_pin = 13\n')
            self.assertEqual(boot_config.desired_lines(config),
                             ['gpio=12=op,dh', 'gpio=13=op,dl', 'gpio=24=op,dh'])
            with self.assertRaises(ValueError):
                boot_config.desired_lines(config, '../evil.bin')

if __name__ == '__main__':
    unittest.main()
