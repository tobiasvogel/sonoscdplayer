"""What the systemd units must guarantee, in one place.

systemd ordering and lifecycle cannot be exercised without systemd, so these
tests read the unit templates. They check only properties that matter for
shutdown, state and safety, not the files line by line.
"""
from pathlib import Path

import pytest

SYSTEMD = Path(__file__).resolve().parents[2] / 'systemd'


def unit_settings(name):
    """{key: [values]} of a unit file (all sections; later values appended)."""
    settings = {}
    for line in (SYSTEMD / name).read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';', '[')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        settings.setdefault(key.strip(), []).append(value.strip())
    return settings


def words(settings, key):
    """All space-separated words of a key, e.g. every unit named in After=."""
    return {word for value in settings.get(key, []) for word in value.split()}


# (unit, key, word that must appear)
REQUIRED = [
    # The display stops before the player, so it can still show the shutdown.
    ('sonoscdplayer-oled.service', 'After', 'sonoscdplayer-player.service'),
    # Player and display share the private state directory.
    ('sonoscdplayer-oled.service', 'StateDirectory', 'sonoscdplayer'),
    ('sonoscdplayer-oled.service', 'StateDirectoryMode', '0700'),
    ('sonoscdplayer-player.service', 'StateDirectory', 'sonoscdplayer'),
    ('sonoscdplayer-player.service', 'StateDirectoryMode', '0700'),
    # The shutdown screen is drawn after the display service has stopped.
    ('sonoscdplayer-shutdown-screen.service', 'Before', 'sonoscdplayer-oled.service'),
    ('sonoscdplayer-shutdown-screen.service', 'RemainAfterExit', 'yes'),
    ('sonoscdplayer-shutdown-screen.service', 'ExecStop', 'transition'),
    ('sonoscdplayer-shutdown-screen.service', 'ConditionFileIsExecutable',
     '/usr/local/libexec/sonoscdplayer-shutdown-display'),
    # The sentinel marks a shutdown right after the display service stopped.
    ('sonoscdplayer-oled-shutdown-sentinel.service', 'After', 'sonoscdplayer-oled.service'),
    ('sonoscdplayer-oled-shutdown-sentinel.service', 'RemainAfterExit', 'yes'),
    ('sonoscdplayer-oled-shutdown-sentinel.service', 'WantedBy', 'sonoscdplayer.target'),
    # exitrd is re-armed at every boot, once.
    ('sonoscdplayer-exitrd.service', 'WantedBy', 'multi-user.target'),
    ('sonoscdplayer-exitrd.service', 'Type', 'oneshot'),
    ('sonoscdplayer-exitrd.service', 'After', 'local-fs.target'),
]

# (unit, key) that must not be set
FORBIDDEN_KEYS = [
    # exitrd must do nothing during shutdown itself.
    ('sonoscdplayer-exitrd.service', 'ExecStop'),
    ('sonoscdplayer-exitrd.service', 'RemainAfterExit'),
    # The display drop-in only sets an environment variable.
    ('sonoscdplayer-oled.service.d/exit-handover.conf.example', 'ExecStart'),
]


@pytest.mark.parametrize('unit, key, word', REQUIRED)
def test_required_setting(unit, key, word):
    assert word in words(unit_settings(unit), key), f'{unit}: {key}= lacks {word}'


@pytest.mark.parametrize('unit, key', FORBIDDEN_KEYS)
def test_forbidden_setting(unit, key):
    assert key not in unit_settings(unit)


def test_shutdown_screen_is_not_started_at_boot_on_its_own():
    assert 'multi-user.target' not in words(
        unit_settings('sonoscdplayer-shutdown-screen.service'), 'WantedBy')


def test_display_handover_dropin_sets_the_preserve_flag():
    environment = unit_settings('sonoscdplayer-oled.service.d/exit-handover.conf.example')
    assert environment.get('Environment') == ['SONOSCDPLAYER_PRESERVE_DISPLAY_ON_EXIT=1']


@pytest.mark.parametrize('name', ['sonoscdplayer-shutdown-screen.service',
                                  'sonoscdplayer-final-shutdown-hook',
                                  'sonoscdplayer-exitrd.service'])
def test_shutdown_parts_never_claim_it_is_safe_to_unplug(name):
    # "Safe to unplug" may only be shown once the hardware path proves it.
    text = (SYSTEMD / name).read_text(encoding='utf-8').lower()
    assert 'safetounplug' not in text
    assert 'phase=safe' not in text
