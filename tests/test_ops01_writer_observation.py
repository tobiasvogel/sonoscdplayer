"""Read-only OPS-01 writer observation and race detection."""
from pathlib import Path
import os
import tempfile
import unittest

from tools.config_writer_observation import sample_config_identity


class WriterObservationTests(unittest.TestCase):
    def test_stable_file_is_consistent_but_not_exclusive(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            target.write_bytes(b'[service]\nmode="PRIVATE_VALUE"\n')
            before = target.read_bytes()
            report = sample_config_identity(target)
            self.assertTrue(report["two_observations_consistent"])
            self.assertFalse(report["writer_exclusion_proven"])
            self.assertFalse(report["write_authorized"])
            self.assertEqual(target.read_bytes(), before)
            self.assertNotIn("PRIVATE_VALUE", repr(report))
            self.assertNotIn(str(target), repr(report))

    def test_missing_path_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as root:
            report = sample_config_identity(Path(root) / "missing")
            self.assertFalse(report["two_observations_consistent"])
            self.assertFalse(report["write_authorized"])

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            target.write_bytes(b"x=1\n")
            link = Path(root) / "linked.toml"
            link.symlink_to(target)
            report = sample_config_identity(link)
            self.assertFalse(report["two_observations_consistent"])

    def test_directory_is_not_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(sample_config_identity(root)[
                "two_observations_consistent"])

    def test_in_place_change_between_samples_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            target.write_bytes(b"x=1\n")
            from tools.config_writer_observation import _observe
            calls = 0
            def observe(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    target.write_bytes(b"x=2\n")
                return _observe(path)
            report = sample_config_identity(target, observer=observe)
            self.assertFalse(report["two_observations_consistent"])

    def test_atomic_replacement_between_samples_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            replacement = Path(root) / "staged.toml"
            target.write_bytes(b"x=1\n")
            replacement.write_bytes(b"x=1\n")
            from tools.config_writer_observation import _observe
            calls = 0
            def observe(path):
                nonlocal calls
                calls += 1
                if calls == 2:
                    os.replace(replacement, target)
                return _observe(path)
            report = sample_config_identity(target, observer=observe)
            self.assertFalse(report["two_observations_consistent"])

    def test_observation_error_does_not_claim_stability(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            target.write_bytes(b"x=1\n")
            def fail(_path):
                raise OSError("PRIVATE_ERROR_VALUE")
            report = sample_config_identity(target, observer=fail)
            self.assertFalse(report["two_observations_consistent"])
            self.assertNotIn("PRIVATE_ERROR_VALUE", repr(report))

    def test_oversized_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "config.toml"
            target.write_bytes(b"x" * (1024 * 1024 + 1))
            report = sample_config_identity(target)
            self.assertFalse(report["two_observations_consistent"])


if __name__ == "__main__":
    unittest.main()
