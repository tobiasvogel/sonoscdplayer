"""WEB-06b: metadata diagnostics are read-only, localized, and bounded."""
from pathlib import Path

from sonoscdplayer.ui.i18n.translator import catalog

STATIC = Path(__file__).resolve().parents[2] / 'src' / 'sonoscdplayer' / 'web' / 'static'


def test_source_diagnostics_use_only_player_status_and_no_commands():
    page = (STATIC / 'index.html').read_text(encoding='utf-8')
    script = (STATIC / 'app.js').read_text(encoding='utf-8')
    for key in ('cdtext', 'lookup', 'match'):
        assert f'id="metadata-{key}"' in page
        assert f'data-i18n="web.metadata_status.{key}"' in page
    assert "['cdtext', 'cdtext_status', '#metadata-cdtext']" in script
    assert "['lookup', 'lookup_status', '#metadata-lookup']" in script
    assert "['match', 'match_status', '#metadata-match']" in script
    source_function = script.split('function renderMetadataSourceStatus(', 1)[1].split('function renderStatus(', 1)[0]
    assert "request(" not in source_function
    assert "innerHTML" not in source_function
    assert "'unknown'" in source_function
    assert 'renderMetadataSourceStatus(null);' in script


def test_source_diagnostics_catalogs_have_matching_keys():
    de = {k for k in catalog('de') if k.startswith('web.metadata_status.')}
    en = {k for k in catalog('en') if k.startswith('web.metadata_status.')}
    assert de == en
    assert {'web.metadata_status.' + key for key in
            ('title', 'cdtext', 'lookup', 'match', 'no_disc', 'unknown',
             'available', 'error', 'offline', 'toc', 'cached', 'gnudb')} <= de
