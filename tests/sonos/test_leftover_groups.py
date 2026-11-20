"""Groups left behind by an interrupted playback (simulated Sonos)."""
import pytest

from sonoscdplayer.player.controller import Controller
from sonoscdplayer.player.state import PlayerState
from sonoscdplayer.sonos import groups
from sonoscdplayer.sonos.speaker_checks import SpeakerGrouped, require_solo_and_stopped
from sonoscdplayer.sonos.target_selection import SelectedTarget


class Topology:
    def __init__(self, *uids):
        self.leaders = {uid: uid for uid in uids}
        self.devices = {uid: Speaker(uid, self) for uid in uids}


class Speaker:
    def __init__(self, uid, topology):
        self.uid = uid
        self.topology = topology
        self.ip_address = '192.0.2.20'
        self.transport = 'STOPPED'
        self.uri = ''
        self.unjoin_calls = 0
        self.stale = []          # group member lists reported before the real one

    @property
    def group(self):
        leader = self.topology.leaders[self.uid]
        coordinator = self.topology.devices[leader]
        if self.stale:
            uids = self.stale.pop(0)
        else:
            uids = [u for u, v in self.topology.leaders.items() if v == leader]
        members = [self.topology.devices[u] for u in uids]
        return type('Group', (), {'coordinator': coordinator, 'members': members})()

    def get_current_transport_info(self):
        return {'current_transport_state': self.transport}

    def get_current_track_info(self):
        return {'uri': self.uri}

    def join(self, coordinator):
        self.topology.leaders[self.uid] = coordinator.uid

    def unjoin(self):
        self.unjoin_calls += 1
        leaders = self.topology.leaders
        others = sorted(u for u, v in leaders.items() if v == self.uid and u != self.uid)
        for uid in others:                 # like Sonos: the next member coordinates
            leaders[uid] = others[0]
        leaders[self.uid] = self.uid


def no_wait(*_):
    pass


@pytest.fixture
def topo():
    t = Topology('A', 'B', 'C', 'D')
    # What the incident left behind: B was released, C still hangs on A.
    t.leaders['C'] = 'A'
    return t


def test_release_tolerates_a_member_reported_late(topo):
    """Sonos tells C about B's unjoin with a delay; C must still be released."""
    a, b, c = (topo.devices[u] for u in 'ABC')
    topo.leaders['B'] = 'A'
    c.stale = [['A', 'B', 'C']]
    outcome = groups.release(a, {'B': b, 'C': c}, sleep=no_wait)
    assert outcome == {'B': 'released', 'C': 'released'}
    assert topo.leaders == {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D'}


def test_release_keeps_a_member_of_a_foreign_group(topo):
    a, c, d = (topo.devices[u] for u in 'ACD')
    topo.leaders['D'] = 'A'
    assert groups.release(a, {'C': c}, sleep=no_wait) == {'C': 'kept: topology changed'}
    assert c.unjoin_calls == 0


def test_leftover_of_own_speakers_is_split(topo):
    a = topo.devices['A']
    assert groups.release_leftover(a, {'A', 'B', 'C'}, sleep=no_wait)
    assert topo.leaders['C'] == 'C'


@pytest.mark.parametrize('case', ['foreign member', 'coordinator playing'])
def test_other_groups_are_left_alone(topo, case):
    a, c = topo.devices['A'], topo.devices['C']
    if case == 'foreign member':
        topo.leaders['D'] = 'A'
    else:
        a.transport = 'PLAYING'
    assert not groups.release_leftover(a, {'A', 'B', 'C'}, sleep=no_wait)
    assert c.unjoin_calls == 0


def test_selection_splits_a_leftover_of_the_old_selection(topo):
    def verify(uid):
        speaker = topo.devices[uid]
        require_solo_and_stopped(speaker, uid)
        return speaker
    find = lambda uids: {u: topo.devices[u] for u in uids}
    selection = SelectedTarget(verify=verify, find_many=find)
    selection._uid, selection._members = 'A', ('B', 'C')       # remembered selection
    controller = Controller(PlayerState().disc_loaded((1, 2)), 'A', '192.0.2.10', 8765,
                            server=None, verify=verify, find_many=find)
    selection.set(controller, 'A')                              # now A alone
    assert topo.leaders['C'] == 'C'
    assert selection.speakers() == ['A']


def selection_with_verify(topo):
    def verify(uid):
        speaker = topo.devices[uid]
        require_solo_and_stopped(speaker, uid)
        return speaker
    selection = SelectedTarget(verify=verify, find_many=lambda uids: {})
    selection._uid, selection._members = 'A', ('B', 'C')
    controller = Controller(PlayerState().disc_loaded((1, 2)), 'A', '192.0.2.10', 8765,
                            server=None, verify=verify)
    return selection, controller


@pytest.mark.parametrize('transport', ['STOPPED', 'PAUSED_PLAYBACK'])
def test_chosen_speaker_leaves_an_idle_foreign_group_alone(topo, transport):
    """A is grouped with C and a foreign D; only A leaves, C and D stay together."""
    topo.leaders['D'] = 'A'
    topo.devices['A'].transport = transport
    selection, controller = selection_with_verify(topo)
    selection.set(controller, 'A')
    assert topo.leaders == {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'C'}


def test_playing_foreign_group_is_not_touched(topo):
    topo.leaders['D'] = 'A'
    topo.devices['A'].transport = 'PLAYING'
    selection, controller = selection_with_verify(topo)
    with pytest.raises(SpeakerGrouped):
        selection.set(controller, 'A')
    assert topo.leaders['C'] == 'A' and topo.devices['A'].unjoin_calls == 0

class MemoryGroupStore:
    def __init__(self, uids=()):
        self.uids = frozenset(uids)

    def load(self):
        return self.uids

    def save(self, uids):
        self.uids = frozenset(uids)


def test_play_after_restart_splits_a_group_with_app_added_speakers(topo):
    """Speakers added in the app were remembered in the playback group store;
    after a restart PLAY splits the whole leftover, not only the selection."""
    topo.leaders.update({'B': 'A', 'C': 'A', 'D': 'A'})     # D was added in the app
    a = topo.devices['A']
    store = MemoryGroupStore({'A', 'B', 'C', 'D'})

    def verify(uid):
        speaker = topo.devices[uid]
        require_solo_and_stopped(speaker, uid)
        return speaker

    class Server:
        running = False
        def alive(self): return self.running
        def start(self, track, **options): self.running = True
        def close(self): self.running = False

    a.play_uri = lambda url, **kw: None
    controller = Controller(PlayerState().disc_loaded((1, 2)), 'A', '192.0.2.10', 8765,
                            Server(), verify=verify,
                            find_many=lambda uids: {u: topo.devices[u] for u in uids})
    controller.use_queue = False
    controller.group_store = store
    controller.play('A', ['B', 'C'])
    assert topo.leaders['D'] == 'D'
    assert store.uids == {'A', 'B', 'C'}                     # the new playback group


def test_without_memory_a_foreign_speaker_keeps_the_group(topo, capsys):
    topo.leaders['D'] = 'A'
    assert not groups.release_leftover(topo.devices['A'], {'A', 'B', 'C'}, sleep=no_wait)
    assert '1 of them not ours' in capsys.readouterr().out


def test_playback_group_store_round_trip(tmp_path):
    from sonoscdplayer.sonos.target_store import PlaybackGroupStore
    store = PlaybackGroupStore(tmp_path, log=lambda *_: None)
    assert store.load() == frozenset()
    store.save({'RINCON_A1', 'RINCON_B1'})
    assert store.load() == {'RINCON_A1', 'RINCON_B1'}
    store.save(set())
    assert store.load() == frozenset() and not store.path.exists()
