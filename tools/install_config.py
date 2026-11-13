#!/usr/bin/env python3
"""Create the SonosCDPlayer configuration files at the standard location.

The standard location is /etc/sonoscdplayer (see src/config_paths.py;
SONOSCDPLAYER_CONFIG_DIR overrides it). For each of player.toml,
interface.toml and ui.toml the source is, in this order:

1. an existing file of the same name in --from-dir (migrating an older
   installation, copied byte-for-byte), otherwise
2. the repository template config/<name>.example.toml.

Existing files at the destination are never overwritten or followed. Without
--apply nothing is written (dry run). Sources are never modified or deleted;
remove old copies yourself after verifying the services. No systemd actions.

With --service-user (required with --apply when run as root), the directory is
created root:<user's group> 0750 and files root:<user's group> 0640, so the
services can read but not modify their configuration.
"""
from __future__ import annotations

import argparse
import grp
import os
import pwd
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import config_paths  # noqa: E402

NAMES = (config_paths.PLAYER_TOML, config_paths.INTERFACE_TOML, config_paths.UI_TOML)


def example_for(name: str) -> Path:
    return ROOT / 'config' / name.replace('.toml', '.example.toml')


def pick_source(name: str, from_dir: Path | None) -> tuple[Path, str]:
    if from_dir is not None:
        legacy = Path(from_dir) / name
        if legacy.is_file() and not legacy.is_symlink():
            return legacy, 'migrated'
    return example_for(name), 'example'


def install_configs(destination: Path, *, from_dir: Path | None = None,
                    dry_run: bool = True, gid: int | None = None):
    """Return [(name, outcome)]; outcome is kept-existing, would-copy-*, copied-*."""
    destination = Path(destination)
    if not destination.is_absolute():
        raise ValueError('destination must be an absolute path')
    plan = []
    for name in NAMES:
        target = destination / name
        if target.exists() or target.is_symlink():
            plan.append((name, None, 'kept-existing'))
            continue
        source, kind = pick_source(name, from_dir)
        tomllib.loads(source.read_text(encoding='utf-8'))  # refuse invalid TOML early
        plan.append((name, source, kind))
    if dry_run:
        return [(name, kind if source is None else f'would-copy-{kind}')
                for name, source, kind in plan]
    if not destination.exists():
        destination.mkdir(mode=0o750, parents=True)
        if gid is not None:
            os.chown(destination, 0, gid)
        os.chmod(destination, 0o750)
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError('destination must be a real directory')
    outcomes = []
    for name, source, kind in plan:
        if source is None:
            outcomes.append((name, kind))
            continue
        target = destination / name
        # Exclusive create: never overwrite, even if created concurrently.
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(source.read_bytes())
            if gid is not None:
                os.chown(target, 0, gid)
            os.chmod(target, 0o640)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        outcomes.append((name, f'copied-{kind}'))
    return outcomes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--from-dir', type=Path,
                        help='directory with existing player/interface/ui.toml to migrate')
    parser.add_argument('--service-user',
                        help='account running the services; its group may read the files')
    parser.add_argument('--apply', action='store_true',
                        help='create missing files; never overwrite existing ones')
    args = parser.parse_args(argv)
    destination = config_paths.config_dir()
    gid = None
    if args.service_user:
        try:
            gid = pwd.getpwnam(args.service_user).pw_gid
        except KeyError:
            parser.error(f'unknown account: {args.service_user}')
        print(f'group: {grp.getgrgid(gid).gr_name}')
    if args.apply and os.geteuid() == 0 and gid is None:
        parser.error('--service-user is required with --apply as root')
    try:
        outcomes = install_configs(destination, from_dir=args.from_dir,
                                   dry_run=not args.apply, gid=gid)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    print(f'destination: {destination}')
    for name, outcome in outcomes:
        print(f'{name}: {outcome}')
    if not args.apply:
        print('Dry run only; nothing written. Repeat with --apply.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
