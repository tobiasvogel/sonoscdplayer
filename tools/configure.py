#!/usr/bin/env python3
"""Interactive, conservative first-pass player.toml configuration wizard.

Preview never modifies files. Apply delegates backup, idle checks, atomic swap,
service restart and rollback to config_swap.py. Existing TOML comments and
unrelated values are preserved verbatim.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import string
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
SWAP_PATH = ROOT / 'tools' / 'config_swap.py'
LIMITS = (16, 512)
# Align these ranges and constraints with player.service and drive.power.
FIELD_RANGES = {
    ('streaming', 'max_buffer_mib'): LIMITS,
    ('audio_startup', 'pcm_timeout_seconds'): (5, 120),
    ('drive_power', 'keepalive_interval_seconds'): (3, 3600),
    ('drive_power', 'idle_spin_down_seconds'): (30, 86400),
    ('metadata.musicbrainz', 'timeout_seconds'): (1, 30),
    ('metadata.matching', 'confidence_threshold'): (0, 100),
    ('metadata.matching', 'minimum_margin'): (0, 100),
    ('metadata.matching', 'minimum_track_titles'): (1, 99),
    ('ui.cover', 'max_dimension_px'): (64, 2048),
    ('ui.cover', 'jpeg_quality'): (40, 95),
    ('ui.cover', 'budget_mb'): (1, 10000),
}
DRIVE_STRATEGIES = ('off', 'start', 'keepalive', 'power_condition', 'auto')
# Optional values are taken from the shipped reference configuration.
REFERENCE = ROOT / "config" / "player.example.toml"
UI_REFERENCE = ROOT / 'config' / 'ui.example.toml'
UI_SERVICES = ('sonoscdplayer-player.service', 'sonoscdplayer-oled.service')
UI_FIELDS = (
    ('ui', 'language', 'Interface language (de/en)', 'language'),
    ('ui.text_input', 'extra_characters', 'Extra encoder text characters', 'extra_chars'),
    ('ui.cover', 'mode', 'Cover art mode (auto/on/off)', 'cover_mode'),
    ('ui.cover', 'max_dimension_px', 'Maximum cover dimension (pixels)', 'int'),
    ('ui.cover', 'jpeg_quality', 'JPEG quality', 'int'),
    ('ui.cover', 'budget_mb', 'Cover cache budget (MB)', 'int'),
)

FIELDS = (
    ('streaming', 'max_buffer_mib', 'PCM ring buffer (MiB)', 'int'),
    ('metadata', 'enabled', 'Enable CD metadata', 'bool'),
    ('metadata', 'read_cd_text', 'Read CD-TEXT from discs', 'bool'),
    ('metadata', 'online_lookup', 'Look up metadata online', 'bool'),
    ('metadata.musicbrainz', 'enabled', 'Enable MusicBrainz provider', 'bool'),
    ('metadata.musicbrainz', 'timeout_seconds', 'MusicBrainz request timeout (seconds)', 'int'),
    ('metadata', 'auto_match', 'Automatically select matched releases', 'bool'),
    ('metadata.matching', 'confidence_threshold', 'Matching confidence threshold (0..100)', 'int'),
    ('metadata.matching', 'minimum_margin', 'Minimum score margin (0..100)', 'int'),
    ('metadata.matching', 'minimum_track_titles', 'Minimum comparable titles (1..99)', 'int'),
    ('sonos', 'use_queue', 'Use the Sonos queue', 'bool'),
    ('playback', 'continue', 'Continue to the next track', 'bool'),
    ('audio_startup', 'prebuffer_before_sonos', 'Prebuffer audio before Sonos starts', 'bool'),
    ('audio_startup', 'pcm_timeout_seconds', 'PCM startup timeout (seconds)', 'int'),
    ('drive_power', 'enabled', 'Enable CD drive power management', 'bool'),
    ('drive_power', 'strategy', 'CD drive power strategy', 'strategy'),
    ('drive_power', 'keepalive_interval_seconds', 'Drive keepalive interval (seconds)', 'int'),
    ('drive_power', 'idle_spin_down_seconds', 'Idle spin-down after (seconds; off disables)', 'optional_int'),
    ('sonos', 'album_prefix', 'Album prefix shown in Sonos', 'album_prefix'),
    ('service', 'shutdown_policy', 'Playback behavior on shutdown', 'shutdown_policy'),
)
SECTION = re.compile(r'^\s*\[([^\]]+)\]\s*(?:#.*)?$')
ASSIGN = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(\"(?:\\.|[^\"\\])*\"|'[^']*'|[^#\r\n]*?)(\s*(?:#.*)?)$")


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
        group = parsed
        default_group = reference
        for component in section.split('.'):
            group = group.get(component, {}) if isinstance(group, dict) else None
            default_group = (default_group.get(component, {})
                             if isinstance(default_group, dict) else None)
        if not isinstance(group, dict):
            raise ValueError(f'invalid [{section}] section')
        if key in group:
            value = group[key]
        elif (section, key) == ('drive_power', 'idle_spin_down_seconds'):
            # Unlike the example, the runtime default for an omitted value is off.
            value = None
        elif (section, key) == ('streaming', 'max_buffer_mib'):
            raise ValueError('missing required [streaming].max_buffer_mib')
        else:
            value = default_group.get(key) if isinstance(default_group, dict) else None
            if value is None:
                raise ValueError(f'no reference default for {section}.{key}')
        field = (section, key)
        bounds = FIELD_RANGES.get(field)
        if ((kind == 'int' and (type(value) is not int or not bounds[0] <= value <= bounds[1]))
                or (kind == 'optional_int' and value is not None
                    and (type(value) is not int or not bounds[0] <= value <= bounds[1]))
                or (kind == 'strategy' and (type(value) is not str or value not in DRIVE_STRATEGIES))
                or (kind == 'shutdown_policy' and (type(value) is not str or value not in
                                                   ('stop_owned', 'leave_playing')))
                or (kind == 'album_prefix' and not valid_album_prefix(value))
                or (kind == 'bool' and type(value) is not bool)):
            raise ValueError(f'invalid {section}.{key}')
        current[(section, key)] = value
    return current


def valid_album_prefix(value):
    """Validate only printable, single-line Sonos album prefixes (max 64 chars)."""
    return (type(value) is str and len(value) <= 64 and
            all(ch.isprintable() for ch in value))


def valid_extra_characters(value):
    """Match the encoder's extra character rules; reject lossy input."""
    if type(value) is not str or len(value) > 64:
        return False
    ascii_chars = string.ascii_letters + string.digits + string.punctuation
    return (len(set(value)) == len(value) and all(
        ch.isprintable() and not ch.isspace() and ch not in ascii_chars for ch in value))


def existing_ui_values(parsed: dict, reference: dict) -> dict[tuple[str, str], object]:
    """Read optional ui.toml values without altering unrelated UI preferences."""
    current = {}
    for section, key, _, kind in UI_FIELDS:
        group, defaults = parsed, reference
        for part in section.split('.'):
            group = group.get(part, {}) if isinstance(group, dict) else None
            defaults = defaults.get(part, {}) if isinstance(defaults, dict) else None
        if not isinstance(group, dict) or not isinstance(defaults, dict):
            raise ValueError(f'invalid [{section}] section')
        value = group.get(key, defaults.get(key))
        bounds = FIELD_RANGES.get((section, key))
        if ((kind == 'int' and (type(value) is not int or not bounds[0] <= value <= bounds[1]))
                or (kind == 'language' and (type(value) is not str or value not in ('de', 'en')))
                or (kind == 'extra_chars' and not valid_extra_characters(value))
                or (kind == 'cover_mode' and (type(value) is not str
                                              or value not in ('auto', 'on', 'off')))):
            raise ValueError(f'invalid {section}.{key}')
        current[(section, key)] = value
    return current


def render_value(value: object) -> str:
    if value is None:
        return 'off'  # Only used for a removed optional idle spin-down setting.
    if type(value) is bool:
        return 'true' if value else 'false'
    if type(value) is str:
        return json.dumps(value, ensure_ascii=False)
    return str(value)


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
            if changes[key] is None:
                # TOML has no null. Missing idle_spin_down_seconds means disabled.
                lines[index] = ''
            else:
                lines[index] = (assignment.group(1) + assignment.group(2) + assignment.group(3)
                                + render_value(changes[key]) + assignment.group(5) + eol)
    missing = {key for key in changes.keys() - found if changes[key] is not None}
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
        elif '.' in section:
            # Nested table after existing top-level tables is always legal TOML.
            # Inserting it at the beginning could create an implicit parent and
            # make the existing explicit parent declaration invalid.
            if lines and not lines[-1].endswith(('\n', '\r')):
                additions.insert(0, '\n')
            inserts.append((len(lines), [f'\n[{section}]\n', *additions]))
        else:
            inserts.append((0, [f'[{section}]\n', *additions, '\n']))
    for position, additions in sorted(inserts, key=lambda p: p[0], reverse=True):
        lines[position:position] = additions
    result = ''.join(lines)
    tomllib.loads(result)
    return result


FIELD_HELP = {
    ('ui', 'language'): ('Language in ui.toml. A language already selected on the '
                           'device is saved in ui.json and takes precedence.'),
    ('ui.text_input', 'extra_characters'): ('Additional printable non-whitespace characters '
        'available for names entered with the encoder (not Wi-Fi passwords). '
        'Up to 64 characters; use - to clear the list.'),
    ('streaming', 'max_buffer_mib'): (
        'RAM used for PCM and the next track read-ahead. Valid range: 16..512 MiB. '
        'The recommendation is 10% of total physical RAM, capped at 512 MiB.'),
    ('metadata', 'enabled'): 'Enable CD metadata processing, including local titles and cover lookup.',
    ('metadata', 'read_cd_text'): 'Read titles directly from the inserted CD before online lookup.',
    ('metadata', 'online_lookup'): 'Allow online release identification when CD metadata is enabled.',
    ('metadata.musicbrainz', 'enabled'): 'Enable MusicBrainz as an online metadata provider.',
    ('metadata.musicbrainz', 'timeout_seconds'): 'One MusicBrainz request timeout: 1..30 seconds.',
    ('metadata', 'auto_match'): 'Select an unambiguous MusicBrainz release automatically.',
    ('metadata.matching', 'confidence_threshold'): 'Required matching score for ambiguous releases: 0..100.',
    ('metadata.matching', 'minimum_margin'): 'Minimum lead over competing release candidates: 0..100.',
    ('metadata.matching', 'minimum_track_titles'): 'Minimum CD-TEXT titles for ambiguous matches: 1..99.',
    ('ui.cover', 'mode'): ('auto downloads only when a display or Sonos uses covers; '
                           'on always enables cover downloads; off disables them.'),
    ('ui.cover', 'max_dimension_px'): 'Maximum cached image dimension: 64..2048 pixels.',
    ('ui.cover', 'jpeg_quality'): 'JPEG compression quality: 40..95.',
    ('ui.cover', 'budget_mb'): 'Maximum cover cache size: 1..10000 MB.',
    ('sonos', 'use_queue'): 'Add CD tracks to the Sonos queue so album and artist are visible.',
    ('playback', 'continue'): 'Automatically play the next CD track after one ends.',
    ('audio_startup', 'prebuffer_before_sonos'): (
        'Wait for initial CD audio before asking Sonos to start playback.'),
    ('audio_startup', 'pcm_timeout_seconds'): (
        'Time allowed for initial PCM audio from the CD reader: 5..120 seconds.'),
    ('drive_power', 'enabled'): 'Allow controlled power management of the CD drive.',
    ('drive_power', 'strategy'): (
        'off disables motor control; start issues a motor start; keepalive keeps the '
        'drive awake; power_condition uses supported drive firmware; auto probes '
        'capabilities and falls back to keepalive.'),
    ('drive_power', 'keepalive_interval_seconds'): (
        'Interval between keepalive checks when active: 3..3600 seconds.'),
    ('drive_power', 'idle_spin_down_seconds'): (
        'After this much safe inactivity the drive may spin down: 30..86400 seconds. '
        'Type off to disable. Requires enabled drive management with auto or '
        'keepalive strategy; cannot combine with mode-page overrides.'),
    ('sonos', 'album_prefix'): ('Text prefixed to CD albums in Sonos. Up to 64 printable '
                               'characters; trailing spaces are preserved. Type - to clear.'),
    ('service', 'shutdown_policy'): ('stop_owned stops playback started by this player on '
                                    'shutdown; leave_playing leaves it playing.'),
}
MENU_GROUPS = (
    ('Streaming and playback', (('streaming', 'max_buffer_mib'), ('playback', 'continue'))),
    ('CD metadata', (
        ('metadata', 'enabled'), ('metadata', 'read_cd_text'),
        ('metadata', 'online_lookup'), ('metadata.musicbrainz', 'enabled'),
        ('metadata.musicbrainz', 'timeout_seconds'), ('metadata', 'auto_match'))),
    ('Sonos', (('sonos', 'use_queue'),)),
    ('CD drive and power management', (
        ('drive_power', 'enabled'), ('drive_power', 'strategy'),
        ('drive_power', 'keepalive_interval_seconds'), ('drive_power', 'idle_spin_down_seconds'))),
    ('Audio startup', (
        ('audio_startup', 'prebuffer_before_sonos'), ('audio_startup', 'pcm_timeout_seconds'))),
    ('Release matching (advanced)', (
        ('metadata.matching', 'confidence_threshold'),
        ('metadata.matching', 'minimum_margin'),
        ('metadata.matching', 'minimum_track_titles'))),
)


UI_MENU_GROUP = ('Cover art and cache (ui.toml)', tuple((s, k) for s, k, _, _ in UI_FIELDS if s == 'ui.cover'))
UI_LANGUAGE_GROUP = ('Interface language and text input (ui.toml)',
                     tuple((s, k) for s, k, _, _ in UI_FIELDS if s != 'ui.cover'))
# Letter shortcut preserves all existing numeric menu selections.
PLAYER_ADVANCED_GROUP = ('Additional player settings (player.toml)',
                         (('sonos', 'album_prefix'), ('service', 'shutdown_policy')))

def ask(label: str, current: object, suggestion: object, kind: str,
        *, help_text: str = '', bounds=None, choices=(), read=input, out=print) -> object:
    """Enter explicitly accepts the suggestion; k leaves current untouched."""
    while True:
        answer = read(f'{label} (current: {render_value(current)}; '
                      f'recommended: {render_value(suggestion)}) '
                      f'[{render_value(suggestion)}] (k=keep, ?=help): ')
        value = answer.strip()
        if value == '?':
            out(help_text or 'Enter a value or k to keep the existing setting.')
            continue
        if not value:
            return suggestion
        if value.lower() == 'k':
            return current
        if kind in ('int', 'optional_int'):
            if kind == 'optional_int' and value.lower() == 'off':
                return None
            low, high = bounds or LIMITS
            if re.fullmatch(r'\d+', value) and low <= int(value) <= high:
                return int(value)
            out(f'Enter a whole number from {low} to {high}'
                + (' or off.' if kind == 'optional_int' else '.'))
        elif kind == 'language':
            if value.lower() in ('de', 'en'):
                return value.lower()
            out('Choose de or en.')
        elif kind == 'extra_chars':
            if value == '-':
                return ''
            if valid_extra_characters(value):
                return value
            out('Enter up to 64 printable non-whitespace characters, or - to clear.')
        elif kind == 'cover_mode':
            if value.lower() in ('auto', 'on', 'off'):
                return value.lower()
            out('Choose auto, on or off.')
        elif kind == 'strategy':
            if value.lower() in choices:
                return value.lower()
            out('Choose one of: ' + ', '.join(choices))
        elif kind == 'shutdown_policy':
            if value in ('stop_owned', 'leave_playing'):
                return value
            out('Choose stop_owned or leave_playing.')
        elif kind == 'album_prefix':
            if value == '-':
                return ''
            # Unlike other wizard inputs, a prefix may end in spaces.
            if valid_album_prefix(answer):
                return answer
            out('Enter up to 64 printable characters on one line, or - to clear.')
        elif value.lower() in ('y', 'yes', 'true', '1'):
            return True
        elif value.lower() in ('n', 'no', 'false', '0'):
            return False
        else:
            out('Enter yes or no.')


def validate_combination(staged, parsed=None):
    """Refuse power configurations rejected by drive.power at service startup."""
    enabled = staged[('drive_power', 'enabled')]
    strategy = staged[('drive_power', 'strategy')]
    idle = staged[('drive_power', 'idle_spin_down_seconds')]
    if idle is not None and (not enabled or strategy not in ('auto', 'keepalive')):
        raise ValueError('idle spin-down requires enabled drive power with auto or keepalive strategy')
    overrides = (parsed or {}).get('drive_power', {}).get('mode_page_overrides', {})
    if idle is not None and overrides:
        raise ValueError('idle spin-down cannot be combined with drive power mode-page overrides')


def pending_changes(current, staged):
    """Only differences from the installed config, including reverted choices."""
    return {field: value for field, value in staged.items() if value != current[field]}


def review(current, staged, *, out=print):
    changes = pending_changes(current, staged)
    out('\nPending changes:')
    if not changes:
        out('  None (the installed configuration will remain unchanged).')
    for section, key, _, _ in (*FIELDS, *UI_FIELDS):
        field = section, key
        if field in changes:
            out(f'  {section}.{key}: {render_value(current[field])} '
                f'-> {render_value(changes[field])}')
    return changes


def interactive_choices(current, recommendation, *, parsed=None, ui_current=None,
                        interface_path=None, read=input, out=print):
    """Stage independent sections; review is the only way to finish with changes.

    Returning None means the user exited without accepting the review.
    Nothing is written by this function.
    """
    current = {**current, **(ui_current or {})}
    staged = dict(current)
    fields = {(section, key): (label, kind) for section, key, label, kind in (*FIELDS, *UI_FIELDS)}
    groups = (*MENU_GROUPS, UI_MENU_GROUP, UI_LANGUAGE_GROUP) if ui_current is not None else MENU_GROUPS
    while True:
        out('\nSonosCDPlayer Configuration')
        for n, (heading, _) in enumerate(groups, 1):
            out(f'  {n}. {heading}')
        finish = len(groups) + 1
        out(f'  {finish}. Review pending changes and finish')
        out('  d. Display hardware inventory (read-only)')
        out('  p. Display change preflight (read-only; switching blocked)')
        out('  a. Additional player settings (player.toml)')
        out('  0. Exit without applying changes')
        choice = read(f'Select a section [{finish}]: ').strip() or str(finish)
        if choice.lower() == 'd':
            show_display_inventory(parsed or {}, interface_path=interface_path, out=out)
            continue
        if choice.lower() == 'p':
            candidate = read('Display candidate [ssd1327/st7735s/sh1107]: ').strip().lower()
            if candidate not in ('ssd1327', 'st7735s', 'sh1107'):
                out('Unknown display candidate; no action taken.')
                continue
            bl = None
            if candidate == 'st7735s':
                value = read('Optional backlight BCM pin (blank=unknown): ').strip()
                if value:
                    if not value.isdecimal():
                        out('Invalid BCM pin; no action taken.')
                        continue
                    bl = int(value)
            show_display_preflight(parsed or {}, candidate, interface_path=interface_path,
                                   backlight_pin=bl, out=out)
            continue
        if choice == '0':
            return None
        if choice == str(finish):
            try:
                validate_combination(staged, parsed)
            except ValueError as exc:
                out(f'Configuration conflict: {exc}')
                continue
            changes = review(current, staged, out=out)
            if ui_current is not None and (any(key in ui_current for key in changes)
                                           and any(key not in ui_current for key in changes)):
                out('Changes affect both player.toml and ui.toml. Apply one file '
                    'per run; return to a section and revert the other file changes.')
                continue
            answer = read('Finish with these selections? [y/N]: ').strip().lower()
            if answer in ('y', 'yes'):
                return changes
            continue
        if choice.lower() == 'a':
            heading, group_fields = PLAYER_ADVANCED_GROUP
        elif not choice.isdigit() or not 1 <= int(choice) <= len(groups):
            out(f'Select a number from 0 to {finish}, or a/d/p.')
            continue
        else:
            heading, group_fields = groups[int(choice) - 1]
        out(f'\n{heading} (press k to keep the original value)')
        for field in group_fields:
            label, kind = fields[field]
            suggestion = (recommendation if field == ('streaming', 'max_buffer_mib')
                          else staged[field])
            staged[field] = ask(label, current[field], suggestion, kind,
                                help_text=FIELD_HELP[field],
                                bounds=FIELD_RANGES.get(field),
                                choices=DRIVE_STRATEGIES if kind == 'strategy' else (),
                                read=read, out=out)



def plan_update(changes, current, ui_current, *, player_path, ui_path, raw, ui_raw):
    """Select exactly one file; never attempt a partial two-file transaction."""
    player_changes = {key: value for key, value in changes.items() if key in current}
    ui_changes = {key: value for key, value in changes.items() if key in ui_current}
    if len(player_changes) + len(ui_changes) != len(changes):
        raise ValueError('unknown configuration change')
    if player_changes and ui_changes:
        raise ValueError('changes span player.toml and ui.toml; apply one file per run '
                         '(rollback is per file). Finish one group, rerun for the other.')
    if ui_changes and ui_raw is None:
        raise ValueError(f'{ui_path} does not exist; install ui.toml with tools/install_config.py first')
    ui = bool(ui_changes)
    path, original = (ui_path, ui_raw) if ui else (player_path, raw)
    updated = replace_values(original, ui_changes if ui else player_changes)
    tomllib.loads(updated)
    return path, updated, UI_REFERENCE if ui else REFERENCE, (
        list(UI_SERVICES) if ui else ['sonoscdplayer-player.service'])


def show_display_inventory(parsed, *, interface_path=None, out=print):
    """Read-only display report; never stages or writes configuration changes."""
    spec = importlib.util.spec_from_file_location(
        'sonos_display_inventory', ROOT / 'tools' / 'display_inventory.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('display_inventory.py unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if interface_path is None:
        out('WARNING: no interface.toml path specified; input pin audit INCOMPLETE.')
        lines = module.inventory_lines(parsed)
    else:
        lines = module.full_inventory_lines(parsed, Path(interface_path))
    for line in lines:
        out(line)


def show_display_preflight(parsed, target, *, interface_path=None, backlight_pin=None, out=print):
    """Show display-switch prerequisites without staging or changing hardware."""
    spec = importlib.util.spec_from_file_location(
        'sonos_display_preflight', ROOT / 'tools' / 'display_preflight.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('display_preflight.py unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if interface_path is None:
        interface = None
        out('UNKNOWN: no interface.toml path supplied; pin audit incomplete.')
    else:
        interface, warning = module.read_interface(Path(interface_path))
        if warning:
            out(warning)
    for line in module.preflight_lines(parsed, interface, target,
                                       backlight_pin=backlight_pin):
        out(line)


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
    p.add_argument('--ui-config', type=Path, help='ui.toml (default: same directory as player.toml)')
    p.add_argument('--display-report', action='store_true',
                   help='print read-only display inventory and exit (no wizard or writes)')
    p.add_argument('--display-preflight', choices=('ssd1327', 'st7735s', 'sh1107'),
                   help='read-only prerequisites for a display candidate; switching remains blocked')
    p.add_argument('--backlight-pin', type=int,
                   help='proposed ST7735S backlight BCM pin, for read-only conflict checking')
    p.add_argument('--interface-config', type=Path,
                   help='interface.toml for read-only display pin audit (default: same config directory)')
    p.add_argument('--apply', action='store_true', help='apply after preview and explicit confirmation')
    p.add_argument('--buffer-mib', type=int, help='explicit 16..512 MiB value for automation')
    p.add_argument('--yes', action='store_true', help='confirm apply without a terminal (requires --apply)')
    args = p.parse_args(argv)
    if args.display_report and (args.apply or args.yes or args.buffer_mib is not None):
        p.error('--display-report cannot be combined with --apply, --yes or --buffer-mib')
    if args.display_preflight and (args.display_report or args.apply or args.yes
                                   or args.buffer_mib is not None):
        p.error('--display-preflight is read-only; cannot combine with apply/report/buffer options')
    if args.backlight_pin is not None and args.display_preflight != 'st7735s':
        p.error('--backlight-pin requires --display-preflight st7735s')
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
        interface_path = args.interface_config or args.config.with_name('interface.toml')
        if args.display_report:
            show_display_inventory(parsed, interface_path=interface_path)
            return 0
        if args.display_preflight:
            show_display_preflight(parsed, args.display_preflight,
                                   interface_path=interface_path,
                                   backlight_pin=args.backlight_pin)
            return 0
        reference = tomllib.loads(REFERENCE.read_text(encoding='utf-8'))
        current = existing_values(parsed, reference)
        ui_path = args.ui_config or args.config.with_name('ui.toml')
        ui_raw = ui_path.read_text(encoding='utf-8') if ui_path.exists() else None
        ui_parsed = tomllib.loads(ui_raw) if ui_raw is not None else {}
        ui_reference = tomllib.loads(UI_REFERENCE.read_text(encoding='utf-8'))
        ui_current = existing_ui_values(ui_parsed, ui_reference)
        ram = total_ram_mib()
        recommendation = suggested_buffer(ram)
        print(f'Detected physical RAM: {ram} MiB; buffer recommendation: {recommendation} MiB (10%, capped at 512 MiB)')
        if sys.stdin.isatty():
            if args.buffer_mib is not None:
                raise ValueError('--buffer-mib is intended for non-interactive use')
            changes = interactive_choices(current, recommendation, parsed=parsed,
                                          ui_current=ui_current, interface_path=interface_path)
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
        if ('ui', 'language') in changes:
            print('Note: the language saved on the device in ui.json takes precedence over ui.toml. '
                  'Use the device language menu to change an existing saved selection.')
        target_config, updated, target_reference, target_services = plan_update(
            changes, current, ui_current, player_path=args.config, ui_path=ui_path,
            raw=raw, ui_raw=ui_raw)
        if not args.apply:
            print(f'Preview only: would update {target_config.name}; no files changed. '
                  'Rerun with --apply to install.')
            return 0
        if not args.yes:
            if not sys.stdin.isatty() or input('Type APPLY to confirm: ').strip() != 'APPLY':
                raise ValueError('changes not confirmed')
        swap = load_swap()
        with tempfile.TemporaryDirectory(prefix='sonoscdplayer-configure-') as directory:
            target = Path(directory) / target_config.name
            target.write_text(updated, encoding='utf-8')
            return swap.swap(target_config, target,
                             services=target_services, reference=target_reference, apply=True)
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
