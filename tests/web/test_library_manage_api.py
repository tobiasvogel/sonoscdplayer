"""WEB-04b: validate the HTTP boundary and do not leak private cache state."""
import pytest

from sonoscdplayer.web.player_client import library_manage_payload
from sonoscdplayer.web.library_projection import public_library


def test_library_manage_payload_whitelist():
    token = 'a' * 32
    assert library_manage_payload({'token': token, 'action': 'edit',
                                   'changes': [{'field':'album', 'value':'Neuer Titel'}]}) == {
        'command': 'library-manage', 'token': token, 'action': 'edit',
        'changes': [{'field': 'album', 'value': 'Neuer Titel'}]}
    assert library_manage_payload({'token': token, 'action': 'reset'}) == {
        'command': 'library-manage', 'token': token, 'action': 'reset'}
    with pytest.raises(ValueError):
        library_manage_payload({'token': token, 'action': 'delete', 'path':'/private'})
    with pytest.raises(ValueError):
        library_manage_payload({'token': token, 'action': 'delete', 'changes': []})


def test_library_manage_projection_public_fields_only():
    token = 'b' * 32
    original = {'ok': True, 'library': {
        'status': 'ready', 'entries': [], 'seq': 1,
        'manage': {'status': 'ready', 'seq': 2, 'token': token,
                   'action': 'edit', 'error': None, 'raw_path': '/private'},
    }}
    result = public_library(original)
    assert result['library']['manage'] == {
        'status': 'ready', 'seq': 2, 'token': token, 'action': 'edit', 'error': None}
    assert '/private' not in str(result)
