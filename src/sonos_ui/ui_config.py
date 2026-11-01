"""Read-only TOML configuration and explicit language preference.

No os locale detection. UI never writes configuration in this milestone.
A future settings writer must be explicitly enabled and atomically persist.
"""
from dataclasses import dataclass
from pathlib import Path
import tomllib
from .i18n import available_languages

DEFAULT_CONFIG = Path('/home/pi/sonoscdplayer/ui.toml')

@dataclass(frozen=True)
class UIConfig:
    language: str = 'de'
    cover_mode: str = 'auto'

    def __post_init__(self):
        if self.language not in available_languages():
            raise ValueError('unsupported UI language')
        if self.cover_mode not in ('auto', 'on', 'off'):
            raise ValueError('unsupported cover mode')


def load_ui_config(path=DEFAULT_CONFIG):
    try:
        with open(path, 'rb') as f:
            doc = tomllib.load(f)
    except FileNotFoundError:
        return UIConfig()
    ui = doc.get('ui', {})
    if not isinstance(ui, dict):
        raise ValueError('invalid [ui] configuration')
    cover = ui.get('cover', {})
    if not isinstance(cover, dict):
        raise ValueError('invalid [ui.cover] configuration')
    return UIConfig(language=ui.get('language', 'de'), cover_mode=cover.get('mode', 'auto'))
