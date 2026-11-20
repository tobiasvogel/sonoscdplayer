"""The player's reason for refusing a speaker selection reaches the display."""
import pytest

from sonoscdplayer.ui.menu.speakers.target_rpc_client import loggable, rejection_notice


@pytest.mark.parametrize('reason, notice', [
    ('speaker is not alone in its group', 'status.target_grouped'),
    ('member not standalone', 'status.target_grouped'),
    ('speaker is not stopped (PLAYING)', 'status.target_busy'),
    ('member has active transport', 'status.target_busy'),
    ('speaker RINCON_X1 not found exactly once (0 matches)', 'status.target_not_found'),
    ('incomplete member discovery', 'status.target_not_found'),
    ('target selection requires stopped, unowned player', 'status.target_rejected'),
])
def test_reason_becomes_a_specific_notice(reason, notice):
    assert rejection_notice(RuntimeError(f'player rejected target: {reason}')) == notice


def test_log_line_has_no_speaker_uid():
    assert loggable('speaker RINCON_ABC123 not found') == 'speaker <speaker> not found'
