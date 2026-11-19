#!/usr/bin/env python3
"""One-shot read-only DiscID / MusicBrainz diagnostic.

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

from sonoscdplayer.audio.table_of_contents import read_toc
from sonoscdplayer.system.config_paths import player_config, ui_config
from sonoscdplayer.metadata.musicbrainz import identity_from_toc, lookup_disc
from sonoscdplayer.metadata.local_metadata import read_cdtext, build_local_metadata
from sonoscdplayer.metadata.release_matching import MatchingPolicy, decide_release, merged_metadata
from sonoscdplayer.metadata.language_rules import load_language_rules
from sonoscdplayer.metadata.cache import DiscCache
from sonoscdplayer.metadata.cover_store import CoverStore
from sonoscdplayer.metadata.cover_preferences import read_cover_preferences
from sonoscdplayer.drive.disc_startup import optical_devices
from sonoscdplayer.drive.drive_lock import drive_lock


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
    def suffix_list(name, fallback):
        supplied = values.get(name, list(fallback))
        if type(supplied) is not list or any(type(item) is not str for item in supplied):
            raise ValueError(f'metadata.matching.{name} must be a list of strings')
        return tuple(supplied)
    languages = values.get('rule_languages', [])
    if type(languages) is not list or any(type(x) is not str for x in languages):
        raise ValueError('metadata.matching.rule_languages must be a list of tags')
    language_rules = load_language_rules(tuple(languages))
    policy = MatchingPolicy(
        auto_match=metadata.get('auto_match', defaults.auto_match),
        confidence_threshold=values.get('confidence_threshold', defaults.confidence_threshold),
        minimum_margin=values.get('minimum_margin', defaults.minimum_margin),
        minimum_track_titles=values.get('minimum_track_titles', defaults.minimum_track_titles),
        soft_suffixes=suffix_list('soft_suffixes', defaults.soft_suffixes),
        protected_suffixes=suffix_list('protected_suffixes', defaults.protected_suffixes),
        language_rules=language_rules)
    return read_text, policy



def probe_cache(identity, cache_path):
    if cache_path is None:
        return None
    return DiscCache(cache_path).get(identity)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--config', type=Path, default=player_config())
    parser.add_argument('--ui-config', type=Path, default=ui_config(), help='Cover preferences from ui.toml')
    parser.add_argument('--offline', action='store_true', help='DiscID only, without network')
    parser.add_argument('--cache-db', type=Path, help='Opt-in local SQLite metadata cache')
    parser.add_argument('--cover-dir', type=Path, help='Explicit directory for cached cover JPEGs')
    parser.add_argument('--fetch-cover', action='store_true', help='Fetch a single cover when configured')

    args = parser.parse_args(argv)
    try:
        enabled, online, timeout = read_settings(args.config)
        read_text, matching_policy = matching_settings(args.config)
        cover_preferences = read_cover_preferences(args.ui_config)
        cover_policy = cover_preferences.download_policy(
            display_supports_cover=bool(args.fetch_cover and args.cover_dir))
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
    try:
        cached = probe_cache(identity, args.cache_db)
    except (OSError, ValueError) as exc:
        print(f'Cache read failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    if cached is not None and not args.offline:
        # Current CD-TEXT remains authoritative, even when a cache entry exists.
        effective = local if local.cdtext_status == 'available' else cached.local
        report.update(
            lookup_status='cached', match_status='cached',
            selected_release_id=cached.release_id,
            match_score=cached.confidence_score,
            match_method=cached.match_method,
            local_metadata=local.to_dict(),
            effective_metadata=effective.to_dict())
    elif args.offline or not online:
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
        if args.cache_db is not None and result.status in ('unique', 'ambiguous'):
            try:
                saved = DiscCache(args.cache_db).put(
                    identity, local, release_id=decision.selected_release_id,
                    match_method=decision.status if decision.selected_release_id else None,
                    confidence_score=decision.score if decision.selected_release_id else None)
                report['cache_saved'] = saved
            except (OSError, ValueError) as exc:
                report['cache_error'] = type(exc).__name__
    if args.fetch_cover and args.cover_dir is not None and report.get('selected_release_id'):
        try:
            image_path = CoverStore(args.cover_dir).get_or_download(
                report['selected_release_id'], policy=cover_policy)
            report['cover_file'] = str(image_path) if image_path else None
        except (OSError, ValueError, RuntimeError) as exc:
            report['cover_error'] = type(exc).__name__
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
