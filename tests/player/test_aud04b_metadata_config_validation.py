"""AUD-04b: malformed metadata switches must not silently alter startup."""
import pytest

from sonoscdplayer.player.service import _metadata_switches


@pytest.mark.parametrize('key', ('enabled', 'online_lookup', 'read_cd_text'))
@pytest.mark.parametrize('wrong', ('false', 0, 1, None))
def test_rejects_non_boolean_metadata_switch(key, wrong):
    with pytest.raises(ValueError, match=r'metadata\.' + key):
        _metadata_switches({key: wrong})


def test_valid_metadata_switches_and_missing_defaults():
    assert _metadata_switches({}) == {}
    values = {'enabled': False, 'online_lookup': False, 'read_cd_text': True}
    assert _metadata_switches(values) == values


def test_non_table_metadata_is_rejected():
    with pytest.raises(ValueError, match='TOML table'):
        _metadata_switches('false')
