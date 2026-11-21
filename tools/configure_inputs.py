#!/usr/bin/env python3
"""M4b.13 interface.toml input tuning; preview by default, explicit apply only.

No GPIO I/O or input events. The installed configuration is checked using the
production input parser BEFORE passing a proposed file to config_swap.
"""
from __future__ import annotations
import argparse
import importlib.util
from pathlib import Path
import sys
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sonoscdplayer.hardware.inputs.event_engine import load_config  # noqa: E402
from sonoscdplayer.system.config_paths import interface_config  # noqa: E402

# Pin mappings and shift registers deliberately excluded: electrical wiring
# must be audited separately. Names are section.key, never arbitrary TOML.
FIELDS = {
    'gpio.poll_interval_ms': ('int', 1, 1000),
    'power.long_press_ms': ('int', 3000, 10000),
    'volume.repeat_delay_ms': ('int', 100, 5000),
    'volume.repeat_interval_ms': ('int', 50, 2000),
    'input_defaults.pull': ('choice', ('off', 'up', 'down')),
    'input_defaults.active_level': ('choice', ('high', 'low')),
    'input_defaults.debounce_ms': ('int', 0, 250),
    'encoder_input.pull': ('choice', ('off', 'up', 'down')),
    'encoder_input.active_level': ('choice', ('high', 'low')),
    'encoder_input.button_debounce_ms': ('int', 0, 250),
    'encoder_input.steps_per_detent': ('choice_int', (1, 2, 4)),
    'encoder_input.invert_direction': ('bool',),
}


def parse_value(name, raw):
    spec = FIELDS[name]
    kind = spec[0]
    if kind == 'int':
        if not raw.isdecimal() or not spec[1] <= int(raw) <= spec[2]:
            raise ValueError(f'{name} must be {spec[1]}..{spec[2]}')
        return int(raw)
    if kind == 'choice_int':
        if not raw.isdecimal() or int(raw) not in spec[1]:
            raise ValueError(f'{name} must be one of {spec[1]}')
        return int(raw)
    if kind == 'choice':
        if raw not in spec[1]:
            raise ValueError(f'{name} must be one of {spec[1]}')
        return raw
    if raw not in ('true', 'false'):
        raise ValueError(f'{name} must be true or false')
    return raw == 'true'


def prepare(raw: str, changes: dict[str, object]) -> str:
    spec = importlib.util.spec_from_file_location('sonos_configure_shared', ROOT/'tools/configure.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    translated = {}
    for field, value in changes.items():
        if field not in FIELDS:
            raise ValueError('unsupported interface option: ' + field)
        translated[tuple(field.split('.', 1))] = value
    return module.replace_values(raw, translated)


def validate(candidate: str):
    """Use the exact production input loader, without touching GPIO."""
    with tempfile.TemporaryDirectory(prefix='sonos-input-validation-') as temp:
        path = Path(temp)/'interface.toml'
        path.write_text(candidate, encoding='utf-8')
        return load_config(path)



def interactive_preview(path, *, read=input, out=print):
    """Guide through input settings without ever applying configuration.

    Called from the main wizard's ``i`` menu. Edits are kept in memory only;
    a separate, explicitly confirmed CLI transaction is required to install.
    This separation prevents mixing interface.toml with staged player/ui edits.
    """
    import shlex

    if path is None:
        out('Input configuration location unknown; nothing changed.')
        return None
    path = Path(path)
    try:
        original = path.read_text(encoding='utf-8')
        installed = tomllib.loads(original)
        validate(original)  # refuse to tune a pre-existing invalid configuration
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        out(f'Input configuration unavailable or invalid: {exc}')
        return None
    staged = {}
    names = tuple(FIELDS)
    while True:
        out('\nButton / encoder configuration (PREVIEW ONLY)')
        for n, name in enumerate(names, 1):
            section, key = name.split('.', 1)
            current = installed.get(section, {}).get(key, '(runtime default)')
            proposed = staged.get(name, current)
            out(f'  {n}. {name}: {proposed!r}')
        out('  r. Review and finish (never applies changes)')
        out('  0. Cancel and discard selections')
        choice = read('Select input option [r]: ').strip().lower() or 'r'
        if choice == '0':
            out('Input changes discarded; no file was modified.')
            return None
        if choice == 'r':
            if not staged:
                out('No changes selected; interface.toml unchanged.')
                return {}
            out('Validated preview; interface.toml has NOT been changed:')
            for name, value in staged.items():
                out(f'  {name} = {value!r}')
            args = [f'--set {shlex.quote(name + "=" + str(value).lower() if type(value) is bool else name + "=" + str(value))}'
                    for name, value in staged.items()]
            command = (f'python3 tools/configure_inputs.py --config {shlex.quote(str(path))} '
                       + ' '.join(args))
            out('To preview independently: ' + command)
            out('To apply explicitly after review: sudo ' + command + ' --apply')
            return dict(staged)
        if not choice.isdecimal() or not 1 <= int(choice) <= len(names):
            out('Unknown option; no changes made.')
            continue
        name = names[int(choice) - 1]
        section, key = name.split('.', 1)
        current = installed.get(section, {}).get(key, '(runtime default)')
        value = read(f'{name} (installed: {current!r}; blank=keep): ').strip()
        if not value or value.lower() == 'k':
            staged.pop(name, None)
            continue
        try:
            parsed = parse_value(name, value)
            proposal = {**staged, name: parsed}
            candidate = prepare(original, proposal)
            validate(candidate)  # complete runtime parser, before accepting a field
        except (OSError, ValueError, KeyError, TypeError) as exc:
            out(f'Rejected: {exc}; previous selections preserved.')
            continue
        if parsed == current:
            staged.pop(name, None)
        else:
            staged[name] = parsed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=None)
    p.add_argument('--set', action='append', default=[], metavar='SECTION.KEY=VALUE')
    p.add_argument('--apply', action='store_true')
    args = p.parse_args(argv)
    path = args.config or interface_config()
    if not args.set:
        p.error('select one or more --set options (run --help for syntax)')
    if args.apply and getattr(__import__('os'), 'geteuid')() != 0:
        p.error('--apply requires root')
    try:
        raw = path.read_text(encoding='utf-8')
        changes = {}
        for item in args.set:
            name, sep, value = item.partition('=')
            if not sep or name not in FIELDS or name in changes:
                raise ValueError(f'unknown or duplicate interface option: {name}')
            changes[name] = parse_value(name, value)
        candidate = prepare(raw, changes)
        validate(candidate)
        if candidate == raw:
            print('No changes; interface.toml unchanged.')
            return 0
        print('Validated input changes (no GPIO opened):')
        for name, value in changes.items():
            print(f'  {name}: {value!r}')
        if not args.apply:
            print('PREVIEW ONLY: no files or services changed. Re-run with --apply.')
            return 0
        import importlib.util
        spec = importlib.util.spec_from_file_location('sonos_input_swap', ROOT/'tools/config_swap.py')
        swap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(swap)
        with tempfile.TemporaryDirectory(prefix='sonos-input-swap-') as temp:
            proposed = Path(temp)/'interface.toml'
            proposed.write_text(candidate, encoding='utf-8')
            return swap.swap(path, proposed, services=['sonoscdplayer-buttons.service'],
                             reference=ROOT/'config/interface.example.toml', apply=True)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        print(f'Input configuration: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
