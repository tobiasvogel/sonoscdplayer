"""INP-02 volume buttons: player gate, button repeat/coalescing, OLED overlay."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from player_state import Mode
from player_service import handle_command
from volume_control import VolumeControl, parse_volume_command
from integrations.interfaces.input_core import load_config
from integrations.interfaces.volume_buttons import VolumeExecutor, VolumeRepeat
from sonos_ui.interactive_menu import InteractiveMenu, VOLUME_OVERLAY_SECONDS
from sonos_ui.ui_event_bridge import decode_event, encode_event

ROOT = Path(__file__).resolve().parents[1]


class FakeSpeaker:
    def __init__(self, uid, volume=30, transport='STOPPED'):
        self.uid = uid
        self.volume = volume
        self.transport = transport
        self.transport_reads = 0
        self.group = NS(coordinator=self, members=[self])

    def set_relative_volume(self, step):
        self.volume = max(0, min(100, self.volume + step))
        return self.volume

    def get_current_transport_info(self):
        self.transport_reads += 1
        return {'current_transport_state': self.transport}


class FakeGroup:
    def __init__(self, coordinator, members):
        self.coordinator = coordinator
        self.members = members
        self.steps = []

    def set_relative_volume(self, step):
        self.steps.append(step)
        for member in self.members:
            member.volume = max(0, min(100, member.volume + step))
        return self.coordinator.volume


class FakeController:
    def __init__(self, mode, *, owned=True, speaker=None):
        self.state = NS(mode=mode)
        self.owned_url = 'http://example/track/1.wav' if owned else None
        self.seen_playing = owned
        self.server = NS(alive=lambda: owned)
        self.player = speaker
        self.ownership_checks = []

    def verify_existing(self):
        return self.player

    def _ownership(self, player, expected):
        self.ownership_checks.append(expected)

    def status(self):
        return 'fake'

    def status_snapshot(self):
        return {}


class FakeSelection:
    def __init__(self, uid='A', members=()):
        self.uid, self.members = uid, tuple(members)

    def uid_for_explicit_action(self):
        return self.uid

    def members_for_explicit_action(self):
        return self.members


class PlayerVolumeGateTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_volume_command('volume +3'), 3)
        self.assertEqual(parse_volume_command('volume -1'), -1)
        self.assertIsNone(parse_volume_command('stop'))
        for bad in ('volume 3', 'volume +0', 'volume +11', 'volume +x', 'volume -'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_volume_command(bad)

    def test_owned_playing_checks_ownership_and_uses_group_volume(self):
        speaker = FakeSpeaker('A')
        other = FakeSpeaker('B', volume=50)
        speaker.group = FakeGroup(speaker, [speaker, other])
        controller = FakeController(Mode.PLAYING, speaker=speaker)
        level = VolumeControl().adjust(controller, None, 2)
        self.assertEqual(controller.ownership_checks, ['PLAYING'])
        self.assertEqual(speaker.group.steps, [2])
        self.assertEqual((level, other.volume), (32, 52))

    def test_owned_paused_expects_paused_transport(self):
        speaker = FakeSpeaker('A')
        controller = FakeController(Mode.PAUSED, speaker=speaker)
        VolumeControl().adjust(controller, None, -1)
        self.assertEqual(controller.ownership_checks, ['PAUSED_PLAYBACK'])

    def test_group_without_group_volume_changes_each_member(self):
        speaker, other = FakeSpeaker('A', 20), FakeSpeaker('B', 40)
        speaker.group = NS(coordinator=speaker, members=[speaker, other])
        controller = FakeController(Mode.PLAYING, speaker=speaker)
        self.assertEqual(VolumeControl().adjust(controller, None, 1), 21)
        self.assertEqual(other.volume, 41)

    def test_ownership_failure_changes_nothing(self):
        speaker = FakeSpeaker('A')
        controller = FakeController(Mode.PLAYING, speaker=speaker)
        controller._ownership = lambda *_: (_ for _ in ()).throw(RuntimeError('foreign'))
        with self.assertRaises(RuntimeError):
            VolumeControl().adjust(controller, None, 1)
        self.assertEqual(speaker.volume, 30)

    def test_unconfirmed_or_other_modes_are_refused(self):
        speaker = FakeSpeaker('A')
        for controller in (FakeController(Mode.PLAYING, owned=False, speaker=speaker),
                           FakeController(Mode.STARTING, speaker=speaker),
                           FakeController(Mode.ERROR, speaker=speaker),
                           FakeController(Mode.NO_DISC, speaker=speaker)):
            with self.subTest(mode=controller.state.mode), self.assertRaises(ValueError):
                VolumeControl().adjust(controller, FakeSelection(), 1)
        self.assertEqual(speaker.volume, 30)

    def stopped(self, speakers, clock=lambda: 0.0):
        found = {s.uid: s for s in speakers}
        calls = []

        def find_many(uids):
            calls.append(uids)
            return {uid: found[uid] for uid in uids}
        return VolumeControl(find_many=find_many, clock=clock), calls

    def test_stopped_selected_idle_speakers_are_changed_individually(self):
        a, b = FakeSpeaker('A', 60), FakeSpeaker('B', 10)
        control, _ = self.stopped([a, b])
        controller = FakeController(Mode.STOPPED, owned=False)
        level = control.adjust(controller, FakeSelection('A', ('B',)), -5)
        self.assertEqual((level, a.volume, b.volume), (55, 55, 5))

    def test_stopped_refuses_when_a_selected_speaker_plays(self):
        a, b = FakeSpeaker('A'), FakeSpeaker('B', transport='PLAYING')
        control, _ = self.stopped([a, b])
        controller = FakeController(Mode.STOPPED, owned=False)
        with self.assertRaises(RuntimeError):
            control.adjust(controller, FakeSelection('A', ('B',)), -1)
        self.assertEqual((a.volume, b.volume), (30, 30))

    def test_stopped_checks_group_coordinator_transport(self):
        a, foreign = FakeSpeaker('A'), FakeSpeaker('X', transport='TRANSITIONING')
        a.group = NS(coordinator=foreign, members=[foreign, a])
        control, _ = self.stopped([a])
        with self.assertRaises(RuntimeError):
            control.adjust(FakeController(Mode.STOPPED, owned=False), FakeSelection('A'), -1)

    def test_idle_check_is_reused_for_a_few_seconds_then_repeated(self):
        now = [0.0]
        a = FakeSpeaker('A')
        control, calls = self.stopped([a], clock=lambda: now[0])
        controller = FakeController(Mode.STOPPED, owned=False)
        for t in (0.0, 1.0, 4.9):
            now[0] = t
            control.adjust(controller, FakeSelection('A'), -1)
        self.assertEqual(a.transport_reads, 1)
        self.assertEqual(len(calls), 1, 'speakers are discovered once')
        now[0] = 5.0
        a.transport = 'PLAYING'
        with self.assertRaises(RuntimeError):
            control.adjust(controller, FakeSelection('A'), -1)

    def test_stopped_without_selection_is_refused(self):
        control, calls = self.stopped([])
        with self.assertRaises(ValueError):
            control.adjust(FakeController(Mode.STOPPED, owned=False), FakeSelection(None), 1)
        self.assertEqual(calls, [])

    def test_changed_selection_is_rediscovered(self):
        a, b = FakeSpeaker('A'), FakeSpeaker('B')
        control, calls = self.stopped([a, b])
        controller = FakeController(Mode.STOPPED, owned=False)
        control.adjust(controller, FakeSelection('A'), 1)
        control.adjust(controller, FakeSelection('B'), 1)
        self.assertEqual(calls, [('A',), ('B',)])


class PlayerVolumeRpcTests(unittest.TestCase):
    def test_reply_contains_level(self):
        controller = FakeController(Mode.PLAYING, speaker=FakeSpeaker('A', 40))
        reply = handle_command(controller, {'command': 'volume +1'}, FakeSelection(),
                               volume=VolumeControl())
        self.assertTrue(reply['ok'], reply)
        self.assertEqual(reply['volume'], 41)

    def test_invalid_or_unavailable(self):
        controller = FakeController(Mode.PLAYING, speaker=FakeSpeaker('A'))
        for command in ('volume 1', 'volume +11', 'volume +1 +1'):
            with self.subTest(command=command):
                reply = handle_command(controller, {'command': command}, None,
                                       volume=VolumeControl())
                self.assertFalse(reply['ok'])
        self.assertFalse(handle_command(controller, {'command': 'volume +1'})['ok'])
        reply = handle_command(controller, {'command': 'volume +1', 'uid': 'A'},
                               volume=VolumeControl())
        self.assertFalse(reply['ok'], 'no UID accepted with volume')


class VolumeConfigTests(unittest.TestCase):
    def config(self, extra):
        text = (ROOT / 'config/interface.example.toml').read_text()
        text = text.replace('[volume]\nrepeat_delay_ms = 750\nrepeat_interval_ms = 250\n', extra)
        with tempfile.NamedTemporaryFile('w', suffix='.toml', delete=False) as f:
            f.write(text)
        self.addCleanup(Path(f.name).unlink)
        return load_config(f.name)

    def test_example_and_defaults(self):
        cfg = load_config(ROOT / 'config/interface.example.toml')
        self.assertEqual((cfg.volume_repeat_delay_ms, cfg.volume_repeat_interval_ms), (750, 250))
        cfg = self.config('')
        self.assertEqual((cfg.volume_repeat_delay_ms, cfg.volume_repeat_interval_ms), (750, 250))
        cfg = self.config('[volume]\nrepeat_delay_ms = 500\nrepeat_interval_ms = 100\n')
        self.assertEqual((cfg.volume_repeat_delay_ms, cfg.volume_repeat_interval_ms), (500, 100))

    def test_invalid(self):
        for extra in ('[volume]\nrepeat_delay_ms = 10\n', '[volume]\nrepeat_interval_ms = true\n',
                      '[volume]\nrepeat_ms = 100\n'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.config(extra)


class VolumeRepeatTests(unittest.TestCase):
    def test_press_hold_release(self):
        r = VolumeRepeat(750, 250)
        self.assertEqual(r.event('volume_up', 'pressed', 0), 1)
        self.assertEqual([r.tick(t) for t in (100, 749, 750, 900, 1000, 1250)],
                         [0, 0, 1, 0, 1, 1])
        self.assertEqual(r.event('volume_up', 'released', 1300), 0)
        self.assertEqual(r.tick(2000), 0)

    def test_down_and_other_buttons(self):
        r = VolumeRepeat()
        self.assertEqual(r.event('volume_dn', 'pressed', 0), -1)
        self.assertEqual(r.event('stop', 'pressed', 0), 0)
        self.assertEqual(r.tick(750), -1)
        # Releasing a different button does not stop the held one.
        r.event('volume_up', 'released', 800)
        self.assertEqual(r.tick(1000), -1)


class VolumeExecutorTests(unittest.TestCase):
    def test_coalesces_while_busy_and_publishes_level(self):
        gate = threading.Event()
        sent, published = [], []

        def sender(_path, command):
            sent.append(command)
            gate.wait(2)
            return {'ok': True, 'volume': 30 + len(sent)}
        ex = VolumeExecutor('/sock', sender=sender, report=lambda *_: None,
                            publish=lambda *e: published.append(e))
        ex.submit(1)
        time.sleep(0.05)
        for _ in range(3):
            ex.submit(1)
        gate.set()
        deadline = time.monotonic() + 2
        while ex._busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(sent, ['volume +1', 'volume +3'])
        self.assertEqual(published, [('volume', '31'), ('volume', '32')])

    def test_failure_drops_pending_and_never_retries(self):
        gate = threading.Event()
        sent = []

        def sender(_path, command):
            sent.append(command)
            gate.wait(2)
            raise ValueError('player rejected command')
        ex = VolumeExecutor('/sock', sender=sender, report=lambda *_: None)
        ex.submit(-1)
        time.sleep(0.05)
        ex.submit(-1)
        gate.set()
        deadline = time.monotonic() + 2
        while ex._busy and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(sent, ['volume -1'])

    def test_steps_are_clamped(self):
        ex = VolumeExecutor('/sock', sender=lambda *_: {'ok': True}, report=lambda *_: None)
        ex._busy = True  # simulate a running request
        for _ in range(25):
            ex.submit(1)
        self.assertEqual(ex._pending, 10)
        self.assertFalse(ex.submit(0))


class VolumeOverlayTests(unittest.TestCase):
    def test_bridge_accepts_levels_only(self):
        for level in ('0', '42', '100'):
            self.assertEqual(decode_event(encode_event('volume', level)), ('volume', level))
        for bad in ('101', '-1', 'up', '07x'):
            self.assertIsNone(encode_event('volume', bad))

    def test_overlay_is_shown_briefly_and_never_hides_power_menu(self):
        menu = InteractiveMenu(discover=lambda **_: None, power_action=lambda _c: True)
        self.assertTrue(menu.event('volume', '42', now=10.0))
        self.assertTrue(menu.volume_overlay_active(now=10.0 + VOLUME_OVERLAY_SECONDS - 0.1))
        self.assertFalse(menu.volume_overlay_active(now=10.0 + VOLUME_OVERLAY_SECONDS))
        self.assertFalse(menu.active, 'overlay is not a menu')
        frames = []
        menu.event('volume', '42')
        menu.frame(lambda im: frames.append(im) or im)
        self.assertEqual(frames[-1].size, (128, 128))
        menu.event('power', 'short')
        self.assertTrue(menu.power_menu)
        menu.renderer.render_power = lambda *a, **k: 'power-frame'
        menu.event('volume', '43')
        self.assertEqual(menu.frame(lambda im: im), 'power-frame')

    def test_status_frame_shows_overlay_without_open_menu(self):
        from integrations.outputs import oled_status
        menu = InteractiveMenu(discover=lambda **_: None)
        menu.event('volume', '5')
        calls = []
        menu.frame = lambda image_to_frame: calls.append('menu') or 'overlay'
        state = {'schema_version': 1, 'player': {'mode': 'PLAYING'}}
        self.assertEqual(oled_status.selected_status_frame(state, menu=menu), 'overlay')


if __name__ == '__main__':
    unittest.main()
