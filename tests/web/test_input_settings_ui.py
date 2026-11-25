"""WEB-05d2b2: input form is restricted to approved settings."""
from html.parser import HTMLParser
from pathlib import Path

from sonoscdplayer.hardware.inputs.input_settings import FIELDS
from sonoscdplayer.ui.i18n.translator import catalog


STATIC = Path(__file__).resolve().parents[2] / 'src/sonoscdplayer/web/static'


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.input_controls = set()
        self.panels = []
        self.locale_keys = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        identifier = attributes.get('id')
        if identifier:
            assert identifier not in self.ids, identifier
            self.ids.add(identifier)
            if identifier.startswith('input-'):
                self.input_controls.add(identifier)
        if tag == 'button' and 'data-panel' in attributes:
            self.panels.append(attributes['data-panel'])
        for attribute in ('data-i18n', 'data-i18n-aria-label'):
            if attribute in attributes:
                self.locale_keys.add(attributes[attribute])


def test_input_form_has_only_the_six_approved_controls():
    html = (STATIC / 'index.html').read_text(encoding='utf-8')
    page = Elements()
    page.feed(html)
    expected = {'input-poll', 'input-repeat-delay', 'input-repeat-interval',
                'input-debounce', 'input-steps', 'input-invert',
                'input-settings-save', 'input-settings-reset'}
    assert page.input_controls == expected
    assert 'inputs' in page.panels
    assert len(FIELDS) == 6
    for key in page.locale_keys:
        assert key in catalog('de') and key in catalog('en')
    for control in expected:
        assert f'id="{control}"' in html


def test_input_form_uses_revisions_and_only_the_input_endpoint():
    js = (STATIC / 'app.js').read_text(encoding='utf-8')
    assert "request('/api/settings/input', {action, revision: inputRevision, values})" in js
    assert "inputRevision = typeof section?.revision" in js
    assert "section?.editable === true" in js
    assert "inputSpecs.every(([key])" in js
    assert "renderInputSettings({input: null})" in js
    assert "['audio', 'display', 'inputs']" in js
    assert "editInputSettings('reset')" in js
    assert "editInputSettings('save')" in js
    for key in FIELDS:
        assert f"'{key}'" in js
