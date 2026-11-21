"""WEB-02: cached read-only dashboard with no Sonos/drive work on reads."""
from __future__ import annotations

from types import SimpleNamespace
import time

from sonoscdplayer.player.web_dashboard import DashboardPublisher, dashboard_tracks
from sonoscdplayer.web.dashboard_projection import public_dashboard


class Controller:
    def __init__(self):
        self.state = SimpleNamespace(
            track_numbers=(1, 2, 3), mode=SimpleNamespace(value='STOPPED'))
        self.metadata_snapshot = {
            'album': 'Demo', 'tracks': [
                {'number': 1, 'title': 'First', 'artist': 'Artist 1'},
                {'number': 3, 'title': 'Last', 'artist': 'Artist 3'},
            ]}
        self.owned_url = None
        self.uid = 'RINCON_COORD'
        self.target_rev = 4


class Selection:
    def __init__(self, members):
        self.members = members

    def speakers(self):
        return self.members


def test_tracks_without_cache_read_or_drive_probe():
    ctrl = Controller()
    assert dashboard_tracks(ctrl) == [
        {'number': 1, 'title': 'First', 'artist': 'Artist 1'},
        {'number': 2, 'title': None, 'artist': None},
        {'number': 3, 'title': 'Last', 'artist': 'Artist 3'},
    ]
    ctrl.metadata_snapshot = None
    assert all(x['title'] is None for x in dashboard_tracks(ctrl))
    ctrl.state.track_numbers = ()
    assert dashboard_tracks(ctrl) == []


def test_dashboard_read_only_and_speaker_names_are_resolved_offline():
    ctrl = Controller()
    selection = Selection(['RINCON_COORD', 'RINCON_MEMBER'])
    asked = []

    def resolve(uids):
        asked.append(tuple(sorted(uids)))
        return {'RINCON_COORD': 'Music room', 'RINCON_MEMBER': 'Study'}

    pub = DashboardPublisher(ctrl, selection, resolve=resolve)
    try:
        for _ in range(100):
            pub.publish(ctrl, selection)
            if len(pub.read()['dashboard']['speaker_names']) == 2:
                break
            time.sleep(0.005)
        info = pub.read()
        assert info['ok'] is True
        assert info['dashboard']['speaker_names'] == ['Music room', 'Study']
        assert info['dashboard']['track_count'] == 3
        assert asked == [('RINCON_COORD', 'RINCON_MEMBER')]
        # Published replies are isolated copies.
        info['dashboard']['tracks'].clear()
        assert len(pub.read()['dashboard']['tracks']) == 3
        ctrl.owned_url = 'owned-url'
        ctrl.state.mode.value = 'PLAYING'
        pub.publish(ctrl, selection)
        assert pub.read()['dashboard']['playing_on_selection'] is True
    finally:
        pub.close()


def test_late_speaker_discovery_does_not_change_new_target():
    import threading
    gate = threading.Event()
    ctrl = Controller()
    selection = Selection(['RINCON_OLD'])

    def resolve(uids):
        if 'RINCON_OLD' in uids:
            gate.wait(0.15)
            return {'RINCON_OLD': 'Old room'}
        return {'RINCON_NEW': 'New room'}

    pub = DashboardPublisher(ctrl, selection, resolve=resolve)
    try:
        selection.members = ['RINCON_NEW']
        pub.publish(ctrl, selection)
        gate.set()
        for _ in range(100):
            pub.publish(ctrl, selection)
            if pub.read()['dashboard']['speaker_names']:
                break
            time.sleep(0.005)
        assert pub.read()['dashboard']['speaker_names'] == ['New room']
    finally:
        pub.close()


def test_public_dashboard_has_titles_and_names_not_private_ids():
    reply = {'ok': True, 'dashboard': {'track_count': 3,
        'tracks': [{'number': 1, 'title': 'Song', 'artist': 'Singer', 'path': '/home/private'},
                   {'number': 2, 'title': '  '}, {'number': 8, 'title': 'wrong'}],
        'speaker_names': ['Kitchen', 'Living room'],
        'speaker_count': 2,
        'playing_on_selection': True,
        'target_rev': 4,
        'uid': 'RINCON_TEST'}}
    result = public_dashboard(reply)
    assert result == {'ok': True, 'track_count': 3,
        'tracks': [{'number': 1, 'title': 'Song', 'artist': 'Singer'},
                   {'number': 2, 'title': None, 'artist': None},
                   {'number': 3, 'title': None, 'artist': None}],
        'speaker_names': ['Kitchen', 'Living room'],
        'speaker_count': 2,
        'playing_on_selection': True, 'target_rev': 4}
    assert 'RINCON_' not in str(result)
    assert '/home/' not in str(result)


def test_malformed_dashboard_rejected():
    import pytest
    for data in ({}, {'ok': False}, {'ok': True, 'dashboard': 'bad'}):
        with pytest.raises(ValueError):
            public_dashboard(data)
