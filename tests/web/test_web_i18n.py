"""I18N-01: the browser uses device-selected, shared JSON catalogs only."""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import string

from sonoscdplayer.ui.i18n.translator import catalog
from sonoscdplayer.web.i18n import active_language, web_catalog


STATIC = Path(__file__).resolve().parents[2] / 'src' / 'sonoscdplayer' / 'web' / 'static'


class _Keys(HTMLParser):
    def __init__(self):
        super().__init__()
        self.keys = set()

    def handle_starttag(self, tag, attributes):
        for key, value in attributes:
            if key == 'data-i18n' or key.startswith('data-i18n-'):
                self.keys.add(value)


def test_catalogs_have_matching_web_keys_and_python_placeholders():
    de = {key: value for key, value in catalog('de').items() if key.startswith('web.')}
    en = {key: value for key, value in catalog('en').items() if key.startswith('web.')}
    assert de.keys() == en.keys()
    assert len(de) >= 180
    formatter = string.Formatter()
    for key in de:
        fields = lambda text: {name for _, name, _, _ in formatter.parse(text) if name}
        assert fields(de[key]) == fields(en[key]), key
    assert de['web.brand.tagline'] == en['web.brand.tagline'] == 'Your CDs, straight to Sonos.'
    assert 'Local music, beautifully simple' not in en.values()


def test_html_and_javascript_refer_only_to_shared_catalog_keys():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    scanner = _Keys()
    scanner.feed(html)
    keys = scanner.keys | set(re.findall(r"\btr\('([^']+)'", js))
    assert len(keys) >= 100
    assert all(key.startswith('web.') for key in keys)
    assert keys <= catalog('de').keys()
    assert keys <= catalog('en').keys()
    assert 'Local music, beautifully simple' not in html
    assert 'Your CDs, straight to Sonos.' not in html  # Catalog is the source.
    assert "request('/api/i18n')" in js


def test_web_language_follows_device_state_not_host_locale(tmp_path, monkeypatch):
    config = tmp_path / 'etc'
    config.mkdir()
    (config / 'ui.toml').write_text('[ui]\nlanguage = "en"\n', encoding='utf-8')
    monkeypatch.setenv('SONOSCDPLAYER_CONFIG_DIR', str(config))
    monkeypatch.setenv('LANG', 'fr_FR.UTF-8')
    state = tmp_path / 'var'
    state.mkdir()
    assert active_language(state) == 'en'
    ui = state / 'ui.json'
    ui.write_text('{"version":1,"language":"de"}', encoding='utf-8')
    assert active_language(state) == 'de'
    answer = web_catalog(state)
    assert answer['language'] == 'de'
    assert answer['strings']['web.text.playback'] == 'Wiedergabe'
    assert 'menu.poweroff' not in answer['strings']
    ui.write_text('{"version":1,"language":"en"}', encoding='utf-8')
    assert web_catalog(state)['strings']['web.text.playback'] == 'Playback'
    ui.write_text('{not json', encoding='utf-8')
    assert active_language(state) == 'en'  # Config fallback, not LANG.


def test_http_catalog_endpoint_reflects_ui_json_not_accept_language(tmp_path):
    from http.server import ThreadingHTTPServer
    import json
    import threading
    from urllib.request import Request, urlopen

    from sonoscdplayer.web.server import make_handler
    from sonoscdplayer.web.state import WebPreferences

    state = tmp_path / 'state'
    state.mkdir()
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(object(), WebPreferences(state)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f'http://127.0.0.1:{server.server_address[1]}/api/i18n'
    try:
        def get(accept):
            with urlopen(Request(endpoint, headers={'Accept-Language': accept}), timeout=3) as response:
                return json.load(response)
        (state / 'ui.json').write_text('{"version":1,"language":"de"}', encoding='utf-8')
        german = get('en-US,en;q=0.9')
        assert german['ok'] is True
        assert german['language'] == 'de'
        assert german['strings']['web.text.settings'] == 'Einstellungen'
        (state / 'ui.json').write_text('{"version":1,"language":"en"}', encoding='utf-8')
        english = get('de-CH,de;q=0.9')
        assert english['language'] == 'en'
        assert english['strings']['web.text.settings'] == 'Settings'
        assert english['strings']['web.brand.tagline'] == 'Your CDs, straight to Sonos.'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
