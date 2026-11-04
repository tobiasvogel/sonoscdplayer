"""Regression coverage for opt-in fast OLED recovery after display handover."""
import unittest

from integrations.outputs.oled_status import player_ready_timeout


class OLEDHandoverFastResumeTests(unittest.TestCase):
    def test_default_boot_wait_unchanged(self):
        self.assertEqual(player_ready_timeout(30.0, preserve=True, environ={}), 30.0)
        self.assertEqual(player_ready_timeout(30.0, preserve=False, environ={}), 30.0)

    def test_flag_without_preserve_is_inert(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(30.0, preserve=False, environ=env), 30.0)

    def test_exact_opt_in_shortens_wait(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(30.0, preserve=True, environ=env), 2.0)

    def test_never_increases_custom_short_timeout(self):
        env = {'SONOSCDPLAYER_HANDOVER_FAST_RESUME': '1'}
        self.assertEqual(player_ready_timeout(1.0, preserve=True, environ=env), 1.0)

    def test_other_env_values_do_not_enable_fast_resume(self):
        for value in ('true', 'yes', '0', ''):
            with self.subTest(value=value):
                self.assertEqual(
                    player_ready_timeout(
                        30.0, preserve=True,
                        environ={'SONOSCDPLAYER_HANDOVER_FAST_RESUME': value}),
                    30.0)


if __name__ == '__main__':
    unittest.main()
