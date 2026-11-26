"""The packaged disc replaces missing covers without an opaque background tile."""
from PIL import Image, ImageOps
from sonoscdplayer.ui.renderers import oled_128
from sonoscdplayer.system.package_paths import ASSETS


def test_missing_cover_uses_disc_and_preserves_transparent_pixels(tmp_path):
    renderer = oled_128.Renderer(cover_mode='on')
    for path in (None, tmp_path/'missing.jpg'):
        canvas = Image.new('RGB', (128, 128), 'black')
        renderer.cover(canvas, path)
        expected = Image.new('RGB', (128, 128), 'black')
        with Image.open(ASSETS/'blank_disc.png') as source:
            thumb = ImageOps.contain(source.convert('RGBA'), (30, 30),
                                    method=Image.Resampling.LANCZOS)
        expected.paste(thumb, (4+(36-thumb.width)//2, 17+(36-thumb.height)//2), thumb)
        assert canvas.tobytes() == expected.tobytes()
        assert canvas.getpixel((4, 17)) == (0, 0, 0)
        assert canvas.getpixel((21, 35)) == (0, 0, 0)
        assert canvas.getbbox() is not None
        assert canvas.crop((40, 0, 128, 128)).getbbox() is None


def test_real_cover_still_takes_precedence(tmp_path):
    path = tmp_path/'cover.png'
    Image.new('RGB', (40, 40), 'red').save(path)
    image = Image.new('RGB', (128, 128), 'black')
    oled_128.Renderer().cover(image, path)
    assert set(image.crop((4, 17, 40, 53)).getdata()) == {(255, 0, 0)}


def test_disc_thumbnail_is_cached():
    oled_128._blank_disc.cache_clear()
    assert oled_128._blank_disc(36) is oled_128._blank_disc(36)
    assert oled_128._blank_disc.cache_info().misses == 1
