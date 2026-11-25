"""WEB-04c: the HTTP boundary accepts only bounded maintenance requests."""
import json

import pytest

from sonoscdplayer.web.player_client import library_maintenance_payload
from sonoscdplayer.web.library_projection import public_library


TOKEN = 'a' * 32


def test_maintenance_payload_whitelist():
    assert library_maintenance_payload({'action': 'stats'}) == {
        'command': 'library-maintenance', 'action': 'stats'}
    assert library_maintenance_payload({'action': 'bulk-delete', 'tokens': [TOKEN]}) == {
        'command': 'library-maintenance', 'action': 'bulk-delete', 'tokens': [TOKEN]}
    assert library_maintenance_payload({'action': 'clear-all',
        'confirmation': 'DELETE ALL METADATA'}) == {
        'command': 'library-maintenance', 'action': 'clear-all',
        'confirmation': 'DELETE ALL METADATA'}


@pytest.mark.parametrize('data', [
    {'action': 'stats', 'tokens': []},
    {'action': 'stats', 'path': '/var/lib/private'},
    {'action': 'bulk-delete', 'tokens': []},
    {'action': 'bulk-delete', 'tokens': [TOKEN, TOKEN]},
    {'action': 'bulk-delete', 'tokens': [TOKEN] * 41},
    {'action': 'clear-all'},
    {'action': 'clear-all', 'confirmation': True},
    {'action': 'prune-auto', 'confirmation': 'wrong'},
    {'action': 'prune-auto', 'tokens': [TOKEN], 'confirmation': 'DELETE UNUSED AUTO COVERS'},
    {'action': 'xxx'},
])
def test_invalid_payload_rejected(data):
    with pytest.raises(ValueError):
        library_maintenance_payload(data)


def test_maintenance_projection_removes_private_info():
    reply = {'ok': True, 'library': {
        'status': 'ready', 'seq': 9, 'offset': 0, 'limit': 20, 'total': 0, 'entries': [],
        'manage': None, 'maintenance': {'status': 'ready', 'seq': 3,
            'action': 'stats', 'changed_count': 0, 'error': None,
            'secret': '/var/lib/supersecret', 'stats': {
                'disc_count': 2, 'database_bytes': 512, 'auto_cover_count': 1,
                'manual_cover_count': 1, 'cover_bytes': 2048,
                'unused_auto_count': 1, 'unused_auto_bytes': 1024,
                'budget_bytes': 104857600, 'disk_path': '/var/private',
            }}}}
    sanitized = public_library(reply)
    info = sanitized['library']['maintenance']
    assert info['stats']['disc_count'] == 2
    assert 'secret' not in json.dumps(sanitized)
    assert '/var/' not in json.dumps(sanitized)
