"""OLED status service: splash, status hold, player readiness, notices, progress and offline handling."""
import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
from PIL import Image
from integrations.outputs.oled_status import (
    image_to_frame,
    load_splash,
    request_status,
    main,
    StatusHold,
    wait_for_player_ready,
    drive_fault,
    drive_waking,
    drive_notice_frame,
    notice_frame,
    modern_status_frame,
    preserve_display_for_shutdown,
    safe_modern_status_frame,
)
from integrations.outputs.ssd1327 import SSD1327, DisplayConfig, Framebuffer
from integrations.outputs import oled_status
from player_controller import Controller
from player_state import Mode, PlayerState
from sonos_ui.playback_progress import parse_position, verified_progress
from sonos_ui.player_adapter import project_status
from sonos_ui.render import Renderer
URI = 'http://127.0.0.1:8765/track/1.wav'



class OLEDStatusTests(unittest.TestCase):
    def test_image_8k(self):
        im=Image.new('L',(128,128),255)
        self.assertEqual(len(image_to_frame(im).data),8192)
        self.assertEqual(image_to_frame(im).data[0],255)
    def test_transparency_black(self):
        im=Image.new('RGBA',(2,2),(255,255,255,0))
        self.assertEqual(image_to_frame(im).data[0],0)
    def test_centered_small(self):
        im=Image.new('L',(16,8),255)
        frame=image_to_frame(im)
        self.assertEqual(frame.data[0],0)
        self.assertGreater(sum(frame.data),0)
    def test_bad_png_fallback(self):
        self.assertIsNone(load_splash('/tmp/nonexistent-sonoscd-test.png'))
    def test_valid_png(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'splash.png'
            Image.new('RGB',(64,64),(255,255,255)).save(path)
            self.assertIsNotNone(load_splash(path))
    def test_status_modes(self):
        st={'player':{'mode':'PAUSED','track':2,'track_count':15},'sonos':{'transport':'PAUSED_PLAYBACK'},'stream':{'active':True}}
        self.assertNotEqual(safe_modern_status_frame(st).data,modern_status_frame(error='offline').data)
        self.assertEqual(len(safe_modern_status_frame(st).data),8192)
    def test_default_status_safe(self):
        self.assertEqual(len(safe_modern_status_frame({}).data),8192)
    def test_no_write_commands(self):
        class FakeSocket:
            def __init__(self): self.sent=[]
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def settimeout(self,*a):pass
            def connect(self,*a):pass
            def sendall(self,b):self.sent.append(b)
            def recv(self,n):return b'{"ok":true,"state":{"player":{"mode":"STOPPED"}}}\n'
        fake=FakeSocket()
        with patch('integrations.outputs.oled_status.socket.socket',return_value=fake):
            st=request_status()
        self.assertEqual(st['player']['mode'],'STOPPED')
        self.assertEqual(fake.sent,[b'{"command":"status"}\n'])
    def test_dry_run(self):
        with patch('integrations.outputs.oled_status.request_status',return_value={'player':{'mode':'STOPPED'}}):
            main(['--seconds','0.01','--refresh-ms','200','--splash-seconds','0'])
    def test_requires_gpio_execute_pair(self):
        with self.assertRaises(SystemExit):main(['--execute'])


class TestBusyStatusHold(unittest.TestCase):
    def test_busy_grace_keeps_last_known_state(self):
        h = StatusHold(4)
        state = {'player': {'mode': 'STOPPED'}}
        h.success(state, 100)
        self.assertIs(h.failure(115, 50), state)
        self.assertIsNone(h.failure(151, 50))
    def test_normal_disconnection_remains_short(self):
        h = StatusHold(4)
        h.success({'player': {'mode': 'STOPPED'}}, 100)
        self.assertIsNone(h.failure(105))
    def test_never_claim_prior_status_without_snapshot(self):
        self.assertIsNone(StatusHold(4).failure(101, 50))


class TestSplashReadiness(unittest.TestCase):
    def test_immediate_valid_state(self):
        calls=[]
        def status(path):
            calls.append(path)
            return {'player':{'mode':'STOPPED'}}
        self.assertEqual(wait_for_player_ready(status,'/test'), {'player':{'mode':'STOPPED'}})
        self.assertEqual(calls,['/test'])

    def test_retries_until_service_ready(self):
        calls=[0]
        def status(path):
            calls[0]+=1
            if calls[0] < 3: raise ConnectionRefusedError()
            return {'player':{'mode':'STARTING'}}
        clock=[0.0]
        def sleep(t): clock[0]+=t
        self.assertEqual(wait_for_player_ready(status,'/test',clock=lambda:clock[0],sleep=sleep)['player']['mode'],'STARTING')
        self.assertEqual(calls[0],3)

    def test_offline_timeout(self):
        clock=[0.0]
        def sleep(t): clock[0]+=t
        def offline(path): raise FileNotFoundError()
        self.assertIsNone(wait_for_player_ready(offline,'/test',max_wait_seconds=1.0,clock=lambda:clock[0],sleep=sleep))
        self.assertEqual(clock[0],1.0)

    def test_invalid_snapshots_do_not_signal_ready(self):
        calls=[0]
        def status(path):
            calls[0]+=1
            return {'player': {'mode': 10}} if calls[0]==1 else {'player':{'mode':'ERROR'}}
        self.assertEqual(wait_for_player_ready(status,'/test',sleep=lambda _:None)['player']['mode'],'ERROR')


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BootSplashTests(unittest.TestCase):
    def test_frame_and_ram_command(self):
        builder=load('splash_builder',Path('tools/build_ssd1327_splash.py'))
        image=Image.new('L',(128,128),0)
        image.putpixel((0,0),255)
        frame=builder.to_frame(image)
        self.assertEqual(len(frame),8192)
        self.assertEqual(frame[0],0xf0)
        source=builder.build(frame)
        self.assertEqual(source.count('oled 0x5c\n'),1)
        self.assertEqual(source.count('oled [DATA_ONLY]'),64)
        # Every initialization byte is its own command line (D/C low), so a
        # command and its argument are consecutive lines, not one line.
        self.assertIn('oled 0xa0\noled 0x51\n',source)
    def test_led_duty(self):
        led=load('early_led',Path('tools/early_power_led.py'))
        self.assertEqual(led.PIN,12)
        self.assertAlmostEqual(led.duty_at(0),7)
        self.assertAlmostEqual(led.duty_at(1),70)
        with self.assertRaises(ValueError): led.duty_at(-1)
    def test_adopt_only_wakes_display_without_reset_or_init(self):
        class GPIO:
            BCM=11; OUT=1; HIGH=1; LOW=0
            def __init__(self): self.events=[]
            def setmode(self,*a): self.events.append(('mode',a))
            def setup(self,*a,**kw): self.events.append(('setup',a,kw))
            def output(self,*a): self.events.append(('output',a))
            def cleanup(self,*a): pass
        class SPI:
            def __init__(self): self.messages=[]
            def writebytes(self,data): self.messages.append(data)
            def close(self): pass
        gpio=GPIO(); spi=SPI()
        driver=SSD1327(DisplayConfig(),gpio=gpio,spi=spi)
        driver.open(adopt_existing=True)
        self.assertTrue(driver.opened)
        # A previous process may have sent AE (display off) in close(); adoption
        # therefore sends exactly one AF (display on) and nothing else: no
        # re-initialization, no RAM write, and no RESET pulse.
        self.assertEqual(spi.messages,[[0xaf]])
        config=DisplayConfig()
        outputs=[x[1] for x in gpio.events if x[0]=='output']
        self.assertEqual(outputs,[(config.dc_pin,gpio.LOW)])
        self.assertFalse(any(pin==config.rst_pin for pin,_ in outputs))
        # Avoid close() turning display off in this test.

    def test_firmware_frame_payload_is_exact(self):
        builder=load('splash_builder_payload',Path('tools/build_ssd1327_splash.py'))
        payload=bytes(range(256))*32
        source=builder.build(payload)
        data=[]
        for line in source.splitlines():
            if line.startswith('oled [DATA_ONLY] '):
                data.extend(int(token,16) for token in line.split()[2:])
        self.assertEqual(bytes(data),payload)
        self.assertEqual(len(data),8192)
        self.assertLess(source.index('oled 0x5c\n'),source.index('oled [DATA_ONLY] '))

    def test_normal_display_open_still_resets_and_initializes(self):
        class GPIO:
            BCM=11; OUT=1; HIGH=1; LOW=0
            def __init__(self): self.events=[]
            def setmode(self,*args): pass
            def setup(self,*args,**kwargs): self.events.append(('setup',args,kwargs))
            def output(self,*args): self.events.append(('output',args))
            def cleanup(self,*args): pass
        class SPI:
            def __init__(self): self.messages=[]
            def writebytes(self,data): self.messages.append(data)
            def close(self): pass
        gpio=GPIO(); spi=SPI()
        driver=SSD1327(DisplayConfig(),gpio=gpio,spi=spi,sleep=lambda _:None)
        driver.open()
        self.assertTrue(driver.opened)
        self.assertIn(('output',(24,0)),gpio.events)
        self.assertIn(('output',(24,1)),gpio.events)
        self.assertIn([0xfd,0x12],spi.messages)
        self.assertIn([0xaf],spi.messages)
        driver.close()


class FakeMenu:
    def __init__(self, *, active, **_kwargs):
        self.language = 'en'
        self.active = active
        self.frame_calls = 0

    def poll(self):
        pass

    def event(self, *_args):
        pass

    def frame(self, _image_to_frame):
        self.frame_calls += 1
        return Framebuffer()


class FakeReceiver:
    def __init__(self, _path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def receive(self):
        return None


class MenuWhileServiceOfflineTests(unittest.TestCase):
    def run_offline(self, *, menu_active):
        menus = []

        def make_menu(**kwargs):
            menus.append(FakeMenu(active=menu_active, **kwargs))
            return menus[-1]

        with tempfile.TemporaryDirectory() as tmp, \
                patch('sonos_ui.interactive_menu.InteractiveMenu', make_menu), \
                patch('sonos_ui.ui_event_bridge.EventReceiver', FakeReceiver), \
                redirect_stdout(io.StringIO()) as out:
            oled_status.main([
                '--ui-modern',
                '--ui-events-socket', str(Path(tmp) / 'events.sock'),
                '--socket', str(Path(tmp) / 'missing-player.sock'),
                '--seconds', '0.02', '--splash-seconds', '0', '--refresh-ms', '200'])
        return menus[0], out.getvalue()

    def test_open_menu_is_drawn_when_service_is_offline(self):
        menu, output = self.run_offline(menu_active=True)
        self.assertGreaterEqual(menu.frame_calls, 1)
        self.assertIn('status_unavailable', output)

    def test_error_screen_is_used_when_menu_is_closed(self):
        menu, output = self.run_offline(menu_active=False)
        self.assertEqual(menu.frame_calls, 0)
        self.assertIn('status_unavailable', output)


class MenuResponsivenessTests(unittest.TestCase):
    """Menu input shows at once, even while the player answers slowly."""

    def test_event_is_drawn_before_the_slow_status_request(self):
        import time as _time
        order = []

        class EventMenu(FakeMenu):
            def __init__(self, **kwargs):
                super().__init__(active=True, **kwargs)
                self.sent = False
            def event(self, *_args):
                return True
            def frame(self, _image_to_frame):
                order.append('menu_frame')
                return super().frame(_image_to_frame)

        class OneEvent(FakeReceiver):
            def __init__(self, _path):
                self.events = [('encoder', 'right')]
            def receive(self):
                return self.events.pop() if self.events else None

        def slow_status(*_args, **_kwargs):
            order.append('status_request')
            _time.sleep(0.05)
            return {'schema_version': 1, 'player': {'mode': 'STOPPED'}}

        with tempfile.TemporaryDirectory() as tmp, \
                patch('sonos_ui.interactive_menu.InteractiveMenu', EventMenu), \
                patch('sonos_ui.ui_event_bridge.EventReceiver', OneEvent), \
                patch.object(oled_status, 'request_status', slow_status), \
                redirect_stdout(io.StringIO()):
            oled_status.main([
                '--ui-modern', '--ui-events-socket', str(Path(tmp) / 'events.sock'),
                '--socket', str(Path(tmp) / 'player.sock'),
                '--seconds', '0.3', '--splash-seconds', '0', '--refresh-ms', '500'])
        self.assertEqual(order[0], 'menu_frame')
        # While the menu is open the player is asked at most every --refresh-ms.
        self.assertLessEqual(order.count('status_request'), 2)

    def test_speaker_menu_says_it_is_searching(self):
        import threading
        from sonos_ui.interactive_menu import InteractiveMenu
        release = threading.Event()
        def slow_discover(**_kwargs):
            release.wait(2)
            from sonos_ui.multi_target import Topology
            return Topology(())
        menu = InteractiveMenu(discover=slow_discover, language='de')
        menu._open_targets(0.0)
        try:
            self.assertTrue(menu.searching())
            searching = bytes(menu.frame(oled_status.image_to_frame).data)
        finally:
            release.set()
            menu._worker.join(2)
        menu.poll(now=1.0)
        self.assertFalse(menu.searching())
        done = bytes(menu.frame(oled_status.image_to_frame).data)
        self.assertNotEqual(searching, done)
        from sonos_ui.multi_render import MultiRenderer
        renderer = MultiRenderer(language='de')
        self.assertEqual(
            bytes(oled_status.image_to_frame(renderer.render_multi(menu.state, gray=True, searching=True)).data),
            searching)


class OledNoticeTests(unittest.TestCase):

    def test_wait_notice_uses_modern_localized_renderer(self):
        state = {'player': {'mode': 'STOPPED'}, 'drive_power': {'idle_waking': True}}
        self.assertTrue(drive_waking(state))
        for language in ('de', 'en'):
            expected = bytes(notice_frame('status.drive_waking', language=language).data)
            self.assertEqual(bytes(drive_notice_frame(state, language=language).data), expected)
            self.assertEqual(bytes(modern_status_frame(state, language=language).data), expected)
        self.assertNotEqual(bytes(modern_status_frame(state, language='de').data),
                            bytes(modern_status_frame(state, language='en').data))

    def test_latched_fault_has_priority(self):
        state = {'player': {'mode': 'STOPPED'},
                 'drive_power': {'idle_waking': True,
                                 'idle_wake_fault_latched': True}}
        expected = bytes(notice_frame('status.drive_wake_fault', severity='error').data)
        self.assertTrue(drive_fault(state))
        self.assertEqual(bytes(drive_notice_frame(state).data), expected)
        self.assertEqual(bytes(modern_status_frame(state).data), expected)

    def test_stale_or_missing_flags_do_not_trigger(self):
        for state in (
            None,
            {},
            {'drive_power': {}},
            {'drive_power': {'idle_waking': False}},
            {'drive_power': {'idle_wake_fault_latched': False}},
            {'drive_power': {'idle_waking': 'true'}},
        ):
            self.assertFalse(drive_waking(state))
            self.assertFalse(drive_fault(state))

    def test_existing_shutdown_and_fallback_apis_remain(self):
        self.assertTrue(callable(preserve_display_for_shutdown))
        self.assertTrue(callable(safe_modern_status_frame))


class OledNoticePriorityTests(unittest.TestCase):

    def test_power_menu_precedes_latched_fault_and_wake(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame
        sentinel = object()
        menu = Mock()
        menu.power_menu = True
        menu.active = True
        menu.frame.return_value = sentinel
        state = {'drive_power': {'idle_waking': True,
                                 'idle_wake_fault_latched': True}}
        self.assertIs(selected_status_frame(state, menu=menu, ui_modern=True),
                      sentinel)
        menu.frame.assert_called_once()

    def test_wake_notice_disappears_when_player_state_recovers(self):
        from integrations.outputs.oled_status import selected_status_frame
        waking = {'player': {'mode': 'STOPPED'},
                  'drive_power': {'idle_waking': True}}
        ready = {'schema_version': 1,
                 'player': {'mode': 'STOPPED', 'track': None, 'track_count': 0},
                 'drive_power': {'idle_waking': False}}
        wake_frame = selected_status_frame(waking, ui_modern=True)
        ready_frame = selected_status_frame(ready, ui_modern=True)
        self.assertNotEqual(bytes(wake_frame.data), bytes(ready_frame.data))
        self.assertEqual(len(ready_frame.data), 8192)


    def test_fault_precedes_waking_and_active_menu(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        menu = Mock()
        menu.active = True

        state = {
            'drive_power': {
                'idle_waking': True,
                'idle_wake_fault_latched': True,
            }
        }
        expected = bytes(drive_notice_frame(state).data)

        for modern in (False, True):
            for cached in (False, True):
                frame = selected_status_frame(
                    state, menu=menu,
                    ui_modern=modern, cached=cached)
                self.assertEqual(bytes(frame.data), expected)

        menu.frame.assert_not_called()

    def test_wait_precedes_active_menu(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        menu = Mock()
        menu.active = True
        state = {'drive_power': {'idle_waking': True}}
        expected = bytes(drive_notice_frame(state).data)

        for modern in (False, True):
            for cached in (False, True):
                frame = selected_status_frame(
                    state, menu=menu,
                    ui_modern=modern, cached=cached)
                self.assertEqual(bytes(frame.data), expected)

        menu.frame.assert_not_called()

    def test_normal_menu_remains_available(self):
        from unittest.mock import Mock
        from integrations.outputs.oled_status import selected_status_frame

        sentinel = object()
        menu = Mock()
        menu.active = True
        menu.frame.return_value = sentinel

        result = selected_status_frame(
            {'drive_power': {}}, menu=menu, ui_modern=True)

        self.assertIs(result, sentinel)
        menu.frame.assert_called_once()

    def test_cached_and_live_use_one_safe_modern_pipeline(self):
        from unittest import mock
        from integrations.outputs import oled_status
        state = {'drive_power': {}}
        frame = object()
        with mock.patch.object(
            oled_status, 'safe_modern_status_frame',
            return_value=frame,
        ) as safe:
            for cached in (False, True):
                self.assertIs(
                    oled_status.selected_status_frame(
                        state, ui_modern=True, cached=cached), frame)
            self.assertEqual(safe.call_count, 2)



class FakeServer:
    def __init__(self):
        self.running = True

    def alive(self):
        return self.running

    def close(self):
        self.running = False


class FakePlayer:
    def __init__(self, *, uri=URI, position='0:00:25', duration='0:03:20'):
        self.uid = 'local-test'
        self.group = SimpleNamespace(coordinator=self, members=[self])
        self.uri, self.position, self.duration = uri, position, duration
        self.bad_timing = False
        self.timing_reads = 0

    def get_current_transport_info(self):
        return {'current_transport_state': 'PLAYING'}

    def get_current_track_info(self):
        self.timing_reads += 1
        if self.bad_timing and self.timing_reads > 1:
            raise TimeoutError('timing unavailable')
        return {'uri': self.uri, 'position': self.position, 'duration': self.duration}


class PlaybackProgressTests(unittest.TestCase):
    def test_sonos_clock_validation(self):
        self.assertEqual(parse_position('0:03:20'), 200)
        for bad in (None, 200, '-1:00:00', '0:60:00', '00:00:60', '1:2:03', '', '9999:00:00'):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_position(bad))

    def test_verified_uri_only_and_unknown_duration(self):
        info = {'uri': URI, 'position': '0:00:25', 'duration': '0:03:20'}
        self.assertEqual(verified_progress(info, URI),
                         {'elapsed_seconds': 25, 'duration_seconds': 200})
        self.assertIsNone(verified_progress(info, 'foreign-uri'))
        self.assertIsNone(verified_progress({**info, 'position':'0:04:00'}, URI))
        self.assertEqual(verified_progress({**info, 'duration':'0:00:00'},URI),
                         {'elapsed_seconds': 25, 'duration_seconds': None})
        self.assertEqual(verified_progress({**info, 'duration':'0:00:00'},URI,
                                           duration_fallback=210),
                         {'elapsed_seconds':25,'duration_seconds':210})
        self.assertIsNone(verified_progress({**info,'position':'0:04:00',
                                            'duration':'0:00:00'},URI,
                                           duration_fallback=210))

    def test_controller_only_caches_owned_time_for_display(self):
        player = FakePlayer()
        ctrl = Controller(PlayerState(Mode.PLAYING,(1,),1), 'local-test',
                          '127.0.0.1',8765,FakeServer(),verify=lambda uid:player)
        ctrl.player = player
        ctrl.owned_url = URI
        ctrl.seen_playing = True
        ctrl.refresh()
        self.assertEqual(ctrl.status_snapshot()['progress'],
                         {'elapsed_seconds':25,'duration_seconds':200})
        self.assertEqual(player.timing_reads >= 1, True)
        player.uri = 'http://foreign.invalid/music'
        ctrl.refresh()
        self.assertNotIn('progress',ctrl.status_snapshot())

    def test_cached_toc_duration_does_not_read_disc(self):
        from cd_toc import DiscTOC, Track
        player = FakePlayer(duration='0:00:00')
        ctrl = Controller(PlayerState(Mode.PLAYING,(1,),1), 'local-test',
                          '127.0.0.1',8765,FakeServer(),verify=lambda uid:player)
        ctrl.player = player
        ctrl.owned_url = URI
        ctrl.seen_playing = True
        ctrl.display_toc = DiscTOC((Track(1,0,75*210,2,False),),75*210)
        ctrl.refresh()
        self.assertEqual(ctrl.status_snapshot()['progress'],
                         {'elapsed_seconds':25,'duration_seconds':210})

    def test_timing_failure_does_not_fail_playback(self):
        player = FakePlayer()
        ctrl = Controller(PlayerState(Mode.PLAYING,(1,),1), 'local-test',
                          '127.0.0.1',8765,FakeServer(),verify=lambda uid:player)
        ctrl.player=player
        ctrl.owned_url=URI
        ctrl.seen_playing=True
        player.bad_timing=True
        ctrl.refresh()
        self.assertEqual(ctrl.state.mode,Mode.PLAYING)
        self.assertNotIn('progress',ctrl.status_snapshot())

    def test_display_mode_and_progress_require_ownership(self):
        snap={'schema_version':1,'player':{'mode':'PLAYING','track':1,'track_count':9},
              'sonos':{'owned':True},'progress':{'elapsed_seconds':25,'duration_seconds':200}}
        model=project_status(snap)
        self.assertEqual(model.view,'playback')
        self.assertEqual(model.playback,'playing')
        self.assertEqual((model.elapsed_seconds,model.duration_seconds),(25,200))
        paused=project_status({**snap,'player':{**snap['player'],'mode':'PAUSED'}})
        self.assertEqual(paused.playback,'paused')
        self.assertEqual(paused.elapsed_seconds,25)
        no_owner=project_status({**snap,'sonos':{'owned':False}})
        self.assertIsNone(no_owner.elapsed_seconds)
        stopped=project_status({**snap,'player':{**snap['player'],'mode':'STOPPED'}})
        self.assertEqual(stopped.view,'idle')
        self.assertIsNone(stopped.elapsed_seconds)

    def test_render_progress_and_paused_have_distinct_frames(self):
        base={'schema_version':1,'player':{'mode':'PLAYING','track':1,'track_count':9},
              'sonos':{'owned':True}}
        with_progress={**base,'progress':{'elapsed_seconds':25,'duration_seconds':200}}
        plain=Renderer(language='en').render(project_status(base)).tobytes()
        progressed=Renderer(language='en').render(project_status(with_progress)).tobytes()
        self.assertNotEqual(plain,progressed)
        paused=Renderer(language='de').render(project_status({
            **with_progress,'player':{**base['player'],'mode':'PAUSED'}})).tobytes()
        self.assertNotEqual(paused,progressed)

    def test_start_stop_error_and_no_disc_are_explicit(self):
        snapshots = (
            ('STARTING',1,9,'idle','STARTING'),
            ('STOPPED',1,9,'idle','STOPPED'),
            ('ERROR',1,9,'idle','ERROR'),
            ('NO_DISC',None,0,'idle','NO_DISC'),
        )
        for mode,track,count,view,phase in snapshots:
            with self.subTest(mode=mode):
                snap={'schema_version':1,'player':{'mode':mode,'track':track,
                                                  'track_count':count}}
                model=project_status(snap)
                self.assertEqual(model.view,view)
                self.assertEqual(model.disc_phase,phase)
                frame=Renderer(language='de').render(model)
                self.assertEqual(frame.size,(128,128))

    def test_bad_progress_never_makes_up_times(self):
        state={'schema_version':1,'player':{'mode':'PLAYING','track':1,'track_count':9},
               'sonos':{'owned':True}}
        for progress in ({'elapsed_seconds':-1,'duration_seconds':20},
                         {'elapsed_seconds':30,'duration_seconds':20},
                         {'elapsed_seconds':True,'duration_seconds':20},
                         {'elapsed_seconds':10,'duration_seconds':0}):
            with self.subTest(progress=progress):
                model=project_status({**state,'progress':progress})
                self.assertIsNone(model.elapsed_seconds)


if __name__ == '__main__':
    unittest.main()
