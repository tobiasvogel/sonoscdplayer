#!/usr/bin/env python3
"""META-01B: one-shot read-only DiscID / MusicBrainz diagnostic.

Stop the player before using this tool. Lookup happens after lock release.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from cd_toc import read_toc
from config_paths import player_config
from disc_musicbrainz import identity_from_toc, lookup_disc
from disc_metadata import read_cdtext, build_local_metadata
from disc_matching import MatchingPolicy, decide_release, merged_metadata
from disc_startup import optical_devices
from drive_access import drive_lock


def read_settings(path):
    with path.open('rb') as handle:
        config = tomllib.load(handle)
    metadata = config.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('[metadata] must be a table')
    musicbrainz = metadata.get('musicbrainz', {})
    if not isinstance(musicbrainz, dict):
        raise ValueError('[metadata.musicbrainz] must be a table')
    for section, name in ((metadata, 'enabled'), (metadata, 'online_lookup'),
                          (musicbrainz, 'enabled')):
        if name in section and not isinstance(section[name], bool):
            raise ValueError(f'{name} must be boolean')
    timeout = musicbrainz.get('timeout_seconds', 8)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 30:
        raise ValueError('metadata.musicbrainz.timeout_seconds must be 1..30')
    return (metadata.get('enabled', True),
            metadata.get('online_lookup', True) and musicbrainz.get('enabled', True),
            timeout)



def matching_settings(path):
    """Load matching policy and read-only CD-TEXT switch from player.toml."""
    with path.open('rb') as handle:
        config = tomllib.load(handle)
    metadata = config.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('[metadata] must be a table')
    values = metadata.get('matching', {})
    if not isinstance(values, dict):
        raise ValueError('[metadata.matching] must be a table')
    read_text = metadata.get('read_cd_text', True)
    if type(read_text) is not bool:
        raise ValueError('metadata.read_cd_text must be boolean')
    defaults = MatchingPolicy()
    policy = MatchingPolicy(
        auto_match=metadata.get('auto_match', defaults.auto_match),
        confidence_threshold=values.get('confidence_threshold', defaults.confidence_threshold),
        minimum_margin=values.get('minimum_margin', defaults.minimum_margin),
        minimum_track_titles=values.get('minimum_track_titles', defaults.minimum_track_titles))
    return read_text, policy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--config', type=Path, default=player_config())
    parser.add_argument('--offline', action='store_true', help='DiscID only, without network')
    args = parser.parse_args(argv)
    try:
        enabled, online, timeout = read_settings(args.config)
        read_text, matching_policy = matching_settings(args.config)
        if not enabled:
            print('Metadata disabled by configuration', file=sys.stderr)
            return 2
        if args.device == 'auto':
            candidates = optical_devices()
            if len(candidates) != 1:
                raise RuntimeError('auto requires exactly one optical drive')
            device = candidates[0]
        else:
            device = args.device
        with drive_lock(device, blocking=False) as acquired:
            if not acquired:
                raise RuntimeError('drive busy; stop playback and retry later')
            toc = read_toc(device)
            if not args.offline and online and read_text:
                text_status, disc_fields, track_fields, text_error = read_cdtext(device)
            else:
                text_status, disc_fields, track_fields, text_error = 'disabled', {}, {}, None
        identity = identity_from_toc(toc)  # No further drive access.
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f'MusicBrainz probe failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    local = build_local_metadata(
        toc, cdtext_status=text_status, disc_fields=disc_fields,
        track_fields=track_fields, error=text_error)
    report = {'disc_id': identity.disc_id, 'toc': identity.toc,
              'track_count': len(toc.tracks)}
    if args.offline or not online:
        report['lookup_status'] = 'disabled'
    else:
        result = lookup_disc(identity, track_count=len(toc.tracks),
                             timeout_seconds=timeout)
        decision = decide_release(local, result, matching_policy)
        report.update(
            lookup_status=result.status,
            match_status=decision.status,
            match_reason=decision.reason,
            match_score=decision.score,
            match_margin=decision.margin,
            selected_release_id=decision.selected_release_id,
            releases=[asdict(candidate) for candidate in result.releases],
            ranked_candidates=[
                {'release_id': scored.release.release_id, 'score': scored.score,
                 'album': scored.album_score, 'artist': scored.artist_score,
                 'tracks': scored.tracks_score, 'structure': scored.structure_score,
                 'compared_tracks': scored.compared_tracks}
                for scored in decision.candidates],
            local_metadata=local.to_dict(),
            effective_metadata=merged_metadata(local, decision).to_dict())
        if result.error:
            report['lookup_error'] = result.error
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
