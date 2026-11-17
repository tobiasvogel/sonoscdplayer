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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--config', type=Path, default=player_config())
    parser.add_argument('--offline', action='store_true', help='DiscID only, without network')
    args = parser.parse_args(argv)
    try:
        enabled, online, timeout = read_settings(args.config)
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
        identity = identity_from_toc(toc)  # No further drive access.
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f'MusicBrainz probe failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    report = {'disc_id': identity.disc_id, 'toc': identity.toc,
              'track_count': len(toc.tracks)}
    if args.offline or not online:
        report['lookup_status'] = 'disabled'
    else:
        result = lookup_disc(identity, track_count=len(toc.tracks),
                             timeout_seconds=timeout)
        report.update(lookup_status=result.status,
                      selected_release_id=result.selected_release_id,
                      releases=[asdict(candidate) for candidate in result.releases])
        if result.error:
            report['lookup_error'] = result.error
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
