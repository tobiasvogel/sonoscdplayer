"""Exercise small text in the actual layouts, including clipping and scrolling."""
from PIL import Image, ImageDraw, ImageColor
import pytest

from sonoscdplayer.ui.renderers.oled_128 import Renderer, font
from sonoscdplayer.hardware.displays import readability_preview as preview
from sonoscdplayer.hardware.displays.frames import image_to_frame


def bottom(aa=False, elapsed=0, text='ÄÖÜ äöü ßgjpqy'):
    renderer = Renderer(use_antialiasing=aa)
    image = Image.new('RGB', (128, 128), renderer.theme.background)
    renderer.bottom(image, status=text, footer=text, elapsed=elapsed)
    return image


def test_pixel_text_has_no_blended_edges():
    renderer = Renderer()
    image = bottom()
    bg = ImageColor.getrgb(renderer.theme.background)
    fg = ImageColor.getrgb(renderer.theme.secondary)
    colors = set(image.crop((5, 100, 123, 111)).getdata())
    assert colors == {bg, fg}
    assert len(set(bottom(True).crop((5, 100, 123, 111)).getdata())) > 2


def test_umlauts_and_descenders_fit_existing_slots():
    box = ImageDraw.Draw(Image.new('L', (128, 128))).textbbox(
        (0, 0), 'ÄÖÜ äöü ßgjpqy', font=font(9, False))
    assert box[3] - box[1] <= 11
    image = bottom()
    bg = ImageColor.getrgb(Renderer().theme.background)
    assert set(image.crop((0, 0, 128, 100)).getdata()) == {bg}
    assert set(image.crop((0, 111, 128, 113)).getdata()) == {bg}


@pytest.mark.parametrize('region', [(5, 100, 123, 111), (5, 115, 123, 127)])
def test_long_text_scrolls_but_short_text_stays_still(region):
    long = preview.LONG_ROOM
    assert bottom(elapsed=0, text=long).crop(region).tobytes() != bottom(elapsed=3, text=long).crop(region).tobytes()
    assert bottom(elapsed=0, text='Bereit').tobytes() == bottom(elapsed=3, text='Bereit').tobytes()


@pytest.mark.parametrize('name,model,elapsed', preview.scenes())
def test_real_views_keep_content_and_frame_geometry(name, model, elapsed):
    pixel = preview.render_scene(model, elapsed=elapsed)
    aa = preview.render_scene(model, raster='aa', elapsed=elapsed)
    assert pixel.mode == 'RGB' and pixel.size == (128, 128)
    assert pixel.tobytes() != aa.tobytes()
    assert len(image_to_frame(pixel, color=True).data) == 32768
    assert len(image_to_frame(pixel, color=False).data) == 8192


def test_offline_preview_never_reads_display_config(monkeypatch, tmp_path):
    from sonoscdplayer.hardware.displays import pins, runtime
    def forbidden(*args, **kwargs):
        raise AssertionError('offline preview touched hardware/config')
    monkeypatch.setattr(pins, 'load_display_pins', forbidden)
    monkeypatch.setattr(runtime, 'backend_for', forbidden)
    preview.main(['--raster', 'both', '--preview-dir', str(tmp_path)])
    assert len(list(tmp_path.glob('*.png'))) == 28


@pytest.mark.parametrize('args', [['--gpio'], ['--execute'], ['--manual'], ['--seconds', 'nan']])
def test_invalid_execution_is_rejected(args):
    with pytest.raises(SystemExit) as exc:
        preview.main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize('color,interrupt', [(True, False), (False, False), (True, True)])
def test_hardware_uses_configured_backend_and_closes(monkeypatch, tmp_path, color, interrupt):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sonoscdplayer.hardware.displays import pins, runtime
    from sonoscdplayer.system import gpio_settings
    config = SimpleNamespace(controller='test-controller')
    driver = Mock()
    if interrupt:
        driver.show.side_effect = KeyboardInterrupt
    backend = Mock(color=color)
    backend.create.return_value = driver
    load = Mock(return_value=config)
    resolve = Mock(return_value=backend)
    monkeypatch.setattr(pins, 'load_display_pins', load)
    monkeypatch.setattr(runtime, 'backend_for', resolve)
    monkeypatch.setattr(gpio_settings, 'gpio_warnings_enabled', lambda path: False)
    monkeypatch.setattr(preview.time, 'sleep', lambda seconds: None)
    path = tmp_path / 'player.toml'
    args = ['--gpio', '--execute', '--display-config', str(path)]
    if interrupt:
        with pytest.raises(KeyboardInterrupt):
            preview.main(args)
    else:
        preview.main(args)
        assert driver.show.call_count == 7
    load.assert_called_once_with(path)
    resolve.assert_called_once_with(config.controller)
    backend.create.assert_called_once_with(config, gpio_warnings=False)
    driver.open.assert_called_once_with(adopt_existing=False)
    frame = driver.show.call_args.args[0]
    assert len(frame.data) == (32768 if color else 8192)
    driver.close.assert_called_once_with(blank=True)


@pytest.mark.parametrize('enabled', [True, False])
def test_config_controls_status_and_menu_text(tmp_path, enabled):
    from sonoscdplayer.ui.config import load_ui_config
    from sonoscdplayer.ui.menu.interactive_menu import InteractiveMenu
    from sonoscdplayer.ui.display_service import refresh_saved_language
    from unittest.mock import Mock
    path = tmp_path / 'ui.toml'
    path.write_text('[ui]\nuse_antialiasing = '+str(enabled).lower()+'\n')
    config = load_ui_config(path)
    assert config.use_antialiasing is enabled
    menu = InteractiveMenu(use_antialiasing=config.use_antialiasing)
    assert menu.renderer.use_antialiasing is enabled
    assert menu._navigation_renderer.use_antialiasing is enabled
    assert menu._connect_renderer.use_antialiasing is enabled
    refresh_saved_language(menu, Mock(language=Mock(return_value='en')))
    assert menu.renderer.use_antialiasing is enabled


@pytest.mark.parametrize('value', ['"false"', '1', '[]'])
def test_antialias_config_requires_boolean(tmp_path, value):
    from sonoscdplayer.ui.config import load_ui_config
    path = tmp_path / 'ui.toml'
    path.write_text('[ui]\nuse_antialiasing = '+value+'\n')
    with pytest.raises(ValueError, match='boolean'):
        load_ui_config(path)


@pytest.mark.parametrize('cover', ['on', 'off'])
def test_metadata_text_fits_before_divider(cover, monkeypatch):
    from sonoscdplayer.ui.screens.model import DisplayModel
    renderer = Renderer(cover_mode=cover)
    original = renderer.text
    calls = []
    def record(canvas, xy, value, **kwargs):
        calls.append((xy, value, kwargs))
        return original(canvas, xy, value, **kwargs)
    monkeypatch.setattr(renderer, 'text', record)
    renderer.render(DisplayModel(album='Album', artist='ÄÖÜ äöü ßgjpqy', track_count=99), elapsed=0)
    artist = next(c for c in calls if c[1] == 'ÄÖÜ äöü ßgjpqy')
    count = next(c for c in calls if '99' in str(c[1]))
    assert artist[2]['size'] == (9 if cover == 'on' else 10)
    assert count[2]['size'] == 9
    assert artist[0][1] + artist[2]['height'] <= count[0][1]
    assert count[0][1] + count[2]['height'] <= 59
    for xy, value, kw in (artist, count):
        box = ImageDraw.Draw(Image.new('L', (128,128))).textbbox((0,0),value,font=font(kw['size'],False))
        assert box[3]-box[1] <= kw['height']


@pytest.mark.parametrize('enabled', [True, False])
def test_live_service_reads_rasterization_from_ui_toml(tmp_path, monkeypatch, enabled):
    from unittest.mock import Mock
    from sonoscdplayer.ui import display_service as service
    from sonoscdplayer.hardware.displays.runtime import BackendSpec
    from sonoscdplayer.ui.renderers import oled_128
    config = tmp_path / 'ui.toml'
    config.write_text('[ui]\nuse_antialiasing = '+str(enabled).lower()+'\n')
    display = tmp_path / 'player.toml'
    display.write_text('[display]\ncontroller="ssd1351"\ndc_pin=22\nrst_pin=27\n')
    monkeypatch.setattr(BackendSpec, 'create', Mock(return_value=Mock()))
    monkeypatch.setattr(service, 'request_status', lambda path: {
        'schema_version': 1, 'player': {'mode': 'STOPPED', 'track': 3, 'track_count': 12}})
    original = oled_128.Renderer.render
    seen = []
    def render(renderer, *args, **kwargs):
        seen.append(renderer.use_antialiasing)
        return original(renderer, *args, **kwargs)
    monkeypatch.setattr(oled_128.Renderer, 'render', render)
    service.main(['--display-config', str(display), '--ui-config', str(config),
                  '--state-dir', str(tmp_path / 'state'), '--gpio', '--execute',
                  '--seconds', '0.005', '--splash-seconds', '0'])
    assert seen and all(value is enabled for value in seen)
