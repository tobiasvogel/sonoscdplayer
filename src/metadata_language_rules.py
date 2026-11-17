"""Safe, data-only language packs for CD metadata comparison.

Language codes are opt-in and independent from the OLED/UI locale. Packs may
declare equivalent whole words and bracketed qualifiers, never scripts/regexes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib
import unicodedata

_RULE_DIR = Path(__file__).resolve().parent / 'metadata_rules'
_CODE = re.compile(r'[a-z]{2,3}(?:-[a-z0-9]{2,8})?')


def normalize_key(text: str | None) -> str:
    """NFKC, casefold and Latin accent tolerance; preserve non-Latin marks.

    No transliteration between scripts. Original text is left untouched.
    """
    if not text:
        return ''
    decomposed = unicodedata.normalize(
        'NFD', unicodedata.normalize('NFKC', text).casefold())
    chars = []
    latin_base = False
    for char in decomposed:
        cat = unicodedata.category(char)
        if cat.startswith('M'):
            if latin_base and cat == 'Mn':
                continue
            chars.append(char)
            continue
        latin_base = unicodedata.name(char, '').startswith('LATIN ')
        chars.append(char if cat.startswith(('L', 'N')) else ' ')
    return ' '.join(unicodedata.normalize('NFC', ''.join(chars)).split())


@dataclass(frozen=True)
class LanguageRules:
    languages: tuple[str, ...] = ()
    token_aliases: tuple[tuple[str, ...], ...] = ()
    soft_suffixes: tuple[str, ...] = ()
    protected_suffixes: tuple[str, ...] = ()

    def normalize(self, text: str | None) -> str:
        words = normalize_key(text).split()
        replacements = {token: group[0]
                        for group in self.token_aliases for token in group}
        return ' '.join(replacements.get(word, word) for word in words)


def _list(data, field, *, max_items=100):
    if type(data) is not list or len(data) > max_items:
        raise ValueError(f'{field} must be a list of at most {max_items} strings')
    result = []
    for value in data:
        if type(value) is not str or not 1 <= len(value) <= 80:
            raise ValueError(f'{field} contains invalid text')
        key = normalize_key(value)
        if not key:
            raise ValueError(f'{field} contains an empty value')
        result.append(key)
    if len(result) != len(set(result)):
        raise ValueError(f'{field} contains duplicates')
    return result


def load_language_rules(languages: tuple[str, ...], *,
                        rules_dir: Path | None = None) -> LanguageRules:
    """Read explicit language packs with strict validation; never use UI locale."""
    if (type(languages) is not tuple or len(languages) > 20
            or any(type(code) is not str or not _CODE.fullmatch(code)
                   for code in languages)
            or len(set(languages)) != len(languages)):
        raise ValueError('rule_languages must be unique language tags')
    base = _RULE_DIR if rules_dir is None else rules_dir
    aliases = []
    soft, protected = [], []
    for code in languages:
        try:
            with (base / (code + '.toml')).open('rb') as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f'cannot load matching rules for {code}') from exc
        if type(data) is not dict or set(data) - {
                'aliases', 'soft_suffixes', 'protected_suffixes'}:
            raise ValueError(f'invalid matching rules for {code}')
        soft.extend(_list(data.get('soft_suffixes', []), 'soft_suffixes'))
        protected.extend(_list(data.get('protected_suffixes', []),
                               'protected_suffixes'))
        groups = data.get('aliases', [])
        if type(groups) is not list or len(groups) > 100:
            raise ValueError('aliases must be a list of groups')
        for group in groups:
            terms = _list(group, 'alias group', max_items=10)
            if len(terms) < 2 or any(' ' in word for word in terms):
                raise ValueError('aliases require two or more single words')
            aliases.append(tuple(terms))
    all_tokens = [word for group in aliases for word in group]
    if len(set(all_tokens)) != len(all_tokens):
        raise ValueError('overlapping aliases in language packs')
    if (set(soft) & set(protected)
            or len(soft) != len(set(soft))
            or len(protected) != len(set(protected))):
        raise ValueError('duplicate or conflicting qualifiers in language packs')
    return LanguageRules(languages, tuple(aliases), tuple(soft), tuple(protected))
