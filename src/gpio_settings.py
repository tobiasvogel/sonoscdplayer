"""Shared RPi.GPIO library settings from player.toml.

``[gpio] warnings`` (default ``false``) controls ``GPIO.setwarnings()`` for
every component that drives GPIO pins: LEDs, the early boot LED, the OLED
control pins and the button/encoder inputs. Warnings such as "This channel is
already in use" are expected during hand-overs between services and are only
useful for diagnostics.
"""
import tomllib

import config_paths


def gpio_warnings_from_document(document):
    gpio = document.get('gpio', {})
    if not isinstance(gpio, dict):
        raise ValueError('[gpio] must be a TOML table')
    value = gpio.get('warnings', False)
    if type(value) is not bool:
        raise ValueError('[gpio].warnings must be true or false')
    return value


def gpio_warnings_enabled(path=None):
    """Return [gpio].warnings; a missing file means the default (False)."""
    path = config_paths.player_config() if path is None else path
    try:
        with open(path, 'rb') as stream:
            document = tomllib.load(stream)
    except FileNotFoundError:
        return False
    return gpio_warnings_from_document(document)


def apply_gpio_warnings(gpio, enabled):
    """Call gpio.setwarnings() when the backend supports it (fakes may not)."""
    setter = getattr(gpio, 'setwarnings', None)
    if setter is not None:
        setter(bool(enabled))
