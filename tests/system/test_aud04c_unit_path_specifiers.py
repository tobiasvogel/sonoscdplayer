"""AUD-04c: rendered systemd paths must not expand unit specifiers."""
import pytest

from tools.render_runtime_units import absolute_path, render


@pytest.mark.parametrize('path', (
    '/opt/sonos%n/src',
    '/opt/%i/python',
    '/etc/sonos%p/player.toml',
    '/opt/100%ready/src',
))
def test_systemd_specifiers_are_rejected(path):
    with pytest.raises(ValueError, match='whitespace/control characters or %'):
        absolute_path(path)


def test_normal_absolute_path_is_unchanged():
    assert absolute_path('/opt/sonoscdplayer/src') == '/opt/sonoscdplayer/src'


@pytest.mark.parametrize('parameter', ('checkout', 'python_venv'))
def test_renderer_rejects_specifier_paths_before_reading_units(tmp_path, parameter):
    args = dict(checkout='/opt/sonoscdplayer/src',
                python_venv='/opt/sonoscdplayer/.venv/bin/python',
                user='sonoscdplayer')
    args[parameter] = '/opt/sonos%n/src'
    with pytest.raises(ValueError, match='whitespace/control characters or %'):
        render(tmp_path, **args)
