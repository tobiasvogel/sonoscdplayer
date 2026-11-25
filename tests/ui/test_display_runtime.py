"""Configuration selects the live backend, RGB UI, firmware and native frames."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from sonoscdplayer.hardware.displays.pins import DisplayPins, load_display_pins
from sonoscdplayer.hardware.displays.runtime import backend_for, BackendSpec
from sonoscdplayer.hardware.displays.ssd1351 import DisplayConfig, SSD1351
from sonoscdplayer.hardware.displays.ssd1351_backend import SSD1351Backend, master_current
from sonoscdplayer.hardware.displays.frames import image_to_frame
from sonoscdplayer.ui import display_service
from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
from sonoscdplayer.ui.renderers import oled_128
from sonoscdplayer.ui.renderers.oled_128_speakers import MultiRenderer
from sonoscdplayer.ui.renderers.oled_128_screens import render_screen, graphic_frame_from_menu
from sonoscdplayer.ui.screens.semantic_screen import SemanticScreen, ScreenItem
from tools.apply_display_config import apply_config
from tools.build_display_splash import build, frame_bytes
from tools.build_shutdown_renderer import generate
from tools.display_config import render_header

ROOT = Path(__file__).resolve().parents[2]
STATE = {'player': {'mode': 'STOPPED', 'track': 3, 'track_count': 12}}


class DisplayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        if os.environ.get('SONOSCDPLAYER_TEST_FONT_DIR'):
            fonts = patch.object(oled_128, 'FONT_DIR', Path(os.environ['SONOSCDPLAYER_TEST_FONT_DIR']))
            fonts.start()
            self.addCleanup(fonts.stop)

    def config(self, controller='ssd1351', extra=''):
        path = self.root / 'player.toml'
        path.write_text(f'[display]\ncontroller="{controller}"\ndc_pin=22\nrst_pin=27\n'+extra)
        return path

    def test_configuration_and_registry_choose_both_runtime_backends(self):
        for controller, color, size in (('ssd1327', False, 8192), ('ssd1351', True, 32768)):
            config = load_display_pins(self.config(controller))
            backend = backend_for(config.controller)
            self.assertEqual(backend.color, color)
            driver = backend.create(config)
            self.assertFalse(driver.opened)
            frame = display_service.selected_status_frame(STATE, color=backend.color)
            self.assertEqual(len(frame.data), size)

    def test_configuration_rejects_unsupported_types_and_settings(self):
        for extra in ('rotation=45', 'contrast=256', 'contrast=true', 'ambient_auto="yes"',
                      'spi_hz=9', 'color_order="unknown"', 'unknown=true'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                load_display_pins(self.config(extra=extra))
        for controller in ('st7735s', 'unknown'):
            with self.assertRaises(ValueError):
                load_display_pins(self.config(controller))
        with self.assertRaises(ValueError):
            load_display_pins(self.config('ssd1327', 'rotation=270'))

    def test_toml_alone_selects_real_service_backend_and_frame_size(self):
        for controller, size in (('ssd1327', 8192), ('ssd1351', 32768)):
            driver = Mock()
            with patch.object(BackendSpec, 'create', return_value=driver) as create, \
                 patch.object(display_service, 'request_status', return_value=STATE), \
                 contextlib.redirect_stdout(io.StringIO()):
                display_service.main(['--display-config', str(self.config(controller)),
                    '--ui-config', str(self.root/'ui.toml'), '--state-dir', str(self.root/'state'),
                    '--gpio', '--execute', '--seconds', '0.005', '--splash-seconds', '0'])
            self.assertEqual(create.call_args.args[0].controller, controller)
            driver.open.assert_called_once_with(adopt_existing=False)
            self.assertEqual(len(driver.show.call_args.args[0].data), size)
            driver.close.assert_called_once_with(blank=True)

    def test_live_encoder_menu_uses_the_same_event_loop_and_rgb_navigation(self):
        menu = InteractiveMenu()
        menu.poll = Mock()
        receiver = Mock()
        receiver.receive.side_effect = [('encoder_sw', 'pressed'), ('encoder', 'right'), None]
        driver = Mock()
        with patch.object(BackendSpec, 'create', return_value=driver), \
             patch('sonoscdplayer.ui.menu.interactive_menu.InteractiveMenu', return_value=menu), \
             patch('sonoscdplayer.ui.menu.ui_event_bridge.EventReceiver') as events, \
             patch.object(display_service, 'request_status', return_value=STATE), \
             contextlib.redirect_stdout(io.StringIO()):
            events.return_value.__enter__.return_value = receiver
            display_service.main(['--display-config', str(self.config()),
                '--ui-config', str(self.root/'ui.toml'), '--state-dir', str(self.root/'state'),
                '--gpio', '--execute', '--ui-modern', '--ui-events-socket', str(self.root/'events.sock'),
                '--seconds', '0.005', '--splash-seconds', '0'])
        self.assertTrue(menu.active)
        self.assertEqual(menu.menu_focus, 1)
        frame = driver.show.call_args.args[0]
        self.assertEqual((frame.pixel_format, len(frame.data)), ('rgb565', 32768))
        self.assertEqual(frame.data, display_service.selected_status_frame(STATE, menu=menu, color=True).data)

    def test_configured_handover_and_initial_current_reach_backend(self):
        driver = Mock()
        path = self.config(extra='contrast=64\nadopt_boot_splash=true\npreserve_on_exit=true\n')
        with patch.object(BackendSpec, 'create', return_value=driver) as create, \
             patch.object(display_service, 'request_status', return_value=STATE), \
             contextlib.redirect_stdout(io.StringIO()):
            display_service.main(['--display-config', str(path), '--ui-config', str(self.root/'ui.toml'),
                '--state-dir', str(self.root/'state'), '--gpio', '--execute', '--seconds', '0.005'])
        self.assertEqual(create.call_args.args[0].contrast, 64)
        driver.open.assert_called_once_with(adopt_existing=True)
        driver.close.assert_called_once_with(blank=False)

    def test_rgb_notices_startup_and_invalid_player_status_are_full_frames(self):
        for frame in (display_service.notice_frame('status.player_offline', color=True),
                      display_service.drive_notice_frame({'drive_power': {'idle_waking': True}}, color=True),
                      display_service.safe_modern_status_frame({'player': None}, color=True),
                      display_service.start_splash(display_service.DEFAULT_LOGO, 'de', color=True)):
            self.assertEqual((frame.pixel_format, len(frame.data)), ('rgb565', 32768))

    def test_color_is_preserved_for_all_semantic_menu_widgets(self):
        renderer = MultiRenderer()
        item = ScreenItem('a', 'Küche', focused=True, selected=True)
        for view, items in (('navigation', (item,)), ('network_status', (item,)),
                            ('targets', (item,)), ('power', (item,)), ('language', (item,)),
                            ('volume', (ScreenItem('volume', 'Volume', value='50'),))):
            screen = SemanticScreen('menu', heading='Menü', view=view, items=items)
            with self.subTest(view=view):
                image = render_screen(screen, renderer=renderer, gray=False)
                self.assertEqual(image.mode, 'RGB')
                self.assertTrue(any(r != g or g != b for _, (r, g, b) in image.getcolors(16384)))

    def test_wifi_owner_receives_rgb_request_without_status_fallthrough(self):
        menu = Mock()
        menu.semantic_screen.return_value = None
        snap = object()
        menu.netsetup_snapshot.return_value = snap
        menu.power_menu = False
        expected = Image.new('RGB', (128, 128), 'red')
        menu.wifi_entry.frame.return_value = expected
        self.assertIs(graphic_frame_from_menu(menu, now=10, gray=False), expected)
        menu.wifi_entry.frame.assert_called_once_with(menu.renderer, snap, now=10, gray=False)

    def test_master_current_mapping_and_quantization(self):
        self.assertEqual([master_current(x) for x in (0, 64, 128, 255)], [0, 4, 8, 15])
        self.assertEqual(len(set(master_current(x) for x in range(256))), 16)
        for bad in (True, -1, 256, 1.5):
            with self.assertRaises(ValueError): master_current(bad)
        transport = Mock()
        driver = SSD1351Backend(transport, sleep=lambda _: None)
        driver.open()
        transport.reset_mock()
        driver.set_contrast(128)
        transport.command.assert_not_called()
        driver.set_contrast(255)
        transport.command.assert_called_once_with(b'\xc7')
        transport.data.assert_called_once_with(b'\x0f')

    def test_firmware_adoption_preserves_ram_and_close_can_handover(self):
        transport = Mock()
        driver = SSD1351(transport, sleep=lambda _: None)
        driver.open(adopt_existing=True)
        transport.reset.assert_not_called()
        self.assertEqual([call.args[0] for call in transport.command.call_args_list], [b'\xc7', b'\xaf'])
        self.assertEqual([call.args[0] for call in transport.data.call_args_list], [b'\x08'])
        transport.reset_mock()
        driver.close(blank=False)
        transport.command.assert_not_called()
        transport.close.assert_called_once()

    def test_recovery_validates_frame_before_reinitializing(self):
        driver = SSD1351Backend(Mock(), sleep=lambda _: None)
        driver.open()
        driver.transport.reset_mock()
        with self.assertRaises(ValueError): driver.recover_once(object())
        driver.transport.close.assert_not_called()
        frame = image_to_frame(Image.new('RGB', (128, 128), 'red'), color=True)
        driver.recover_once(frame, contrast=255)
        driver.transport.reset.assert_called_once()
        self.assertIn(b'\x0f', [call.args[0] for call in driver.transport.data.call_args_list])

    def test_same_configuration_drives_firmware_and_native_frame_packing(self):
        config = load_display_pins(self.config(extra='rotation=270\ncolor_order="bgr"\nspi_hz=1000000'))
        image = Image.new('RGB', (128, 128), 'red')
        data = frame_bytes(image, config)
        self.assertEqual(len(data), 32768)
        self.assertEqual(data[:2], b'\xf8\x00')
        source = build(image, config)
        self.assertIn('oled 0xa0 0x70\n', source)
        self.assertIn('[freq 1000000]', source)
        self.assertEqual(source.count('oled [DATA_ONLY]'), 256)
        self.assertTrue(source.endswith('oled 0xaf\n'))
        header = render_header(config)
        self.assertIn('OLED_DC_PIN 22u', header)
        self.assertIn('DISPLAY_SPI_HZ 1000000u', header)

    def test_firmware_rotation_and_legacy_boot_name_migration(self):
        config = load_display_pins(self.config(extra='rotation=270'))
        image = Image.new('RGB', (128, 128), 'black')
        image.putpixel((0, 0), (255, 0, 0))
        packed = frame_bytes(image, config)
        self.assertEqual(packed[:2], b'\x00\x00')
        self.assertEqual(packed[2*127*128:2*127*128+2], b'\xf8\x00')
        from tools.boot_config import update
        text = update('dtparam=spi=on\nsplash_screen=ssd1327.bin\n',
                      ['splash_screen=display.bin'])
        self.assertNotIn('splash_screen=ssd1327.bin', text)
        self.assertEqual(text.count('splash_screen=display.bin'), 1)

    def test_native_frames_match_selected_backend_and_rotation(self):
        config = load_display_pins(self.config(extra='rotation=270'))
        screens = ROOT/'src/sonoscdplayer/assets/screens'
        output = self.root/'frames.h'
        generate(screens/'shutdown_screen.png', screens/'pleasewait_screen.png',
                 screens/'reboot_screen.png', screens/'shutdown_screen.png',
                 output, 'ssd1351', language='de', display_config=config)
        text = output.read_text()
        self.assertIn('SHUTDOWN_FRAME_BYTES 32768u', text)
        self.assertIn('SHUTDOWN_FRAME_FORMAT "rgb565"', text)
        self.assertIn('shutdown_frame_wait_de[32768]', text)

    def test_apply_preview_has_no_mutations_and_apply_uses_one_config(self):
        path = self.config()
        run = Mock()
        before = path.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            apply_config(path, run=run)
            run.assert_not_called()
            commands = apply_config(path, apply=True, run=run)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(run.call_args_list[1].kwargs['env']['DISPLAY_CONFIG'], str(path.resolve()))
        self.assertEqual(commands[-1][-1], 'sonoscdplayer-oled.service')
        self.assertEqual(path.read_bytes(), before)

    def test_failed_artifact_refresh_does_not_start_a_mismatched_backend(self):
        run = Mock(side_effect=[None, subprocess.CalledProcessError(3, 'refresh')])
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            apply_config(self.config(), apply=True, run=run)
        self.assertEqual(run.call_count, 2)


@unittest.skipUnless(shutil.which('cc'), 'C compiler required')
class NativeSSD1351Tests(unittest.TestCase):
    def test_native_backend_command_data_protocol_with_mock_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'display_config.h').write_text(render_header(DisplayPins(controller='ssd1351', dc_pin=22)))
            source = root/'mock.c'
            source.write_text(r'''
#include "display_backend.h"
#include "transport/linux_spi_gpio.h"
#include <assert.h>
#include <string.h>
static int writes, closes;
int display_io_open(display_io_t *io, const char *chip, unsigned dc, const char *spi, uint32_t speed) {
    (void)io; (void)chip;
    assert(dc == 22 && speed == 2000000 && !strcmp(spi, "/dev/spidev0.0"));
    return 0;
}
int display_io_write(display_io_t *io, int mode, const uint8_t *data, size_t n) {
    (void)io;
    static const int modes[] = {0,1,0,1,0,1,0};
    static const size_t sizes[] = {1,2,1,2,1,32768,1};
    assert(writes < 7 && mode == modes[writes] && n == sizes[writes]);
    if (writes == 0) assert(data[0] == 0x15);
    if (writes == 2) assert(data[0] == 0x75);
    if (writes == 1 || writes == 3) assert(data[0] == 0 && data[1] == 127);
    if (writes == 4) assert(data[0] == 0x5c);
    if (writes == 6) assert(data[0] == 0xaf);
    writes++;
    return 0;
}
void display_io_close(display_io_t *io) { (void)io; closes++; }
int main(void) {
    static uint8_t frame[32768];
    assert(shutdown_display_backend.show(frame, 8192) == -1);
    assert(writes == 0);
    assert(shutdown_display_backend.show(frame, sizeof(frame)) == 0);
    assert(writes == 7 && closes == 1);
    return 0;
}
''')
            target = root/'test-native'
            subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I'+str(root),
                '-I'+str(ROOT/'src/native/shutdown'), str(source),
                str(ROOT/'src/native/shutdown/backends/ssd1351.c'), '-o', str(target)], check=True)
            subprocess.run([str(target)], check=True)
