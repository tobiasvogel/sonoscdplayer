"""The web brand must use the original vector logo and theme-aware paint."""
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'src/sonoscdplayer/web/static/index.html'
ORIGINAL = ROOT / 'docs/logo_new.svg'


class LogoPaths(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_logo = False
        self.depth = 0
        self.geometry = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == 'svg' and data.get('class') == 'brand-logo':
            self.in_logo = True
            self.depth = 1
            assert data.get('viewbox') == '0 0 260 260'
            return
        if self.in_logo:
            if tag == 'svg':
                self.depth += 1
            if tag == 'path':
                self.geometry.append(('path', data['d']))
            if tag == 'circle':
                self.geometry.append(('circle', tuple(data[k] for k in ('cx', 'cy', 'r'))))

    def handle_endtag(self, tag):
        if self.in_logo and tag == 'svg':
            self.depth -= 1
            if self.depth == 0:
                self.in_logo = False


def test_web_uses_exact_original_logo_geometry():
    html = WEB.read_text(encoding='utf-8')
    parser = LogoPaths()
    parser.feed(html)
    original = ElementTree.parse(ORIGINAL).getroot()
    expected = []
    for element in original.iter():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'path':
            expected.append(('path', element.attrib['d']))
        elif tag == 'circle':
            expected.append(('circle', tuple(element.attrib[k] for k in ('cx', 'cy', 'r'))))
    assert len(expected) == 9
    assert parser.geometry == expected
    assert 'class="brand-mark" aria-hidden="true">◉' not in html


def test_web_logo_has_light_and_dark_theme_paint():
    css = WEB.read_text(encoding='utf-8')
    for item in ('brand-logo-frame', 'brand-logo-disc', 'brand-logo-disc-detail',
                 'brand-logo-waves'):
        assert f'.{item}{{' in css
        assert f':root[data-theme="dark"] .{item}{{' in css
