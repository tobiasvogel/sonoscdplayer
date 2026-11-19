"""Headless checks for the installation helpers (no system changes)."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('install_runtime_services', ROOT / 'tools/install_runtime_services.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class RuntimeServiceInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.rendered = self.base / 'rendered'
        self.rendered.mkdir()
        self.units = self.base / 'units'
        self.polkit = self.base / 'polkit'
        for name in mod.UNITS[:4]:
            (self.rendered / name).write_text('[Unit]\nDescription=Test\n')
        self.target = self.base / mod.TARGET
        self.target.write_text('[Unit]\nDescription=Target\n')

    def test_plan_does_not_write_and_includes_target(self):
        files = mod.plan(self.rendered, self.units, self.polkit, target_unit=self.target)
        self.assertEqual(len(files), 5)
        self.assertFalse(self.units.exists())

    def test_refuse_modified_existing_unit(self):
        self.units.mkdir()
        (self.units / mod.UNITS[0]).write_text('Something else')
        with self.assertRaisesRegex(ValueError, 'differs'):
            mod.plan(self.rendered, self.units, self.polkit, target_unit=self.target)

    def test_refuse_dropins(self):
        path = self.units / (mod.UNITS[0] + '.d')
        path.mkdir(parents=True)
        (path / 'override.conf').write_text('anything')
        with self.assertRaisesRegex(ValueError, 'drop-ins'):
            mod.plan(self.rendered, self.units, self.polkit, target_unit=self.target)

    def test_refuse_unrendered_placeholder(self):
        (self.rendered / mod.UNITS[0]).write_text('@SONOSCDPLAYER_USER@')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            mod.plan(self.rendered, self.units, self.polkit, target_unit=self.target)

    def test_explicit_opt_in_sensor_and_early_led(self):
        sensor = self.base / mod.SENSOR
        sensor.write_text('[Unit]\nDescription=Sensor\n')
        (self.rendered / mod.UNITS[4]).write_text('[Unit]\nDescription=LED\n')
        files = mod.plan(self.rendered, self.units, self.polkit, sensor_unit=sensor,
                         target_unit=self.target, early_led=True)
        self.assertEqual(len(files), 7)

    def test_missing_requested_early_led_refused(self):
        with self.assertRaisesRegex(ValueError, 'Missing required rendered unit'):
            mod.plan(self.rendered, self.units, self.polkit,
                     target_unit=self.target, early_led=True)

    def test_optional_shutdown_units_added_only_when_requested(self):
        shutdown_dir = self.base / 'shutdown'
        shutdown_dir.mkdir()
        for name in mod.SHUTDOWN_UNITS:
            (shutdown_dir / name).write_text('[Unit]\nDescription=Shutdown test\n')
        ordinary = mod.plan(self.rendered, self.units, self.polkit,
                            target_unit=self.target)
        selected = mod.plan(self.rendered, self.units, self.polkit,
                            target_unit=self.target, shutdown_units_dir=shutdown_dir)
        self.assertEqual(len(selected), len(ordinary) + 2)
        self.assertEqual({target.name for _, target in selected} -
                         {target.name for _, target in ordinary}, set(mod.SHUTDOWN_UNITS))

    def test_missing_optional_shutdown_unit_fails_closed(self):
        shutdown_dir = self.base / 'shutdown'
        shutdown_dir.mkdir()
        (shutdown_dir / mod.SHUTDOWN_UNITS[0]).write_text('test')
        with self.assertRaisesRegex(ValueError, 'Missing requested shutdown unit'):
            mod.plan(self.rendered, self.units, self.polkit,
                     target_unit=self.target, shutdown_units_dir=shutdown_dir)

    def test_preview_no_side_effects(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/install_runtime_services.py'),
                                 '--rendered-dir', str(self.rendered), '--target-unit', str(self.target),
                                 '--unit-dir', str(self.units), '--polkit-dir', str(self.polkit)],
                                capture_output=True, text=True, check=True)
        self.assertIn('Preview only', result.stdout)
        self.assertFalse(self.units.exists())


class SetupShellTests(unittest.TestCase):
    def test_syntax(self):
        subprocess.run(['bash', '-n', str(ROOT / 'scripts/setup.sh')], check=True)

    def test_no_choice_without_terminal(self):
        result = subprocess.run(['bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
                                 '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing --packages_opt selection', result.stderr)

    def test_sensor_without_services_refused_before_install(self):
        result = subprocess.run([
            'bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
            '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv',
            '--packages', 'no', '--spi', 'no', '--i2c', 'no',
            '--config', 'no', '--sensor', 'yes', '--services', 'no',
            '--screens', 'no', '--color', 'never'],
            stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires --services yes', result.stderr)

    def test_noninteractive_screens_requires_splash_choice(self):
        cmd = ['bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
               '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv',
               '--packages', 'no', '--spi', 'no', '--i2c', 'no',
               '--config', 'no', '--sensor', 'no', '--services', 'no',
               '--screens', 'yes', '--color', 'never']
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Missing --assembler selection', result.stderr)

    def test_shutdown_service_option_needs_screen_choice(self):
        cmd = ['bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
               '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv',
               '--packages', 'no', '--spi', 'no', '--i2c', 'no',
               '--config', 'no', '--sensor', 'no', '--services', 'no',
               '--screens', 'no', '--shutdown-services', 'yes', '--color', 'never']
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('require --screens yes', result.stderr)

    def test_valid_selected_screens_preview(self):
        cmd = ['bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
               '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv',
               '--packages', 'no', '--spi', 'no', '--i2c', 'no',
               '--config', 'no', '--sensor', 'no', '--services', 'no',
               '--screens', 'yes', '--splash-assembler', 'yes', '--color', 'never']
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('SplashAssembler=yes', result.stdout)

    def test_fully_specified_preview(self):
        result = subprocess.run(['bash', str(ROOT / 'scripts/setup.sh'), '--dry-run',
                                 '--service-user', 'nobody', '--venv', '/tmp/setup-test-venv',
                                 '--packages', 'no', '--spi', 'no', '--i2c', 'no',
                                 '--config', 'no', '--sensor', 'no', '--services', 'no',
                                 '--screens', 'no', '--color', 'never'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Preview only', result.stdout)
        self.assertNotIn('\x1b[', result.stdout)


if __name__ == '__main__':
    unittest.main()
