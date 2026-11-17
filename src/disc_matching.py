"""Conservative, deterministic release matching for local CD-TEXT + MusicBrainz.

Scores indicate text agreement, never a calibrated probability. No I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import unicodedata

from disc_metadata import LocalDiscMetadata
from disc_musicbrainz import LookupResult, ReleaseCandidate


@dataclass(frozen=True)
class MatchingPolicy:
    auto_match: bool = True
    confidence_threshold: int = 90
    minimum_margin: int = 15
    minimum_track_titles: int = 3

    def __post_init__(self):
        for name in ('confidence_threshold', 'minimum_margin'):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError(f'{name} must be an integer between 0 and 100')
        if type(self.minimum_track_titles) is not int or not 1 <= self.minimum_track_titles <= 99:
            raise ValueError('minimum_track_titles must be an integer between 1 and 99')
        if type(self.auto_match) is not bool:
            raise ValueError('auto_match must be boolean')


@dataclass(frozen=True)
class ScoredRelease:
    release: ReleaseCandidate
    score: int
    album_score: int
    artist_score: int
    tracks_score: int
    structure_score: int
    compared_tracks: int


@dataclass(frozen=True)
class MatchDecision:
    status: str  # unique | confidence | manual | disabled | not_found
    selected_release_id: str | None
    score: int | None
    margin: int | None
    candidates: tuple[ScoredRelease, ...]
    reason: str


def _normalize(text: str | None) -> str:
    if not text:
        return ''
    # Only normalize comparison keys. The source data is never rewritten.
    text = text.casefold().replace('ä', 'ae').replace('ö', 'oe').replace('ü', 'ue').replace('ß', 'ss')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    # Common recording/version descriptors do not decide album identity.
    text = re.sub(r'\((?:sing\s+mit|tv\s+version|[^()]{1,45}\s+version)\)', ' ', text)
    text = re.sub(r'[^\w]+', ' ', text, flags=re.UNICODE)
    return ' '.join(text.split())


def _similarity(left: str | None, right: str | None) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def score_release(local: LocalDiscMetadata, release: ReleaseCandidate) -> ScoredRelease:
    """25 artist + 20 album + 45 tracks + 10 exact medium structure."""
    artist = round(25 * _similarity(local.artist, release.artist))
    album = round(20 * _similarity(local.album, release.title))
    local_titles = [(index, t.title) for index, t in enumerate(local.tracks) if t.title]
    matching_structure = len(release.track_titles) == len(local.tracks) and len(local.tracks) > 0
    if matching_structure and local_titles:
        ratio = sum(_similarity(title, release.track_titles[index])
                    for index, title in local_titles) / len(local_titles)
        tracks = round(45 * ratio)
    else:
        tracks = 0
    structure = 10 if matching_structure else 0
    return ScoredRelease(release, artist + album + tracks + structure,
                         album, artist, tracks, structure,
                         len(local_titles) if matching_structure else 0)


def decide_release(local: LocalDiscMetadata, lookup: LookupResult,
                   policy: MatchingPolicy = MatchingPolicy()) -> MatchDecision:
    """Auto-select unique DiscID hits or unambiguous, corroborated high scores.

    For ambiguous DiscIDs, require album, artist and sufficient local titles.
    Never treat score alone as proof; enforce threshold and runner-up margin.
    """
    ranked = tuple(sorted((score_release(local, release) for release in lookup.releases),
                          key=lambda item: (-item.score, item.release.release_id)))
    if lookup.status not in ('unique', 'ambiguous') or not ranked:
        return MatchDecision('not_found', None, None, None, ranked, 'no release available')
    best = ranked[0]
    margin = best.score - ranked[1].score if len(ranked) > 1 else None
    if not policy.auto_match:
        return MatchDecision('disabled', None, best.score, margin, ranked,
                             'automatic matching disabled')
    if lookup.status == 'unique' and len(ranked) == 1:
        return MatchDecision('unique', best.release.release_id, best.score, None,
                             ranked, 'unique MusicBrainz DiscID release')
    if not local.album or not local.artist:
        reason = 'album title and artist required for ambiguous DiscID'
    elif best.compared_tracks < policy.minimum_track_titles:
        reason = 'insufficient comparable CD-TEXT track titles'
    elif best.structure_score != 10:
        reason = 'unverified release track structure'
    elif best.score < policy.confidence_threshold:
        reason = 'score below threshold'
    elif margin is None or margin < policy.minimum_margin:
        reason = 'margin below minimum'
    else:
        return MatchDecision('confidence', best.release.release_id, best.score,
                             margin, ranked, 'high-confidence CD-TEXT agreement')
    return MatchDecision('manual', None, best.score, margin, ranked, reason)


def merged_metadata(local: LocalDiscMetadata, decision: MatchDecision) -> LocalDiscMetadata:
    """Supplement only missing local fields. Do not alter CD-TEXT or ISRC."""
    if not decision.selected_release_id:
        return local
    chosen = next((item.release for item in decision.candidates
                   if item.release.release_id == decision.selected_release_id), None)
    if chosen is None:
        return local
    updated_tracks = local.tracks
    if len(chosen.track_titles) == len(local.tracks):
        updated_tracks = tuple(replace(track, title=track.title or chosen.track_titles[i])
                               for i, track in enumerate(local.tracks))
    return replace(local, album=local.album or chosen.title,
                   artist=local.artist or chosen.artist, tracks=updated_tracks)
