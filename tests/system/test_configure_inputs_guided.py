"""M4b.15 guided preview: no mutation and production input validation."""
from pathlib import Path
import importlib.util
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('configure_inputs', ROOT / 'tools/configure_inputs.py')
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


class GuidedInputPreviewTests(unittest.TestCase):
    def fixture(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / 'interface.toml'
        data = (ROOT / 'config/interface.example.toml').read_bytes()
        path.write_bytes(data)
        return tmp, path, data

    def test_guided_preview_does_not_modify_file(self):
        tmp, path, original = self.fixture()
        with tmp:
            answers = iter(['12', 'true', 'r'])
            lines = []
            result = inputs.interactive_preview(path, read=lambda _: next(answers),
                                                out=lines.append)
            self.assertEqual(result, {'encoder_input.invert_direction': True})
            self.assertEqual(path.read_bytes(), original)
            self.assertIn('PREVIEW ONLY', '\n'.join(lines))
            self.assertIn('--apply', '\n'.join(lines))

    def test_invalid_choice_rejected_before_staging(self):
        tmp, path, original = self.fixture()
        with tmp:
            answers = iter(['11', '3', 'r'])
            lines = []
            self.assertEqual(inputs.interactive_preview(path,
                             read=lambda _: next(answers), out=lines.append), {})
            self.assertIn('Rejected:', '\n'.join(lines))
            self.assertEqual(path.read_bytes(), original)

    def test_cancel_discards_even_valid_staging(self):
        tmp, path, original = self.fixture()
        with tmp:
            answers = iter(['12', 'true', '0'])
            self.assertIsNone(inputs.interactive_preview(path,
                              read=lambda _: next(answers), out=lambda _: None))
            self.assertEqual(path.read_bytes(), original)

    def test_missing_config_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            lines = []
            result = inputs.interactive_preview(Path(temp) / 'missing.toml',
                                                out=lines.append)
            self.assertIsNone(result)
            self.assertIn('unavailable', '\n'.join(lines))

    def test_only_known_input_fields_exposed(self):
        self.assertNotIn('buttons.power', inputs.FIELDS)
        self.assertNotIn('encoder.encoder_a', inputs.FIELDS)


class MainWizardEntryTests(unittest.TestCase):
    def test_input_entry_is_preview_only_and_returns_to_main_menu(self):
        import tomllib
        spec = importlib.util.spec_from_file_location('sonos_main_configure',
                                                      ROOT / 'tools/configure.py')
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        current = main.existing_values({'streaming': {'max_buffer_mib': 16}},
                                       tomllib.loads(main.REFERENCE.read_text()))
        tmp, path, original = GuidedInputPreviewTests().fixture()
        with tmp:
            answers = iter(['i', '0', '0'])
            self.assertIsNone(main.interactive_choices(
                current, 128, interface_path=path,
                read=lambda _: next(answers), out=lambda _: None))
            self.assertEqual(path.read_bytes(), original)

    def test_input_entry_refuses_mixed_staged_transaction(self):
        import tomllib
        spec = importlib.util.spec_from_file_location('sonos_main_configure',
                                                      ROOT / 'tools/configure.py')
        main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(main)
        current = main.existing_values({'streaming': {'max_buffer_mib': 16}},
                                       tomllib.loads(main.REFERENCE.read_text()))
        answers = iter(['1', '128', 'k', 'i', '0'])
        outputs = []
        self.assertIsNone(main.interactive_choices(
            current, 128, interface_path=Path('/should/not/be/read'),
            read=lambda _: next(answers), out=outputs.append))
        self.assertIn('Finish or revert staged', '\n'.join(outputs))


if __name__ == '__main__':
    unittest.main()
