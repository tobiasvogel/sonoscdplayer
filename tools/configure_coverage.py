#!/usr/bin/env python3
"""M4b.17: read-only coverage inventory for the configuration assistants.

Coverage is derived from shipped reference TOML and the *actual* wizard field
catalogues; neither installed configuration nor hardware is opened or changed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]

# Deliberately restricted until physical wiring and runtime ownership are
# independently proven; these must never be auto-exposed as wizard settings.
PIN_AND_BUS_FIELDS = frozenset({
    'led_outputs.power_pin', 'led_outputs.connection_pin',
    'display.controller', 'display.interface', 'display.spi_bus',
    'display.spi_device', 'display.dc_pin', 'display.rst_pin',
    'gpio.numbering', 'encoder.encoder_a', 'encoder.encoder_b',
    'encoder.encoder_sw',
})
# Not ordinary interactive configuration: needs a separate, dedicated safety
# workflow, or is managed outside the current running services.
SPECIAL_FIELDS = frozenset({
    'service.experimental_safe_screen', 'gpio.warnings',
    'led_outputs.ambient.socket_path', 'led_outputs.ambient.request',
})


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'unavailable assistant: {filename}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _paths(mapping: dict, prefix: str = '') -> set[str]:
    result = set()
    for name, value in mapping.items():
        path = f'{prefix}.{name}' if prefix else name
        if isinstance(value, dict):
            result.update(_paths(value, path))
        else:
            result.add(path)
    return result


def inventory() -> dict[str, dict[str, tuple[str, ...]]]:
    """Classify reference keys, not detected devices or live configuration."""
    configure = _load('sonos_coverage_main', 'configure.py')
    inputs = _load('sonos_coverage_inputs', 'configure_inputs.py')
    actual = {
        'player.toml': {f'{s}.{k}' for s, k, _, _ in configure.FIELDS},
        'ui.toml': {f'{s}.{k}' for s, k, _, _ in configure.UI_FIELDS},
        'interface.toml': set(inputs.FIELDS),
    }
    files = {
        'player.toml': ROOT / 'config/player.example.toml',
        'ui.toml': ROOT / 'config/ui.example.toml',
        'interface.toml': ROOT / 'config/interface.example.toml',
    }
    result = {}
    for name, path in files.items():
        with path.open('rb') as stream:
            reference = _paths(tomllib.load(stream))
        editable = reference & actual[name]
        # Missing keys in the reference are a sign of a broken wizard catalogue.
        unreferenced = actual[name] - reference
        guarded = (reference - editable) & (PIN_AND_BUS_FIELDS | SPECIAL_FIELDS)
        # Button assignments and per-button overrides require physical review.
        guarded |= {key for key in reference - editable
                    if key.startswith(('buttons.', 'shift_registers.',
                                       'button_overrides.'))}
        not_covered = reference - editable - guarded
        result[name] = {
            'editable': tuple(sorted(editable)),
            'guarded': tuple(sorted(guarded)),
            'not_covered': tuple(sorted(not_covered)),
            'unreferenced': tuple(sorted(unreferenced)),
        }
    return result


def report_lines(data=None) -> tuple[str, ...]:
    data = inventory() if data is None else data
    lines = [
        'SonosCDPlayer configuration coverage (READ-ONLY)',
        'Source: shipped example TOML files and wizard field catalogues.',
        'No installed config, GPIO, SPI, sensor or service was accessed.',
    ]
    for name, groups in data.items():
        lines.append(f'\n{name}: {len(groups["editable"])} editable, '
                     f'{len(groups["guarded"])} guarded, '
                     f'{len(groups["not_covered"])} not covered')
        for category, label in (('not_covered', 'NOT COVERED'),
                                ('guarded', 'GUARDED'),
                                ('unreferenced', 'CATALOGUE WITHOUT REFERENCE')):
            for field in groups[category]:
                lines.append(f'  {label}: {field}')
    lines.extend((
        '\nDisplay switching: BLOCKED; SSD1327 alone is player-integrated.',
        'APDS9306 calibration and timing remain separate future wizard work.',
        'This report is an inventory, NOT permission to change live hardware.',
    ))
    return tuple(lines)


def main() -> int:
    try:
        for line in report_lines():
            print(line)
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(f'Coverage inventory: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
