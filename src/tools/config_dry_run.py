#!/usr/bin/env python3
"""OPS-01: read-only TOML config structure check, with no value disclosure."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tomllib

MAX_BYTES = 1024 * 1024
SERVICES = frozenset({'sonoscdplayer-player.service', 'sonoscdplayer-buttons.service'})
SAFE_KEY = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


class InspectionError(ValueError):
    pass


def read_toml(path):
    """Reject symlinks, nonregular files, oversized files and invalid TOML."""
    try:
        fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                     | getattr(os, 'O_NONBLOCK', 0))
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise InspectionError('configuration must be a regular file <= 1 MiB')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise InspectionError('configuration exceeds 1 MiB')
        finally:
            os.close(fd)
    except OSError as exc:
        raise InspectionError('configuration unavailable or unsafe') from exc
    try:
        return tomllib.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise InspectionError('invalid TOML configuration') from exc


def effective_config_path(service, *, runner=subprocess.run):
    """Read effective systemd ExecStart. Never print raw argv or paths."""
    if service not in SERVICES:
        raise InspectionError('unsupported service identifier')
    try:
        result = runner(['systemctl', 'show', service, '-p', 'ExecStart', '--no-pager'],
                        text=True, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InspectionError('systemd inspection unavailable') from exc
    if result.returncode != 0:
        raise InspectionError('systemd inspection failed')
    match = re.search(r'(?:^|\n)ExecStart=\{[^\n]*?argv\[\]=(.*?)\s;\signore_errors=', result.stdout)
    if match is None:
        raise InspectionError('cannot safely identify effective ExecStart')
    try:
        args = shlex.split(match.group(1))
    except ValueError as exc:
        raise InspectionError('cannot parse effective ExecStart') from exc
    if args.count('--config') != 1:
        raise InspectionError('effective ExecStart must contain one --config')
    index = args.index('--config')
    if index + 1 >= len(args):
        raise InspectionError('effective ExecStart missing config path')
    path = Path(args[index + 1])
    if not path.is_absolute():
        raise InspectionError('effective config path must be absolute')
    return path


def kind(value):
    if isinstance(value, dict):
        return 'table'
    if isinstance(value, list):
        return 'array'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, int):
        return 'integer'
    if isinstance(value, float):
        return 'float'
    if isinstance(value, str):
        return 'string'
    return 'other'


def safe_path(parts):
    if all(SAFE_KEY.fullmatch(part) for part in parts):
        return '.'.join(parts)
    return None


def inspect(current, template):
    """Compare structure/types only; unknown current keys require review."""
    added, conflicts = [], []
    extra_count = 0
    redacted_count = 0

    def walk(old, new, parts=()):
        nonlocal extra_count, redacted_count
        for key, expected in new.items():
            here = parts + (key,)
            label = safe_path(here)
            if key not in old:
                if label is None:
                    redacted_count += 1
                else:
                    added.append(label)
                continue
            actual = old[key]
            if kind(actual) != kind(expected):
                if label is None:
                    redacted_count += 1
                else:
                    conflicts.append({'key': label, 'current_type': kind(actual),
                                      'template_type': kind(expected)})
            elif isinstance(expected, dict):
                walk(actual, expected, here)
        for key in old.keys() - new.keys():
            extra_count += 1  # Never print arbitrary local key names.

    walk(current, template)
    return {
        'status': ('review_required' if conflicts or extra_count or redacted_count
                   else 'compatible'),
        'read_only': True,
        'missing_template_keys': sorted(added),
        'type_conflicts': sorted(conflicts, key=lambda x: x['key']),
        'extra_current_keys_count': extra_count,
        'redacted_key_paths_count': redacted_count,
        'existing_values_compared': False,
        'changes_applied': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--current', help='existing TOML config path')
    source.add_argument('--service', choices=sorted(SERVICES),
                        help='read effective --config from systemd')
    parser.add_argument('--template', required=True, help='reference TOML config')
    parser.add_argument('--plan', action='store_true',
                        help='read-only, allowlisted migration proposal; never apply')
    args = parser.parse_args(argv)
    try:
        path = effective_config_path(args.service) if args.service else Path(args.current)
        current, template = read_toml(path), read_toml(args.template)
        if args.plan:
            if __package__:
                from .config_migration_plan import plan_migration
            else:
                # Direct invocation: python3 src/tools/config_dry_run.py
                from config_migration_plan import plan_migration
            report = plan_migration(current, template)
        else:
            report = inspect(current, template)
    except InspectionError as exc:
        print(json.dumps({'status': 'error', 'read_only': True,
                          'reason': str(exc), 'changes_applied': False}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'compatible' else 1


if __name__ == '__main__':
    sys.exit(main())
