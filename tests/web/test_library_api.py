"""WEB-04a public projection and strict request validation (no raw DB fields)."""
import json

import pytest

from sonoscdplayer.web.library_projection import public_library
from sonoscdplayer.web.player_client import library_payload


TOKEN = 'a' * 32


def test_library_projection_blocks_private_fields_and_bounds_output():
    response = {'ok': True, 'library': {'status':'ready','seq':2,'query':'','offset':0,
        'limit':20,'total':1,'entries':[{'token':TOKEN,'album':'Album',
            'artist':'Artist','track_count':2,'updated_at':4,'manual_cover':True,
            'toc':'hidden','uid':'RINCON_HIDDEN','private_path':'/fixture-private/image.jpg'}],
        'detail':{'token':TOKEN,'album':'Album','artist':'Artist','track_count':2,
            'tracks':[{'number':1,'title':'One','artist':'Singer',
                       'isrc':'PRIVATE','path':'/fixture-private/file.mp3'}],
            'toc':'hidden','manual_fields':['album']},'error':None}}
    result = public_library(response)
    raw = json.dumps(result)
    assert 'RINCON_' not in raw and '/fixture-private/' not in raw
    assert 'toc' not in raw and 'isrc' not in raw
    assert result['library']['detail']['tracks'][1]['title'] is None
    assert result['library']['entries'][0]['album'] == 'Album'


@pytest.mark.parametrize('data,op', [
    ({'query':'','offset':0,'limit':20,'extra':1},'query'),
    ({'query':'a'*81,'offset':0,'limit':20},'query'),
    ({'query':'','offset':0,'limit':999},'query'),
    ({'query':'','offset':True,'limit':20},'query'),
    ({'token':TOKEN,'offset':0},'detail'),
    ({'token':'not-valid'},'detail'),
])
def test_library_requests_fail_closed(data,op):
    with pytest.raises(ValueError):
        library_payload(data,op)


def test_library_request_whitelist():
    assert library_payload({'query':'jazz','offset':0,'limit':20},'query') == {
        'command':'library-query','query':'jazz','offset':0,'limit':20}
    assert library_payload({'token':TOKEN},'detail') == {
        'command':'library-detail','token':TOKEN}
