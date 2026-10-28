"""M0.13a LED status model regression tests, no GPIO or Sonos hardware."""
import unittest

from src.integrations.outputs.led_model import Connection, Pattern, derive_led_state


def snapshot(mode='STOPPED', **sonos):
    return {'schema_version': 1, 'player': {'mode': mode, 'track': 1, 'track_count': 15, 'error': None},
            'sonos': {'owned': False, 'transport': 'STOPPED', 'target_uid': None, **sonos},
            'stream': {'active': False}}


class LedModelTests(unittest.TestCase):
    def test_operational_modes_steady_even_paused_and_no_disc(self):
        for mode in ('NO_DISC', 'STOPPED', 'PLAYING', 'PAUSED'):
            with self.subTest(mode=mode):
                self.assertEqual(derive_led_state(snapshot(mode)).power.pattern, Pattern.STEADY)

    def test_starting_breathes(self):
        self.assertEqual(derive_led_state(snapshot('STARTING')).power.pattern, Pattern.BREATHE)

    def test_error_blinks(self):
        self.assertEqual(derive_led_state(snapshot('ERROR')).power.pattern, Pattern.BLINK)

    def test_off_is_explicit_not_no_disc(self):
        result = derive_led_state(snapshot('NO_DISC'), operating=False, connection=Connection.REACHABLE)
        self.assertEqual(result.power.pattern, Pattern.OFF)
        self.assertEqual(result.connection.pattern, Pattern.OFF)

    def test_no_implicit_reachability_from_owned_or_transport(self):
        result = derive_led_state(snapshot('PLAYING', owned=True, transport='PLAYING', target_uid='FAKE_TEST_UID'))
        self.assertEqual(result.connection.pattern, Pattern.OFF)
        self.assertEqual(result.connection.reason, 'reachability_unknown')

    def test_explicit_connection_states(self):
        expected = {Connection.UNKNOWN: Pattern.OFF, Connection.UNSELECTED: Pattern.OFF,
                    Connection.CONNECTING: Pattern.BREATHE, Connection.REACHABLE: Pattern.STEADY,
                    Connection.UNREACHABLE: Pattern.SLOW_BLINK}
        for observation, pattern in expected.items():
            with self.subTest(observation=observation):
                self.assertEqual(derive_led_state(snapshot(), connection=observation).connection.pattern, pattern)

    def test_connection_independent_of_player_mode(self):
        for mode in ('STOPPED', 'STARTING', 'PLAYING', 'PAUSED', 'ERROR'):
            with self.subTest(mode=mode):
                self.assertEqual(derive_led_state(snapshot(mode), connection=Connection.REACHABLE).connection.pattern, Pattern.STEADY)

    def test_unknown_mode_fails_closed(self):
        with self.assertRaises(ValueError):
            derive_led_state(snapshot('UNRECOGNIZED'))

    def test_bad_schema_fails_closed(self):
        bad = snapshot()
        bad['schema_version'] = 2
        with self.assertRaises(ValueError):
            derive_led_state(bad)

    def test_invalid_connection_observation_rejected(self):
        with self.assertRaises(ValueError):
            derive_led_state(snapshot(), connection='reachable')

    def test_input_is_not_mutated(self):
        original = snapshot('PLAYING')
        saved = {key: dict(value) if isinstance(value, dict) else value for key, value in original.items()}
        derive_led_state(original)
        self.assertEqual(original, saved)

    def test_model_has_no_gpio_dependencies(self):
        import inspect
        import src.integrations.outputs.led_model as model
        self.assertNotIn('gpiozero', inspect.getsource(model))
        self.assertNotIn('RPi.GPIO', inspect.getsource(model))


if __name__ == '__main__':
    unittest.main()
