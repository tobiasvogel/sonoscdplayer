"""WEB-W04a: metadata field origins disclose only proven manual overrides."""
from pathlib import Path

from sonoscdplayer.ui.i18n.translator import catalog

STATIC = Path(__file__).resolve().parents[2] / 'src' / 'sonoscdplayer' / 'web' / 'static'


def test_manual_field_indicators_and_stale_state_reset():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    for field in ('album', 'artist', 'track-title', 'track-artist'):
        assert f'id="meta-origin-{field}"' in html
    assert 'manual.includes(key)' in js
    assert "['track-title', `${track}:track_title`, entry.title]" in js
    assert "['track-artist', `${track}:track_artist`, entry.artist]" in js
    assert 'renderFieldOrigins(null, 0)' in js


def test_sources_are_localized_without_claiming_field_level_providers():
    for language in ('de', 'en'):
        strings = catalog(language)
        for kind in ('manual', 'automatic', 'unknown'):
            assert strings[f'web.metadata_origin.{kind}']
        assert strings['web.metadata_origin.automatic'] != strings['web.metadata_origin.manual']
