#!/usr/bin/env python3
"""Read-only, idle-only TOC and CD-TEXT diagnostic.

Run only when the SonosCDPlayer service is stopped (or after verifying it is
idle). A shared nonblocking drive lock prevents concurrent local CD reads.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from sonoscdplayer.audio.table_of_contents import read_toc
from sonoscdplayer.system.config_paths import player_config
from sonoscdplayer.metadata.local_metadata import build_local_metadata, read_cdtext
from sonoscdplayer.drive.drive_lock import drive_lock
from sonoscdplayer.drive.disc_startup import optical_devices


def metadata_flags(path: Path) -> tuple[bool, bool]:
    with path.open('rb') as handle:
        config = tomllib.load(handle)
    metadata = config.get('metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('[metadata] must be a table')
    for key in ('enabled', 'read_cd_text'):
        if key in metadata and not isinstance(metadata[key], bool):
            raise ValueError(f'[metadata] {key} must be boolean')
    return metadata.get('enabled', True), metadata.get('read_cd_text', True)


def inspect(device: str, *, read_text: bool):
    with drive_lock(device, blocking=False) as acquired:
        if not acquired:
            raise RuntimeError('drive busy; stop playback and retry later')
        toc = read_toc(device)
        if read_text:
            status, disc, tracks, error = read_cdtext(device)
        else:
            status, disc, tracks, error = 'disabled', {}, {}, None
        return build_local_metadata(toc, cdtext_status=status,
                                    disc_fields=disc, track_fields=tracks,
                                    error=error)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', default='auto', help='Optical device (default: auto)')
    parser.add_argument('--config', type=Path, default=player_config(),
                        help='Player TOML (default: /etc/sonoscdplayer/player.toml)')
    args = parser.parse_args(argv)
    try:
        enabled, read_text = metadata_flags(args.config)
        if not enabled:
            print('Metadata disabled by configuration', file=sys.stderr)
            return 2
        # Existing detection rules choose a device only if unambiguous.
        if args.device == 'auto':
            candidates = optical_devices()
            if len(candidates) != 1:
                raise RuntimeError('auto requires exactly one optical drive; use --device')
            device = candidates[0]
        else:
            device = args.device
        report = inspect(device, read_text=read_text)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        print(f'Metadata probe failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
