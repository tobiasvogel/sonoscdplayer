"""Local CD-TEXT metadata regressions; no hardware, Sonos or internet."""
import subprocess
import unittest
from unittest.mock import Mock

from cd_toc import DiscTOC, Track
from disc_metadata import build_local_metadata, parse_cd_info_cdtext, read_cdtext


class CDTextTests(unittest.TestCase):
    def setUp(self):
        self.toc = DiscTOC((Track(1, 0, 75, 2, False),
                            Track(2, 75, 225, 2, False)), 225)

    def test_disc_and_track_fields(self):
        source = '''CD Analysis Report
CD-TEXT for Disc:
  TITLE: Example Album
  PERFORMER: Example Artist
CD-TEXT for Track  1:
  TITLE: First Song
  PERFORMER: Guest Artist
CD-TEXT for Track  2:
  TITLE: Second Song
CDDB TITLE: Not from disc
'''
        disc, tracks = parse_cd_info_cdtext(source)
        self.assertEqual(disc, {'TITLE': 'Example Album', 'PERFORMER': 'Example Artist'})
        self.assertEqual(tracks[1]['TITLE'], 'First Song')
        self.assertEqual(tracks[1]['PERFORMER'], 'Guest Artist')
        model = build_local_metadata(self.toc, cdtext_status='available',
                                     disc_fields=disc, track_fields=tracks)
        self.assertEqual([t.duration_seconds for t in model.tracks], [1.0, 2.0])
        self.assertEqual(model.tracks[0].title, 'First Song')
        self.assertEqual(model.album, 'Example Album')

    def test_missing_cdtext_is_not_read_failure(self):
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, 'CD Analysis Report', ''))
        status, disc, tracks, error = read_cdtext('/dev/sr0', runner=runner)
        self.assertEqual((status, disc, tracks, error), ('absent', {}, {}, None))
        self.assertEqual(runner.call_args.args[0][0], 'cd-info')

    def test_missing_binary_is_distinct(self):
        runner = Mock(side_effect=FileNotFoundError())
        self.assertEqual(read_cdtext('/dev/sr0', runner=runner)[0], 'unavailable')

    def test_timeout_and_nonzero_are_not_absent(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired(['cd-info'], 15))
        self.assertEqual(read_cdtext('/dev/sr0', runner=runner)[0], 'failed')
        runner.side_effect = None
        runner.return_value = subprocess.CompletedProcess([], 2, '', 'private error')
        report = read_cdtext('/dev/sr0', runner=runner)
        self.assertEqual(report[0], 'failed')
        self.assertNotIn('private error', str(report))

    def test_only_valid_toc_track_numbers(self):
        model = build_local_metadata(self.toc, cdtext_status='available',
                                     track_fields={1: {'TITLE': 'One'},
                                                   99: {'TITLE': 'Foreign'}})
        self.assertEqual(len(model.tracks), 2)
        self.assertIsNone(model.tracks[1].title)

    def test_no_other_sections_masquerade_as_cdtext(self):
        disc, tracks = parse_cd_info_cdtext('CDDB:\n TITLE: Online\n')
        self.assertFalse(disc)
        self.assertFalse(tracks)


if __name__ == '__main__':
    unittest.main()
