"""Synthetic matching fixtures: no personal installation or real disc metadata."""
import unittest

from disc_metadata import LocalDiscMetadata, LocalTrackMetadata
from disc_musicbrainz import ReleaseCandidate, LookupResult
from disc_matching import MatchingPolicy, decide_release, merged_metadata


def make_local(titles, album='Fruehlingslieder (Sing mit)', artist='Kinderchor'):
    return LocalDiscMetadata(album, artist, tuple(
        LocalTrackMetadata(i + 1, 180, title) for i, title in enumerate(titles)), 'available')


def candidate(id, album, artist, titles):
    return ReleaseCandidate(id, album, artist, None, None, tuple(titles))


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.local = make_local(('Lied vom Fruehling (Sing mit)', 'Schoene Blumen',
                                 'Alles ist gruen', 'Das Schlusslied'))
        self.correct = candidate('correct', 'Frühlingslieder', 'Kinderchor',
                                 ('Lied vom Frühling (TV Version)', 'Schöne Blumen',
                                  'Alles ist grün', 'Das Schlusslied'))
        self.wrong = candidate('wrong', 'Military Marches', 'Brass Band',
                               ('March One', 'March Two', 'March Three', 'March Four'))
        self.other = candidate('other', 'Chansons de printemps', 'Les Enfants',
                               ('Le printemps', 'Fleurs', 'Tout est vert', 'Finale'))

    def decide(self, *releases, local=None, policy=None):
        return decide_release(local or self.local,
                              LookupResult('unique' if len(releases) == 1 else 'ambiguous',
                                           tuple(releases)), policy or MatchingPolicy())

    def test_high_confidence_autoselect(self):
        decision = self.decide(self.wrong, self.other, self.correct)
        self.assertEqual(decision.status, 'confidence')
        self.assertEqual(decision.selected_release_id, 'correct')
        self.assertGreaterEqual(decision.score, 90)
        self.assertGreaterEqual(decision.margin, 15)

    def test_preserves_cdtext_when_merge(self):
        decision = self.decide(self.other, self.correct)
        enriched = merged_metadata(self.local, decision)
        self.assertEqual(enriched.album, self.local.album)
        self.assertEqual(enriched.tracks[0].title, self.local.tracks[0].title)

    def test_adds_only_missing_track_titles(self):
        local = make_local(('Lied vom Fruehling', None, 'Alles ist gruen', 'Das Schlusslied'))
        decision = self.decide(self.wrong, self.correct, local=local)
        enriched = merged_metadata(local, decision)
        self.assertEqual(enriched.tracks[1].title, 'Schöne Blumen')
        self.assertEqual(enriched.tracks[0].title, 'Lied vom Fruehling')

    def test_near_tie_stays_manual(self):
        similar = candidate('alternative', 'Frühlingslieder', 'Kinderchor',
                            self.correct.track_titles)
        decision = self.decide(self.correct, similar)
        self.assertEqual(decision.status, 'manual')
        self.assertEqual(decision.reason, 'margin below minimum')

    def test_no_cdtext_stays_manual(self):
        local = make_local((None, None, None, None), album=None, artist=None)
        decision = self.decide(self.correct, self.wrong, local=local)
        self.assertEqual(decision.status, 'manual')

    def test_fewer_than_three_titles_stays_manual(self):
        local = make_local(('Lied vom Fruehling', None, None, None))
        decision = self.decide(self.correct, self.wrong, local=local)
        self.assertEqual(decision.status, 'manual')

    def test_unique_disc_id_auto_selected(self):
        local = make_local((None, None, None, None), album=None, artist=None)
        decision = self.decide(self.correct, local=local)
        self.assertEqual(decision.status, 'unique')

    def test_auto_match_disabled(self):
        decision = self.decide(self.correct, self.wrong, policy=MatchingPolicy(auto_match=False))
        self.assertEqual(decision.status, 'disabled')

    def test_strict_threshold_does_not_select(self):
        decision = self.decide(self.correct, self.wrong,
                               policy=MatchingPolicy(confidence_threshold=100))
        self.assertLessEqual(decision.score, 100)

    def test_inconsistent_track_structure(self):
        wrong_structure = candidate('bad', 'Frühlingslieder', 'Kinderchor', ('Short',))
        decision = self.decide(wrong_structure, self.wrong)
        self.assertEqual(decision.status, 'manual')

    def test_none_found(self):
        result = decide_release(self.local, LookupResult('not_found'))
        self.assertEqual(result.status, 'not_found')

    def test_no_mutation_of_input(self):
        before = self.local
        self.decide(self.correct, self.wrong)
        self.assertEqual(self.local, before)

    def test_reject_invalid_policy(self):
        for policy in ({'confidence_threshold': 101}, {'minimum_margin': -1},
                       {'minimum_track_titles': 0}, {'auto_match': 1}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                MatchingPolicy(**policy)


if __name__ == '__main__':
    unittest.main()
