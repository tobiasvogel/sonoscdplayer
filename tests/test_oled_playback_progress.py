"""Read-only playback position display; offline without GPIO/CD/Sonos I/O."""
import unittest
from types import SimpleNamespace

from player_controller import Controller
from player_state import Mode, PlayerState
from sonos_ui.playback_progress import parse_position, verified_progress
from sonos_ui.player_adapter import project_status
from sonos_ui.render import Renderer


URI = 'http://127.0.0.1:8765/track/1.wav'


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


if __name__=='__main__':
    unittest.main()
