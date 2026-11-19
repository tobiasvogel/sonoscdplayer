"""Standard location of the SonosCDPlayer configuration files.

All services and tools read their configuration from one directory,
``/etc/sonoscdplayer`` by default:

- ``player.toml``    player, streaming, drive, LED and display settings
- ``interface.toml`` buttons, rotary encoder and input behaviour
- ``ui.toml``        display language and UI preferences (optional)

The environment variable ``SONOSCDPLAYER_CONFIG_DIR`` overrides the directory
(tests, development checkouts). Templates live in ``config/*.example.toml``;
``tools/install_config.py`` creates the files at the standard location.
"""
import os
from pathlib import Path

CONFIG_DIR_ENV = 'SONOSCDPLAYER_CONFIG_DIR'
DEFAULT_CONFIG_DIR = Path('/etc/sonoscdplayer')
PLAYER_TOML = 'player.toml'
INTERFACE_TOML = 'interface.toml'
UI_TOML = 'ui.toml'


def config_dir():
    """Return the configuration directory; an override must be absolute."""
    value = os.environ.get(CONFIG_DIR_ENV)
    if not value:
        return DEFAULT_CONFIG_DIR
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f'{CONFIG_DIR_ENV} must be an absolute path')
    return path


def player_config():
    return config_dir() / PLAYER_TOML


def interface_config():
    return config_dir() / INTERFACE_TOML


def ui_config():
    return config_dir() / UI_TOML
