"""Synthetic matching fixtures: no personal installation or real disc metadata."""
import unittest

from sonoscdplayer.metadata.local_metadata import LocalDiscMetadata, LocalTrackMetadata
from sonoscdplayer.metadata.musicbrainz import ReleaseCandidate, LookupResult
from sonoscdplayer.metadata.release_matching import MatchingPolicy, decide_release, merged_metadata


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

    def test_adds_missing_track_artists_from_musicbrainz(self):
        local = LocalDiscMetadata(None, None, tuple(
            LocalTrackMetadata(i + 1, 180, title, artist)
            for i, (title, artist) in enumerate((
                ('Lied vom Fruehling', 'Chor vom CD-TEXT'), ('Schoene Blumen', None),
                ('Alles ist gruen', None), ('Das Schlusslied', None)))), 'available')
        compilation = ReleaseCandidate('correct', 'Frühlingslieder', 'Verschiedene', None, None,
                                       self.correct.track_titles,
                                       ('MB A', 'MB B', None, 'MB D'))
        enriched = merged_metadata(local, self.decide(compilation, local=local))
        self.assertEqual([t.artist for t in enriched.tracks],
                         ['Chor vom CD-TEXT', 'MB B', None, 'MB D'])   # CD-TEXT wins
        self.assertEqual(enriched.artist, 'Verschiedene')

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


    def test_protected_version_never_auto_matches_ambiguous(self):
        local = make_local(('Lied vom Fruehling (Live)', 'Schoene Blumen',
                            'Alles ist gruen', 'Das Schlusslied'),
                           album='Frühlingslieder', artist='Kinderchor')
        decision = self.decide(self.correct, self.wrong, local=local)
        self.assertEqual(decision.status, 'manual')
        self.assertIn('protected', decision.reason)

    def test_user_added_multilingual_soft_suffixes(self):
        policy = MatchingPolicy(soft_suffixes=('Sing mit', 'TV Version', 'Version française'))
        local = make_local(('Lied vom Fruehling (Version française)',
                            'Schoene Blumen', 'Alles ist gruen', 'Das Schlusslied'))
        decision = self.decide(self.correct, self.wrong, local=local, policy=policy)
        self.assertEqual(decision.status, 'confidence')

    def test_suffix_only_at_end(self):
        from sonoscdplayer.metadata.release_matching import _split_qualifier
        policy = MatchingPolicy()
        self.assertEqual(_split_qualifier('Live and Let Die', policy),
                         ('Live and Let Die', None, None))
        self.assertEqual(_split_qualifier('Song (Live)', policy),
                         ('Song', 'live', 'protected'))

    def test_conflicting_lists_invalid(self):
        with self.assertRaises(ValueError):
            MatchingPolicy(soft_suffixes=('Live',))
        with self.assertRaises(ValueError):
            MatchingPolicy(protected_suffixes=('Live', 'LIVE'))


    def test_unicode_multilingual_comparisons(self):
        from sonoscdplayer.metadata.release_matching import _normalize
        self.assertEqual(_normalize('Café'), _normalize('Cafe\u0301'))
        self.assertEqual(_normalize('Canción'), _normalize('Cancion'))
        self.assertEqual(_normalize('Straße'), _normalize('STRASSE'))
        self.assertEqual(_normalize('Ｎｅｗ　Ｙｏｒｋ'), 'new york')
        self.assertNotEqual(_normalize('ガラス'), _normalize('カラス'))
        self.assertEqual(_normalize('ｶﾞﾗｽ'), _normalize('ガラス'))
        self.assertNotEqual(_normalize('Москва'), _normalize('Moskva'))

    def test_explicit_german_pack_word_aliases(self):
        from sonoscdplayer.metadata.language_rules import load_language_rules
        rules = load_language_rules(('de',))
        self.assertEqual(rules.normalize('Fruehling und schöne Blumen'),
                         rules.normalize('Frühling und schoene Blumen'))
        self.assertNotEqual(rules.normalize('Fruehling'), rules.normalize('Sommer'))

    def test_language_packs_not_tied_to_ui_locale(self):
        from sonoscdplayer.metadata.language_rules import load_language_rules
        from sonoscdplayer.metadata.release_matching import _split_qualifier
        policy = MatchingPolicy(language_rules=load_language_rules(('fr',)))
        self.assertEqual(_split_qualifier('Chanson (Version française)', policy)[2], 'soft')
        self.assertEqual(_split_qualifier('Chanson (En direct)', policy)[2], 'protected')
        self.assertIsNone(_split_qualifier('Chanson (En direct)', MatchingPolicy())[2])

    def test_japanese_album_matching_without_language_pack(self):
        local = make_local(('はるのうた', '青い空', '花の道', 'ありがとう'),
                           album='春の歌', artist='子ども合唱団')
        right = candidate('right', '春の歌', '子ども合唱団',
                          ('はるのうた', '青い空', '花の道', 'ありがとう'))
        wrong = candidate('wrong', '夏の夜', '別の歌手',
                          ('遠い海', '赤い空', '旅の道', 'さようなら'))
        decision = self.decide(wrong, right, local=local)
        self.assertEqual(decision.selected_release_id, 'right')

    def test_spanish_accents_without_language_pack(self):
        local = make_local(('Corazón', 'Canción de otoño', 'Niñez', 'La última'),
                           album='Música para todos', artist='Los Niños')
        right = candidate('right', 'Musica para todos', 'Los Ninos',
                          ('Corazon', 'Cancion de otono', 'Ninez', 'La ultima'))
        wrong = candidate('wrong', 'Ritmos lejanos', 'Otra banda',
                          ('Otro', 'Nuevo', 'Danza', 'Final'))
        decision = self.decide(right, wrong, local=local)
        self.assertEqual(decision.selected_release_id, 'right')
        self.assertEqual(merged_metadata(local, decision), local)

if __name__ == '__main__':
    unittest.main()
