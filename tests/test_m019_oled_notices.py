import unittest

from integrations.outputs.oled_status import (
    drive_fault,
    drive_waking,
    drive_fault_frame,
    drive_wait_frame,
    modern_status_frame,
    status_frame,
    preserve_display_for_shutdown,
    safe_modern_status_frame,
)


class M019OLEDNoticeTests(unittest.TestCase):

    def test_wait_notice_in_legacy_and_modern(self):
        state = {
            'player': {'mode': 'STOPPED'},
            'drive_power': {'idle_waking': True},
        }
        expected = bytes(drive_wait_frame().data)

        self.assertTrue(drive_waking(state))
        self.assertEqual(
            bytes(status_frame(state).data), expected
        )
        self.assertEqual(
            bytes(modern_status_frame(state).data), expected
        )

    def test_latched_fault_has_priority(self):
        state = {
            'player': {'mode': 'STOPPED'},
            'drive_power': {
                'idle_waking': True,
                'idle_wake_fault_latched': True,
            },
        }
        expected = bytes(drive_fault_frame().data)

        self.assertTrue(drive_fault(state))
        self.assertEqual(
            bytes(status_frame(state).data), expected
        )
        self.assertEqual(
            bytes(modern_status_frame(state).data), expected
        )

    def test_stale_or_missing_flags_do_not_trigger(self):
        for state in (
            None,
            {},
            {'drive_power': {}},
            {'drive_power': {'idle_waking': False}},
            {'drive_power': {'idle_wake_fault_latched': False}},
            {'drive_power': {'idle_waking': 'true'}},
        ):
            self.assertFalse(drive_waking(state))
            self.assertFalse(drive_fault(state))

    def test_existing_shutdown_and_fallback_apis_remain(self):
        self.assertTrue(callable(preserve_display_for_shutdown))
        self.assertTrue(callable(safe_modern_status_frame))



class M019OLEDPriorityTests(unittest.TestCase):

    def test_fault_precedes_waking_and_active_menu(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        menu = Mock()
        menu.active = True

        state = {
            'drive_power': {
                'idle_waking': True,
                'idle_wake_fault_latched': True,
            }
        }
        expected = bytes(drive_fault_frame().data)

        for modern in (False, True):
            for cached in (False, True):
                frame = selected_status_frame(
                    state, menu=menu,
                    ui_modern=modern, cached=cached)
                self.assertEqual(bytes(frame.data), expected)

        menu.frame.assert_not_called()

    def test_wait_precedes_active_menu(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        menu = Mock()
        menu.active = True
        state = {'drive_power': {'idle_waking': True}}
        expected = bytes(drive_wait_frame().data)

        for modern in (False, True):
            for cached in (False, True):
                frame = selected_status_frame(
                    state, menu=menu,
                    ui_modern=modern, cached=cached)
                self.assertEqual(bytes(frame.data), expected)

        menu.frame.assert_not_called()

    def test_normal_menu_remains_available(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        sentinel = object()
        menu = Mock()
        menu.active = True
        menu.frame.return_value = sentinel

        result = selected_status_frame(
            {'drive_power': {}}, menu=menu, ui_modern=True)

        self.assertIs(result, sentinel)
        menu.frame.assert_called_once()

    def test_cached_rendering_keeps_existing_fallback_split(self):
        from unittest import mock
        from integrations.outputs import oled_status

        state = {'drive_power': {}}
        normal = object()
        cached = object()

        with mock.patch.object(
            oled_status, 'safe_modern_status_frame',
            return_value=normal,
        ) as safe, mock.patch.object(
            oled_status, 'modern_status_frame',
            return_value=cached,
        ) as direct:
            self.assertIs(
                oled_status.selected_status_frame(
                    state, ui_modern=True, cached=False),
                normal,
            )
            self.assertIs(
                oled_status.selected_status_frame(
                    state, ui_modern=True, cached=True),
                cached,
            )
            safe.assert_called_once()
            direct.assert_called_once()


if __name__ == '__main__':
    unittest.main()
