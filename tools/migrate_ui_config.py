#!/usr/bin/env python3
"""Install local SonosCDPlayer TOML files without overwriting existing settings.

No systemd actions and no changes to source files. Existing legacy files are
copied byte-for-byte on first migration; examples are used only as fallback.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS = (
    ('player.toml', ROOT / 'src/config.toml', ROOT / 'config/player.example.toml'),
    ('interface.toml', ROOT / 'src/integrations/interfaces/interface.toml',
     ROOT / 'config/interface.example.toml'),
    ('ui.toml', None, ROOT / 'config/ui.example.toml'),
)


def install_configs(destination: Path, *, dry_run=True):
    destination = Path(destination)
    outcomes = []
    if not dry_run:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name, legacy, example in PAIRS:
        target = destination / name
        if target.exists() or target.is_symlink():
            outcomes.append((name, 'kept-existing'))
            continue
        source = legacy if legacy is not None and legacy.is_file() else example
        if dry_run:
            outcomes.append((name, 'would-copy-legacy' if source == legacy else 'would-copy-example'))
            continue
        # Exclusive create prevents accidental overwrite even if a competing
        # process creates the file after the initial check.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(source.read_bytes())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        outcomes.append((name, 'copied-legacy' if source == legacy else 'copied-example'))
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path,
                        default=Path.home() / 'sonoscdplayer')
    parser.add_argument('--apply', action='store_true',
                        help='create missing files only; never overwrite')
    args = parser.parse_args(argv)
    for name, outcome in install_configs(args.destination, dry_run=not args.apply):
        print(f'{name}: {outcome}')


if __name__ == '__main__':
    main()
