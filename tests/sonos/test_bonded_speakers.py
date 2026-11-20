"""Stereo pairs and home-theatre sets: hidden bonded speakers are part of the room."""
from types import SimpleNamespace as NS

import pytest

from sonoscdplayer.sonos.groups import is_expected_group, visible_members
from sonoscdplayer.sonos.speaker_checks import SpeakerGrouped, require_solo_and_stopped


def room(uid, *hidden, transport='STOPPED'):
    main = NS(uid=uid, is_visible=True,
              get_current_transport_info=lambda: {'current_transport_state': transport})
    parts = [NS(uid=h, is_visible=False) for h in hidden]
    main.group = NS(coordinator=main, members=[main, *parts])
    return main


def test_stereo_pair_counts_as_one_speaker():
    pair = room('RINCON_L1', 'RINCON_R1')
    assert [p.uid for p in visible_members(pair.group)] == ['RINCON_L1']
    assert require_solo_and_stopped(pair, 'RINCON_L1') is None
    assert is_expected_group(pair, 'RINCON_L1')


def test_soundbar_with_sub_and_surrounds_is_alone():
    bar = room('RINCON_TV', 'RINCON_SUB', 'RINCON_SL', 'RINCON_SR')
    require_solo_and_stopped(bar, 'RINCON_TV')


def test_a_real_second_room_is_still_a_group():
    pair = room('RINCON_L1', 'RINCON_R1')
    kitchen = NS(uid='RINCON_K1', is_visible=True)
    pair.group.members.append(kitchen)
    with pytest.raises(SpeakerGrouped):
        require_solo_and_stopped(pair, 'RINCON_L1')
