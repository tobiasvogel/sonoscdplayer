#!/usr/bin/env python3
"""Interactive, conservative first-pass player.toml configuration wizard.

Preview never modifies files. Apply delegates backup, idle checks, atomic swap,
service restart and rollback to config_swap.py. Existing TOML comments and
unrelated values are preserved verbatim.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SWAP_PATH = ROOT / 'tools' / 'config_swap.py'
LIMITS = (16, 512)
FIELDS = (
    ('streaming', 'max_buffer_mib', 'PCM ring buffer (MiB)', 'int'),
    ('metadata', 'online_lookup', 'Look up metadata online', 'bool'),
    ('sonos', 'use_queue', 'Use the Sonos queue', 'bool'),
    ('playback', 'continue', 'Continue to the next track', 'bool'),
)
SECTION = re.compile(r'^\s*\[([^\]]+)\]\s*(?:#.*)?$')
ASSIGN = re.compile(r'^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)$')


def total_ram_mib(meminfo: Path = Path('/proc/meminfo')) -> int:
    """Report physical RAM (MemTotal), never fluctuating MemAvailable."""
    for line in meminfo.read_text(encoding='ascii').splitlines():
        match = re.fullmatch(r'MemTotal:\s*(\d+)\s+kB\s*', line)
        if match:
            return int(match.group(1)) // 1024
    raise ValueError('MemTotal not found in /proc/meminfo')


def suggested_buffer(total_mib: int) -> int:
    """10% of total RAM, nearest MiB, clamped to the configured supported range."""
    if not isinstance(total_mib, int) or total_mib <= 0:
        raise ValueError('invalid total RAM')
    return max(LIMITS[0], min(LIMITS[1], (total_mib + 5) // 10))


def replace_values(raw: str, changes: dict[tuple[str, str], object]) -> str:
    """Patch only existing keys in their existing sections, preserving comments."""
    lines = raw.splitlines(keepends=True)
    section = None
    found = set()
    output = []
    for line in lines:
        bare = line.rstrip('\r\n')
        eol = line[len(bare):]
        section_match = SECTION.fullmatch(bare)
        if section_match:
            section = section_match.group(1)
        assignment = ASSIGN.fullmatch(bare)
        if assignment and (section, assignment.group(2)) in changes:
            key = (section, assignment.group(2))
            if key in found:
                raise ValueError(f'duplicate configuration key {section}.{key[1]}')
            found.add(key)
            value = changes[key]
            rendered = ('true' if value else 'false') if type(value) is bool else str(value)
            line = (assignment.group(1) + assignment.group(2) + assignment.group(3)
                    + rendered + assignment.group(5) + eol)
        output.append(line)
    missing = changes.keys() - found
    if missing:
        raise ValueError('missing keys: ' + ', '.join('.'.join(p) for p in sorted(missing)))
    return ''.join(output)


def ask(label: str, current: object, suggestion: object, kind: str) -> object:
    """Enter explicitly accepts suggestion; CLI automation does not use suggestions."""
    while True:
        value = input(f'{label} (current: {current}; suggested: {suggestion}) [{suggestion}]: ').strip()
        if not value:
            return suggestion
        if kind == 'int':
            if re.fullmatch(r'\d+', value) and LIMITS[0] <= int(value) <= LIMITS[1]:
                return int(value)
            print(f'Enter a whole number from {LIMITS[0]} to {LIMITS[1]}.', file=sys.stderr)
        elif value.lower() in ('y', 'yes', 'true', '1'):
            return True
        elif value.lower() in ('n', 'no', 'false', '0'):
            return False
        else:
            print('Enter yes or no.', file=sys.stderr)


def load_swap():
    spec = importlib.util.spec_from_file_location('sonos_config_swap', SWAP_PATH)
    if not spec or not spec.loader:
        raise RuntimeError('config_swap.py unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, help='player.toml (default: installed location)')
    p.add_argument('--apply', action='store_true', help='apply after preview and explicit confirmation')
    p.add_argument('--buffer-mib', type=int, help='explicit 16..512 MiB value for automation')
    p.add_argument('--yes', action='store_true', help='confirm apply without a terminal (requires --apply)')
    args = p.parse_args(argv)
    if args.yes and not args.apply:
        p.error('--yes requires --apply')
    if args.config is None:
        sys.path.insert(0, str(ROOT / 'src'))
        from sonoscdplayer.system.config_paths import player_config
        args.config = player_config()
    if args.apply and os.geteuid() != 0:
        p.error('--apply requires root; run with sudo')
    try:
        raw = args.config.read_text(encoding='utf-8')
        parsed = tomllib.loads(raw)
        current = {}
        for section, key, _, kind in FIELDS:
            value = parsed[section][key]
            if (kind == 'int' and (type(value) is not int or not LIMITS[0] <= value <= LIMITS[1])) or (
                kind == 'bool' and type(value) is not bool):
                raise ValueError(f'invalid {section}.{key}')
            current[(section, key)] = value
        ram = total_ram_mib()
        recommendation = suggested_buffer(ram)
        print(f'Detected physical RAM: {ram} MiB; buffer recommendation: {recommendation} MiB (10%, capped at 512 MiB)')
        changes = {}
        for section, key, label, kind in FIELDS:
            field = (section, key)
            suggestion = recommendation if field == ('streaming', 'max_buffer_mib') else current[field]
            if field == ('streaming', 'max_buffer_mib') and args.buffer_mib is not None:
                if not LIMITS[0] <= args.buffer_mib <= LIMITS[1]:
                    raise ValueError('--buffer-mib must be between 16 and 512')
                value = args.buffer_mib
            elif sys.stdin.isatty():
                value = ask(label, current[field], suggestion, kind)
            elif field == ('streaming', 'max_buffer_mib') and args.buffer_mib is None:
                raise ValueError('non-interactive use requires --buffer-mib; no implicit recommendation')
            else:
                value = current[field]
            if value != current[field]:
                changes[field] = value
        if not changes:
            print('No configuration changes selected.')
            return 0
        updated = replace_values(raw, changes)
        tomllib.loads(updated)  # validate the complete output before touching anything
        print('Proposed changes:')
        for section, key in changes:
            print(f'  {section}.{key}: {current[(section, key)]} -> {changes[(section, key)]}')
        if not args.apply:
            print('Preview only; no files changed. Rerun with --apply to install.')
            return 0
        if not args.yes:
            if not sys.stdin.isatty() or input('Type APPLY to confirm: ').strip() != 'APPLY':
                raise ValueError('changes not confirmed')
        swap = load_swap()
        # Same-directory temp is intentionally avoided: config_swap.replace_atomic()
        # handles the final same-filesystem atomic write and rollback itself.
        with tempfile.TemporaryDirectory(prefix='sonoscdplayer-configure-') as directory:
            target = Path(directory) / 'player.toml'
            target.write_text(updated, encoding='utf-8')
            return swap.swap(args.config, target, services=[swap.DEFAULT_SERVICE],
                             reference=ROOT / 'config/player.example.toml', apply=True)
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f'Configuration wizard: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        if exc.__class__.__name__ == 'SwapError':
            print(f'Configuration wizard: {exc}', file=sys.stderr)
            return 1
        raise


if __name__ == '__main__':
    raise SystemExit(main())
