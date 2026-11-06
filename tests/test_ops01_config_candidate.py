"""OPS-01 candidate generation tests. Synthetic TOML only; no production I/O."""
import hashlib
import tomllib
import unittest

from tools.config_candidate import CandidateRejected, build_candidate


REFERENCE = (
    b'[audio_startup]\n'
    b'prebuffer_before_sonos = true\n'
    b'pcm_timeout_seconds = 45\n'
    b'\n[led_outputs.ambient]\n'
    b'enabled = true\n'
    b'timeout_ms = 150\n'
    b'\n[service]\n'
    b'experimental_safe_screen = false\n'
)
CURRENT = (
    b'# locally maintained\n'
    b'[audio_startup]\n'
    b'prebuffer_before_sonos = false # intentional override\n'
    b'\n[led_outputs.ambient]\n'
    b'enabled = false\n'
    b'local_extra = "PRIVATE_VALUE"\n'
    b'\n[service]\n'
    b'experimental_safe_screen = true\n'
)


class OfflineCandidateTests(unittest.TestCase):
    def test_selected_only_preserves_all_original_settings(self):
        result, original_sha = build_candidate(
            CURRENT, REFERENCE, ('audio_startup.pcm_timeout_seconds',))
        self.assertEqual(original_sha, hashlib.sha256(CURRENT).hexdigest())
        self.assertEqual(result.count(b'pcm_timeout_seconds = 45'), 1)
        self.assertNotIn(b'timeout_ms = 150', result)
        self.assertIn(b'local_extra = "PRIVATE_VALUE"', result)
        self.assertIn(b'experimental_safe_screen = true', result)
        data = tomllib.loads(result.decode())
        self.assertFalse(data['audio_startup']['prebuffer_before_sonos'])
        self.assertTrue(data['service']['experimental_safe_screen'])
        self.assertFalse(data['led_outputs']['ambient']['enabled'])

    def test_two_selected_insertions_are_deterministic(self):
        keys = ('audio_startup.pcm_timeout_seconds',
                'led_outputs.ambient.timeout_ms')
        first, _ = build_candidate(CURRENT, REFERENCE, keys)
        second, _ = build_candidate(CURRENT, REFERENCE, tuple(reversed(keys)))
        self.assertEqual(first, second)
        self.assertEqual(tomllib.loads(first.decode())['led_outputs']['ambient']['timeout_ms'], 150)

    def test_unlisted_and_protected_keys_rejected(self):
        for key in ('service.experimental_safe_screen', 'drive_power.enabled',
                    'led_outputs.ambient.enabled', 'unknown'):
            with self.subTest(key=key), self.assertRaises(CandidateRejected):
                build_candidate(CURRENT, REFERENCE, (key,))

    def test_duplicate_and_empty_selection_rejected(self):
        key = 'audio_startup.pcm_timeout_seconds'
        for keys in ((), (key, key), [key], ('arbitrary',)):
            with self.subTest(keys=keys), self.assertRaises(CandidateRejected):
                build_candidate(CURRENT, REFERENCE, keys)

    def test_existing_key_cannot_be_overwritten(self):
        modified = CURRENT.replace(
            b'prebuffer_before_sonos = false # intentional override\n',
            b'prebuffer_before_sonos = false\npcm_timeout_seconds = 90\n')
        with self.assertRaises(CandidateRejected):
            build_candidate(modified, REFERENCE,
                            ('audio_startup.pcm_timeout_seconds',))

    def test_modified_reference_default_rejected(self):
        reference = REFERENCE.replace(b'pcm_timeout_seconds = 45',
                                      b'pcm_timeout_seconds = 90')
        with self.assertRaises(CandidateRejected):
            build_candidate(CURRENT, reference,
                            ('audio_startup.pcm_timeout_seconds',))

    def test_missing_explicit_table_fails_closed(self):
        current = b'[service]\nshutdown_policy = "stop_owned"\n'
        with self.assertRaises(CandidateRejected):
            build_candidate(current, REFERENCE,
                            ('audio_startup.pcm_timeout_seconds',))

    def test_conflicting_types_rejected(self):
        current = CURRENT.replace(b'enabled = false',
                                  b'enabled = "not a boolean"')
        with self.assertRaises(CandidateRejected):
            build_candidate(current, REFERENCE,
                            ('audio_startup.pcm_timeout_seconds',))

    def test_ambiguous_or_unsupported_table_forms_rejected(self):
        for injection in (b'[[extra]]\nid=1\n',
                          b'["quoted"]\nx=1\n',
                          b'[audio_startup]\nother=1\n'):
            with self.subTest(injection=injection), self.assertRaises(CandidateRejected):
                build_candidate(CURRENT + injection, REFERENCE,
                                ('audio_startup.pcm_timeout_seconds',))

    def test_invalid_and_noncanonical_encoding_rejected(self):
        for current in (b'', b'\xff', CURRENT.replace(b'\n', b'\r\n'),
                        CURRENT + b'\x00'):
            with self.subTest(current=current[:15]), self.assertRaises(CandidateRejected):
                build_candidate(current, REFERENCE,
                                ('audio_startup.pcm_timeout_seconds',))

    def test_does_not_touch_filesystem_or_execute_services(self):
        # Pure bytes in / bytes out; no paths, shell, service or device argument.
        result, _ = build_candidate(CURRENT, REFERENCE,
                                    ('led_outputs.ambient.timeout_ms',))
        self.assertIn(b'timeout_ms = 150', result)


if __name__ == '__main__':
    unittest.main()
