"""Regression: dashboard must not crash older service test doubles."""
from types import SimpleNamespace

from sonoscdplayer.player.web_dashboard import DashboardPublisher, dashboard_tracks


def _selection(*speakers):
    return SimpleNamespace(speakers=lambda: speakers)


def test_dashboard_with_controller_missing_all_optional_attributes():
    # Legacy socket, EJECT and wake tests use controllers without metadata,
    # transport ownership or even a player state.
    controller = SimpleNamespace()
    assert dashboard_tracks(controller) == []
    publisher = DashboardPublisher(controller, _selection(), resolve=lambda _: {})
    try:
        info = publisher.read()['dashboard']
        assert info['track_count'] == 0
        assert info['tracks'] == []
        assert info['playing_on_selection'] is False
        assert info['speaker_count'] == 0
        assert info['album'] is None
        publisher.publish(controller, _selection())
    finally:
        publisher.close()


def test_dashboard_with_missing_metadata_but_active_playback():
    controller = SimpleNamespace(
        state=SimpleNamespace(
            mode=SimpleNamespace(value='PLAYING'), track_numbers=(1, 2)),
        owned_url='owned', uid='RINCON_TEST',
    )
    publisher = DashboardPublisher(controller, _selection(), resolve=lambda _: {})
    try:
        info = publisher.read()['dashboard']
        assert info['playing_on_selection'] is True
        assert info['speaker_count'] == 1
        assert info['track_count'] == 2
        assert info['tracks'] == [
            {'number': 1, 'title': None, 'artist': None},
            {'number': 2, 'title': None, 'artist': None},
        ]
    finally:
        publisher.close()


def test_dashboard_never_infers_ownership_from_playback_mode():
    controller = SimpleNamespace(
        state=SimpleNamespace(mode=SimpleNamespace(value='PLAYING'),
                              track_numbers=(1,)),
        metadata_snapshot={'tracks': [{'number': 1, 'title': 'Track 1'}]},
    )
    publisher = DashboardPublisher(controller, _selection(), resolve=lambda _: {})
    try:
        info = publisher.read()['dashboard']
        assert info['playing_on_selection'] is False
        assert info['tracks'][0]['title'] == 'Track 1'
    finally:
        publisher.close()
