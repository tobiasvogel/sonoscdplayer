"""Web UI translations from the same locale catalogs as the device display.

The device's ui.json menu choice wins over ui.toml. Neither the browser's
Accept-Language header nor the Linux process locale selects the language.
"""
from __future__ import annotations

from pathlib import Path

from sonoscdplayer.ui.config import load_ui_config
from sonoscdplayer.ui.i18n.translator import available_languages, catalog
from sonoscdplayer.ui.language_state import UiState


def active_language(state_dir: str | Path) -> str:
    """Use persisted device-menu language, then configured default (normally de)."""
    try:
        saved = UiState(state_dir, log=lambda *_args: None).language(available_languages())
        if saved:
            return saved
    except (OSError, ValueError, TypeError):
        pass
    try:
        return load_ui_config().language
    except (OSError, ValueError, TypeError):
        return 'de' if 'de' in available_languages() else 'en'


def web_catalog(state_dir: str | Path) -> dict:
    """Serve only web.* keys; English fills gaps in newly installed locales."""
    language = active_language(state_dir)
    strings = {key: value for key, value in catalog('en').items()
               if key.startswith('web.')}
    strings.update({key: value for key, value in catalog(language).items()
                    if key.startswith('web.')})
    return {'ok': True, 'language': language, 'strings': strings}


def web_message(state_dir: str | Path, key: str) -> str:
    """Translate public HTTP errors without exposing internal paths or details."""
    if not key.startswith('web.api.'):
        raise ValueError('invalid web message key')
    translations = web_catalog(state_dir)['strings']
    if key not in translations:
        raise KeyError(key)
    return translations[key]
