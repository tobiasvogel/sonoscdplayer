"""M4b.13: interface editor preview must match production parser."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('configure_inputs', ROOT/'tools/configure_inputs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class InputWizardTests(unittest.TestCase):
    def test_timing_bounds_and_encoder_detents(self):
        self.assertEqual(module.parse_value('encoder_input.steps_per_detent', '4'), 4)
        for value in ('0', '3', 'true'):
            with self.assertRaises(ValueError):
                module.parse_value('encoder_input.steps_per_detent', value)
        with self.assertRaises(ValueError):
            module.parse_value('power.long_press_ms', '2500')

    def test_runtime_validation_on_complete_candidate(self):
        raw = (ROOT/'config/interface.example.toml').read_text()
        candidate = module.prepare(raw, {'encoder_input.invert_direction': True})
        config = module.validate(candidate)
        self.assertTrue(config.encoder.invert_direction)
        self.assertIn('invert_direction = true', candidate)

    def test_no_pin_changes_offered(self):
        self.assertNotIn('buttons.power', module.FIELDS)
        self.assertNotIn('encoder.encoder_a', module.FIELDS)

    def test_cli_preview_never_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'interface.toml'
            content = (ROOT/'config/interface.example.toml').read_bytes()
            path.write_bytes(content)
            p = subprocess.run([sys.executable, str(ROOT/'tools/configure_inputs.py'),
                                '--config', str(path), '--set',
                                'encoder_input.invert_direction=true'],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('PREVIEW ONLY', p.stdout)
            self.assertEqual(path.read_bytes(), content)

if __name__ == '__main__':
    unittest.main()
