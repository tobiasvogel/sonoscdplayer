"""CDs whose first track does not start at LSN 0 (seen on a real disc)."""
import pytest

from sonoscdplayer.audio.table_of_contents import TOCError, parse_cdparanoia_query

OFFSET_DISC = """\
Table of contents (audio tracks only):
track        length               begin        copy pre ch
===========================================================
  1.    17835 [03:57.60]       33 [00:00.33]    no   no  2
  2.    16125 [03:35.00]    17868 [03:58.18]    no   no  2
  3.    31860 [07:04.60]    33993 [07:33.18]    no   no  2
  4.    21530 [04:47.05]    65853 [14:38.03]    no   no  2
  5.    28685 [06:22.35]    87383 [19:25.08]    no   no  2
  6.    35270 [07:50.20]   116068 [25:47.43]    no   no  2
  7.    15425 [03:25.50]   151338 [33:37.63]    no   no  2
  8.    17287 [03:50.37]   166763 [37:03.38]    no   no  2
  9.     9093 [02:01.18]   184050 [40:54.00]    no   no  2
TOTAL  193110 [42:54.60]    (audio only)
"""


def test_first_track_after_lsn_zero_is_accepted():
    toc = parse_cdparanoia_query(OFFSET_DISC)
    assert len(toc.tracks) == 9
    assert toc.tracks[0].start_lsn == 33
    assert toc.leadout_lsn == 193143          # end of track 9, not cdparanoia's TOTAL


def test_inconsistent_total_is_still_refused():
    with pytest.raises(TOCError, match='Lead-out mismatch'):
        parse_cdparanoia_query(OFFSET_DISC.replace('TOTAL  193110', 'TOTAL  190000'))
