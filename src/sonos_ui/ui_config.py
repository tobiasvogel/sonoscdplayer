"""Read-only TOML configuration and explicit language preference.

No os locale detection. UI never writes configuration in this milestone.
A future settings writer must be explicitly enabled and atomically persist.
"""
from dataclasses import dataclass
from pathlib import Path
import tomllib
import logging
from .i18n import available_languages
from .text_input import DEFAULT_EXTRA_CHARACTERS, clean_extra_characters

# The UI TOML is optional: without a file at the standard location
# (config_paths.ui_config()) the built-in defaults below apply.
DEFAULT_CONFIG = None

@dataclass(frozen=True)
class UIConfig:
    language: str = 'de'
    cover_mode: str = 'auto'
    # Characters offered in addition to ASCII when typing names (D-224);
    # passwords stay ASCII.
    extra_characters: str = DEFAULT_EXTRA_CHARACTERS

    def __post_init__(self):
        if self.language not in available_languages():
            raise ValueError('unsupported UI language')
        if self.cover_mode not in ('auto', 'on', 'off'):
            raise ValueError('unsupported cover mode')
        object.__setattr__(self, 'extra_characters',
                           clean_extra_characters(self.extra_characters))


def load_ui_config(path=DEFAULT_CONFIG):
    if path is None:
        import config_paths
        path = config_paths.ui_config()
    try:
        with open(path, 'rb') as f:
            doc = tomllib.load(f)
    except FileNotFoundError:
        logging.info('No UI configuration at %s; using built-in UI defaults', path)
        return UIConfig()
    ui = doc.get('ui', {})
    if not isinstance(ui, dict):
        raise ValueError('invalid [ui] configuration')
    cover = ui.get('cover', {})
    if not isinstance(cover, dict):
        raise ValueError('invalid [ui.cover] configuration')
    text_input = ui.get('text_input', {})
    if not isinstance(text_input, dict):
        raise ValueError('invalid [ui.text_input] configuration')
    extra = text_input.get('extra_characters', DEFAULT_EXTRA_CHARACTERS)
    language = ui.get('language', 'de')
    if language not in available_languages():
        logging.warning('Unsupported UI language in configuration; using English fallback')
        language = 'en'
    return UIConfig(language=language, cover_mode=cover.get('mode', 'auto'),
                    extra_characters=extra)
