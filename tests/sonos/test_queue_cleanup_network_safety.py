"""Queue cleanup requires fresh position/URI proof and never retries a write."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.exceptions import ReadTimeout, ConnectionError as HTTPConnectionError

from sonoscdplayer.sonos.track_metadata import remove_queue_block


OWN = [f'http://192.0.2.10:8765/track/{n}.wav' for n in (1, 2, 3)]
FOREIGN = ['http://foreign.example/first', 'http://foreign.example/last']
BLOCK = {'first': 2, 'tracks': [1, 2, 3], 'urls': OWN}


class Queue:
    def __init__(self):
        self.items = [FOREIGN[0], *OWN, FOREIGN[1]]
        self.events = []
        self.get_queue = Mock(side_effect=self.read)
        self.remove_from_queue = Mock(side_effect=self.remove)

    def read(self, *, start, max_items):
        self.events.append(('read', start))
        assert max_items == 1
        return [SimpleNamespace(resources=[SimpleNamespace(uri=uri)])
                for uri in self.items[start:start + max_items]]

    def remove(self, index):
        self.events.append(('remove', index))
        self.items.pop(index)


def test_cleanup_reads_each_position_and_removes_in_reverse_order():
    player = Queue()
    assert remove_queue_block(player, BLOCK) == 3
    assert player.items == FOREIGN
    assert player.events == [('read', 3), ('remove', 3), ('read', 2),
                             ('remove', 2), ('read', 1), ('remove', 1)]


def test_shifted_queue_does_not_remove_unverified_entries():
    player = Queue()
    player.items.insert(1, 'http://foreign.example/inserted')
    before = player.items.copy()
    assert remove_queue_block(player, BLOCK) == 0
    assert player.items == before
    player.remove_from_queue.assert_not_called()


def test_replaced_entry_is_preserved_while_other_own_entries_are_removed():
    player = Queue()
    replacement = 'http://foreign.example/replacement'
    player.items[2] = replacement
    assert remove_queue_block(player, BLOCK) == 2
    assert player.items == [FOREIGN[0], replacement, FOREIGN[1]]
    assert player.events == [('read', 3), ('remove', 3), ('read', 2),
                             ('read', 1), ('remove', 1)]


@pytest.mark.parametrize('error_type', [TimeoutError, ConnectionError,
                                        ReadTimeout, HTTPConnectionError])
def test_failed_ownership_read_never_authorizes_removal(error_type):
    player = Queue()
    def read(*, start, max_items):
        if start == 2:
            raise error_type('queue read unavailable')
        return player.read(start=start, max_items=max_items)
    player.get_queue.side_effect = read
    assert remove_queue_block(player, BLOCK) == 2
    assert player.items == [FOREIGN[0], OWN[1], FOREIGN[1]]
    assert [call.args for call in player.remove_from_queue.call_args_list] == [(3,), (1,)]


@pytest.mark.parametrize('applied', [False, True])
def test_ambiguous_remove_timeout_is_not_retried(applied):
    player = Queue()
    def remove(index):
        if index == 2:
            if applied:
                player.remove(index)
            raise ReadTimeout('write outcome unknown')
        player.remove(index)
    player.remove_from_queue.side_effect = remove
    # Only acknowledged removals contribute to the count.
    assert remove_queue_block(player, BLOCK) == 2
    assert [call.args for call in player.remove_from_queue.call_args_list] == [(3,), (2,), (1,)]
    assert player.items == (FOREIGN if applied else [FOREIGN[0], OWN[1], FOREIGN[1]])


def test_full_read_outage_performs_no_writes():
    player = Queue()
    before = player.items.copy()
    player.get_queue.side_effect = ReadTimeout('offline')
    assert remove_queue_block(player, BLOCK) == 0
    assert player.items == before
    player.remove_from_queue.assert_not_called()
    assert player.get_queue.call_count == 3


def test_explicit_fresh_cleanup_after_read_recovery():
    player = Queue()
    player.get_queue.side_effect = ReadTimeout('offline')
    assert remove_queue_block(player, BLOCK) == 0
    player.remove_from_queue.assert_not_called()
    player.get_queue.side_effect = player.read
    assert remove_queue_block(player, BLOCK) == 3
    assert player.items == FOREIGN
