"""WEB-03b: validation and strict projection of release selection endpoints."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import json
import threading
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from sonoscdplayer.web.server import make_handler
from sonoscdplayer.web.state import WebPreferences
from sonoscdplayer.ui.i18n.translator import catalog
from sonoscdplayer.web.player_client import release_payload
from sonoscdplayer.web.dashboard_projection import public_dashboard, public_release_detail

RID = '00000000-0000-4000-8000-000000000001'
TOKEN = 'a'*32

class FakePlayer:
    def __init__(self): self.requests=[]
    def request(self,data,*,timeout=35):
        self.requests.append(data)
        if data['command']=='release-detail':
            return {'ok':True,'release_detail':{'release_id':RID,'title':'Release','artist':'Artist',
                'country':'CH','date':'2026','track_count':1,
                'tracks':[{'number':1,'title':'Track','artist':'Singer','path':'/fixture-private/secret'}]}}
        return {'ok':True,'queued':True}

@contextmanager
def server(language='de'):
    # Error localization requires a real preferences state directory.
    with tempfile.TemporaryDirectory(prefix='sonoscdplayer-web-release-test-') as state_dir:
        (Path(state_dir) / 'ui.json').write_text(
            json.dumps({'version': 1, 'language': language}), encoding='utf-8')
        player=FakePlayer()
        instance=ThreadingHTTPServer(('127.0.0.1',0),make_handler(
            player, WebPreferences(state_dir)))
        thread=threading.Thread(target=instance.serve_forever,daemon=True)
        thread.start()
        try:yield 'http://127.0.0.1:'+str(instance.server_port),player
        finally:instance.shutdown();instance.server_close();thread.join(2)

def post(url,data,headers=None):
    request=Request(url,data=json.dumps(data).encode(),method='POST',
        headers={'Content-Type':'application/json',**(headers or {})})
    try:
        with urlopen(request,timeout=3) as response:return response.status,json.load(response)
    except HTTPError as exc:return exc.code,json.loads(exc.read())


def test_release_search_select_and_details_have_bounded_player_commands():
    with server() as (base, player):
        assert post(base+'/api/releases/search',{'token':TOKEN})[0]==202
        assert post(base+'/api/releases/select',{'token':TOKEN,'release_id':RID,'keep_manual':False})[0]==202
        with urlopen(base+'/api/release-detail?'+urlencode({'token':TOKEN,'release_id':RID})) as response:
            data=json.load(response)
        assert data['tracks']==[{'number':1,'title':'Track','artist':'Singer'}]
        assert '/fixture-private/' not in json.dumps(data)
        assert player.requests==[
            {'command':'lookup-releases','token':TOKEN},
            {'command':'select-release','token':TOKEN,'release_id':RID,'keep_manual':False},
            {'command':'release-detail','token':TOKEN,'release_id':RID},
        ]


def test_bad_release_ids_and_origins_are_rejected_before_player():
    with server() as (base, player):
        for data in ({'token':'a'*31}, {'token':TOKEN,'uid':'RINCON_FAKE'}):
            code, payload = post(base+'/api/releases/search',data)
            assert code == 400
            assert payload['error'] == catalog('de')['web.api.invalid_input']
        for data in ({'token':TOKEN,'release_id':'RINCON_FAKE','keep_manual':True},
                     {'token':TOKEN,'release_id':RID,'keep_manual':'yes'}):
            assert post(base+'/api/releases/select',data)[0]==400
        code, payload = post(base+'/api/releases/select',
            {'token':TOKEN,'release_id':RID,'keep_manual':True},
            {'Origin':'http://untrusted.example'})
        assert code == 403
        assert payload['error'] == catalog('de')['web.api.cross_origin']
        assert player.requests==[]


def test_public_dashboard_drops_candidate_private_fields():
    projection=public_dashboard({'ok':True,'dashboard':{'track_count':0,'release_candidates':[
        {'release_id':RID,'title':'Example','artist':'Artist','path':'/fixture-private/secret',
         'score':80,'track_count':2}], 'release_status':'ready','release_total':1}})
    assert projection['release_candidates']==[{'release_id':RID,'title':'Example',
        'artist':'Artist','country':None,'date':None,'score':80,'track_count':2}]
    assert '/fixture-private/' not in json.dumps(projection)
    assert public_release_detail({'release_id':RID,'tracks':[], 'path':'/fixture-private/secret'})['tracks']==[]
