"""Validated, deterministic UI localization with English fallback."""
import json
from functools import lru_cache
from importlib import resources

SUPPORTED = ('de', 'en')


@lru_cache(maxsize=4)
def catalog(language):
    if language not in SUPPORTED:
        language = 'en'
    source = resources.files('sonos_ui.i18n.locales').joinpath(language + '.json')
    return json.loads(source.read_text(encoding='utf-8'))


def available_languages():
    return SUPPORTED


class Translator:
    def __init__(self, language='de'):
        self.language = language if language in SUPPORTED else 'en'

    def tr(self, key, **values):
        template = catalog(self.language).get(key, catalog('en').get(key))
        if template is None:
            raise KeyError('untranslated UI key: ' + key)
        return template.format(**values)
