"""Validated UI localization with automatic JSON locale discovery.

Adding a language requires a new locales/<language>.json file with the
same translation IDs and format placeholders as en.json. No Python list
of languages needs editing.
"""
import json
import re
from functools import lru_cache
from importlib import resources

_LOCALE_NAME = re.compile(r'^[a-z]{2}(?:-[A-Za-z0-9]{2,8})*$')


def available_languages():
    """Discover locale files from the installed package, not Linux LANG."""
    directory = resources.files('sonos_ui.i18n.locales')
    languages = sorted(
        file.name[:-5] for file in directory.iterdir()
        if file.is_file() and file.name.endswith('.json')
        and _LOCALE_NAME.fullmatch(file.name[:-5])
    )
    if 'en' not in languages:
        raise RuntimeError('English fallback locale is missing')
    return tuple(languages)


@lru_cache(maxsize=32)
def catalog(language):
    if language not in available_languages():
        language = 'en'
    source = resources.files('sonos_ui.i18n.locales').joinpath(language + '.json')
    data = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in data.items()):
        raise ValueError('invalid UI locale catalog')
    return data


class Translator:
    def __init__(self, language='de'):
        self.language = language if language in available_languages() else 'en'

    def tr(self, key, **values):
        template = catalog(self.language).get(key, catalog('en').get(key))
        if template is None:
            raise KeyError('untranslated UI key: ' + key)
        return template.format(**values)

    def language_name(self, language):
        """Self-localized label, or locale code for a new incomplete file."""
        if language not in available_languages():
            raise ValueError('unsupported UI language')
        return catalog(language).get('language.name', language)
