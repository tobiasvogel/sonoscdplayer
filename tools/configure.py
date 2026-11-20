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
# Optional values are taken from the shipped reference configuration.
REFERENCE = ROOT / "config" / "player.example.toml"
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


def existing_values(parsed: dict, reference: dict) -> dict[tuple[str, str], object]:
    """Use shipped defaults for absent optional keys; do not invent required ones."""
    current = {}
    for section, key, _, kind in FIELDS:
        group = parsed.get(section, {})
        if not isinstance(group, dict):
            raise ValueError(f'invalid [{section}] section')
        if key in group:
            value = group[key]
        elif (section, key) == ('streaming', 'max_buffer_mib'):
            raise ValueError('missing required [streaming].max_buffer_mib')
        else:
            value = reference.get(section, {}).get(key)
            if value is None:
                raise ValueError(f'no reference default for {section}.{key}')
        if ((kind == 'int' and (type(value) is not int or not LIMITS[0] <= value <= LIMITS[1]))
                or (kind == 'bool' and type(value) is not bool)):
            raise ValueError(f'invalid {section}.{key}')
        current[(section, key)] = value
    return current


def render_value(value: object) -> str:
    return ('true' if value else 'false') if type(value) is bool else str(value)


def replace_values(raw: str, changes: dict[tuple[str, str], object]) -> str:
    """Change selected values, inserting only changed optional missing keys."""
    lines = raw.splitlines(keepends=True)
    section = None
    found = set()
    section_positions = {}
    for index, line in enumerate(lines):
        bare = line.rstrip('\r\n')
        section_match = SECTION.fullmatch(bare)
        if section_match:
            section = section_match.group(1)
            section_positions.setdefault(section, []).append(index)
        assignment = ASSIGN.fullmatch(bare)
        if assignment and (section, assignment.group(2)) in changes:
            key = (section, assignment.group(2))
            if key in found:
                raise ValueError(f'duplicate configuration key {section}.{key[1]}')
            found.add(key)
            eol = line[len(bare):]
            lines[index] = (assignment.group(1) + assignment.group(2) + assignment.group(3)
                            + render_value(changes[key]) + assignment.group(5) + eol)
    missing = changes.keys() - found
    if any(section == 'streaming' for section, _ in missing):
        raise ValueError('missing required [streaming].max_buffer_mib')
    # Insert a missing key at the end of its existing top-level table, before
    # the next table header (possibly a nested subsection). Missing tables are
    # inserted at the beginning, avoiding TOML parent-after-child conflicts.
    inserts = []
    grouped = {}
    for section, key in sorted(missing):
        grouped.setdefault(section, []).append(f'{key} = {render_value(changes[(section, key)])}\n')
    for section, additions in grouped.items():
        if section in section_positions:
            start = section_positions[section][0] + 1
            stop = next((i for i in range(start, len(lines))
                         if SECTION.fullmatch(lines[i].rstrip('\r\n'))), len(lines))
            if stop and lines[stop - 1] and not lines[stop - 1].endswith(('\n', '\r')):
                additions.insert(0, '\n')
            inserts.append((stop, additions))
        else:
            inserts.append((0, [f'[{section}]\n', *additions, '\n']))
    for position, additions in sorted(inserts, key=lambda p: p[0], reverse=True):
        lines[position:position] = additions
    result = ''.join(lines)
    tomllib.loads(result)
    return result


FIELD_HELP = {
    ('streaming', 'max_buffer_mib'): (
        'RAM used for PCM and the next track read-ahead. Valid range: 16..512 MiB. '
        'The recommendation is 10% of total physical RAM, capped at 512 MiB.'),
    ('metadata', 'online_lookup'): 'Look up CD metadata on MusicBrainz when enabled.',
    ('sonos', 'use_queue'): 'Add CD tracks to the Sonos queue so album and artist are visible.',
    ('playback', 'continue'): 'Automatically play the next CD track after one ends.',
}
MENU_GROUPS = (
    ('Streaming and playback', (('streaming', 'max_buffer_mib'), ('playback', 'continue'))),
    ('CD metadata', (('metadata', 'online_lookup'),)),
    ('Sonos', (('sonos', 'use_queue'),)),
)


def ask(label: str, current: object, suggestion: object, kind: str,
        *, help_text: str = '', read=input, out=print) -> object:
    """Enter explicitly accepts the suggestion; k leaves current untouched."""
    while True:
        value = read(f'{label} (current: {render_value(current)}; '
                     f'recommended: {render_value(suggestion)}) '
                     f'[{render_value(suggestion)}] (k=keep, ?=help): ').strip()
        if value == '?':
            out(help_text or 'Enter a value or k to keep the existing setting.')
            continue
        if not value:
            return suggestion
        if value.lower() == 'k':
            return current
        if kind == 'int':
            if re.fullmatch(r'\d+', value) and LIMITS[0] <= int(value) <= LIMITS[1]:
                return int(value)
            out(f'Enter a whole number from {LIMITS[0]} to {LIMITS[1]}.')
        elif value.lower() in ('y', 'yes', 'true', '1'):
            return True
        elif value.lower() in ('n', 'no', 'false', '0'):
            return False
        else:
            out('Enter yes or no.')


def pending_changes(current, staged):
    """Only differences from the installed config, including reverted choices."""
    return {field: value for field, value in staged.items() if value != current[field]}


def review(current, staged, *, out=print):
    changes = pending_changes(current, staged)
    out('\nPending changes:')
    if not changes:
        out('  None (the installed configuration will remain unchanged).')
    for section, key, _, _ in FIELDS:
        field = section, key
        if field in changes:
            out(f'  {section}.{key}: {render_value(current[field])} '
                f'-> {render_value(changes[field])}')
    return changes


def interactive_choices(current, recommendation, *, read=input, out=print):
    """Stage independent sections; review is the only way to finish with changes.

    Returning None means the user exited without accepting the review.
    Nothing is written by this function.
    """
    staged = dict(current)
    fields = {(section, key): (label, kind) for section, key, label, kind in FIELDS}
    while True:
        out('\nSonosCDPlayer Configuration')
        for n, (heading, _) in enumerate(MENU_GROUPS, 1):
            out(f'  {n}. {heading}')
        out('  4. Review pending changes and finish')
        out('  0. Exit without applying changes')
        choice = read('Select a section [4]: ').strip() or '4'
        if choice == '0':
            return None
        if choice == '4':
            changes = review(current, staged, out=out)
            answer = read('Finish with these selections? [y/N]: ').strip().lower()
            if answer in ('y', 'yes'):
                return changes
            continue
        if choice not in ('1', '2', '3'):
            out('Select 0, 1, 2, 3 or 4.')
            continue
        heading, group_fields = MENU_GROUPS[int(choice) - 1]
        out(f'\n{heading} (press k to keep the original value)')
        for field in group_fields:
            label, kind = fields[field]
            suggestion = (recommendation if field == ('streaming', 'max_buffer_mib')
                          else staged[field])
            staged[field] = ask(label, current[field], suggestion, kind,
                                help_text=FIELD_HELP[field], read=read, out=out)


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
        reference = tomllib.loads(REFERENCE.read_text(encoding='utf-8'))
        current = existing_values(parsed, reference)
        ram = total_ram_mib()
        recommendation = suggested_buffer(ram)
        print(f'Detected physical RAM: {ram} MiB; buffer recommendation: {recommendation} MiB (10%, capped at 512 MiB)')
        if sys.stdin.isatty():
            if args.buffer_mib is not None:
                raise ValueError('--buffer-mib is intended for non-interactive use')
            changes = interactive_choices(current, recommendation)
            if changes is None:
                print('Cancelled; nothing changed.')
                return 0
        else:
            if args.buffer_mib is None:
                raise ValueError('non-interactive use requires --buffer-mib; no implicit recommendation')
            if not LIMITS[0] <= args.buffer_mib <= LIMITS[1]:
                raise ValueError('--buffer-mib must be between 16 and 512')
            changes = pending_changes(current, {
                **current, ('streaming', 'max_buffer_mib'): args.buffer_mib})
            review(current, {**current, **changes})
        if not changes:
            print('No configuration changes selected.')
            return 0
        updated = replace_values(raw, changes)
        tomllib.loads(updated)  # validate the complete output before touching anything
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
                             reference=REFERENCE, apply=True)
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
