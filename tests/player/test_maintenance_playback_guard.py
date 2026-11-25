"""Playback entry-point gate during destructive library maintenance."""
from types import SimpleNamespace

from sonoscdplayer.player.service import maintenance_blocks_playback
from sonoscdplayer.metadata.library_maintenance import LibraryMaintenanceWorkflow


def test_destructive_maintenance_blocks_playback_entry_points():
    worker = SimpleNamespace(destructive_maintenance_pending=True)
    for command in ('play', 'play-selected', 'playpause', 'resume', 'next',
                    'previous', 'reset', 'select 1', 'select 99', ' PLAY '):
        assert maintenance_blocks_playback({'command': command}, worker)
    for command in ('status', 'stop', 'eject', 'get-target', 'help',
                    'library-query', 'volume +1', 'select 1234'):
        assert not maintenance_blocks_playback({'command': command}, worker)


def test_inactive_maintenance_does_not_block():
    for worker in (None, SimpleNamespace(destructive_maintenance_pending=False)):
        assert not maintenance_blocks_playback({'command': 'play'}, worker)
    assert not maintenance_blocks_playback({'command': 1},
                                           SimpleNamespace(destructive_maintenance_pending=True))


def test_pending_gate_covers_queued_and_finished_but_unconsumed_jobs():
    workflow = LibraryMaintenanceWorkflow()
    workflow._maintenance_job = object()  # also covers a completed, unconsumed Future
    workflow._maintenance_state = {'action': 'prune-auto'}
    assert workflow.destructive_maintenance_pending
    workflow._maintenance_state['action'] = 'stats'
    assert not workflow.destructive_maintenance_pending
    workflow._maintenance_state['action'] = 'prune-auto'
    workflow._maintenance_job = None
    assert not workflow.destructive_maintenance_pending
