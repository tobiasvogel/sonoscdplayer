"""AUD-02: browser write-origin defenses without breaking REST integrations."""
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import pytest

from test_web_client import running_server


def post_with_headers(base, headers):
    request = Request(base + '/api/command', data=b'{"command":"stop"}',
                      headers={'Content-Type': 'application/json', **headers})
    try:
        with urlopen(request, timeout=4) as response:
            return response.status
    except HTTPError as exc:
        return exc.code


def test_browser_fetch_site_policy_and_origin(tmp_path):
    with running_server(tmp_path) as (base, requests):
        host = base.removeprefix('http://')
        for headers in (
            {'Sec-Fetch-Site': 'cross-site'},
            {'Sec-Fetch-Site': 'same-site'},
            {'Sec-Fetch-Site': 'invalid'},
            {'Origin': 'https://' + host},  # Same authority, different scheme.
            {'Origin': 'null'},
            {'Origin': 'http://' + host + '/unexpected'},
            {'Sec-Fetch-Site': 'cross-site', 'Origin': 'http://' + host},
        ):
            assert post_with_headers(base, headers) == 403
        assert requests == []
        for headers in (
            {},  # Existing non-browser REST clients.
            {'Origin': 'http://' + host},
            {'Origin': 'http://' + host, 'Sec-Fetch-Site': 'same-origin'},
            {'Sec-Fetch-Site': 'none'},
        ):
            assert post_with_headers(base, headers) == 200
        assert requests == [{'command': 'stop'}] * 4


def test_upload_rejects_cross_site_before_file_staging(tmp_path):
    with running_server(tmp_path) as (base, requests):
        req = Request(base + '/api/cover/upload', data=b'image', method='POST',
                      headers={'Content-Type': 'application/octet-stream',
                               'X-Disc-Token': '0' * 32,
                               'Sec-Fetch-Site': 'cross-site'})
        with pytest.raises(HTTPError) as caught:
            urlopen(req, timeout=4)
        assert caught.value.code == 403
        assert requests == []
        assert not (tmp_path / 'state' / 'cover-staging').exists()
