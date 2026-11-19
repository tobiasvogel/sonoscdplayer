#!/usr/bin/env python3
"""Install reviewed rendered units and polkit rules without implicit activation.

Use render_runtime_units.py first. Existing different files and drop-ins block
installation; nothing is replaced implicitly. No changes unless --apply.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

UNITS = (
    'sonoscdplayer-player.service', 'sonoscdplayer-led.service',
    'sonoscdplayer-oled.service', 'sonoscdplayer-buttons.service',
    'sonoscdplayer-early-power-led.service',
)
RULES = ('50-sonoscdplayer-power.rules', '50-sonoscdplayer-network.rules')
SENSOR = 'apds9306d.service'
TARGET = 'sonoscdplayer.target'


def plan(rendered, unit_dir, polkit_dir, sensor_unit=None, target_unit=None, early_led=False):
    """Validate all files before any installation; return (source, target) pairs."""
    files = []
    for name in UNITS[:4] + ((UNITS[4],) if early_led else ()) + RULES:
        source = rendered / name
        if source.is_file() and not source.is_symlink():
            files.append((source, (unit_dir if name.endswith('.service') else polkit_dir) / name))
        elif name in UNITS[:4] or (early_led and name == UNITS[4]):
            raise ValueError(f'Missing required rendered unit: {source}')
    if target_unit:
        if not target_unit.is_file() or target_unit.is_symlink():
            raise ValueError(f'Missing runtime target: {target_unit}')
        files.append((target_unit, unit_dir / TARGET))
    if sensor_unit:
        if not sensor_unit.is_file() or sensor_unit.is_symlink():
            raise ValueError(f'Missing sensor service: {sensor_unit}')
        files.append((sensor_unit, unit_dir / SENSOR))
    for src, target in files:
        content = src.read_bytes()
        if b'@SONOSCDPLAYER_' in content:
            raise ValueError(f'Unrendered placeholder in {src}')
        if target.is_symlink():
            raise ValueError(f'Refusing symlink: {target}')
        if target.exists() and target.read_bytes() != content:
            raise ValueError(f'Existing file differs (manual migration required): {target}')
        if target.suffix == '.service':
            dropins = target.parent / (target.name + '.d')
            if dropins.is_dir() and list(dropins.glob('*.conf')):
                raise ValueError(f'Existing drop-ins require review: {dropins}')
    return files


def install_files(files):
    for source, target in files:
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusively create, no replacement of existing files.
        with source.open('rb') as inp:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            try:
                with os.fdopen(fd, 'wb') as output:
                    shutil.copyfileobj(inp, output)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        os.chmod(target, 0o644)
        print(f'Installed {target}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rendered-dir', type=Path, required=True)
    parser.add_argument('--sensor-unit', type=Path)
    parser.add_argument('--target-unit', type=Path, required=True)
    parser.add_argument('--early-led', action='store_true', help='Install rendered early LED unit')
    parser.add_argument('--unit-dir', type=Path, default=Path('/etc/systemd/system'))
    parser.add_argument('--polkit-dir', type=Path, default=Path('/etc/polkit-1/rules.d'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--enable', action='store_true', help='Enable units for next boot (no start)')
    parser.add_argument('--start', action='store_true', help='Start units now (explicit opt-in)')
    args = parser.parse_args(argv)
    if (args.enable or args.start) and not args.apply:
        parser.error('--enable and --start require --apply')
    try:
        files = plan(args.rendered_dir, args.unit_dir, args.polkit_dir, args.sensor_unit, args.target_unit, args.early_led)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for _, target in files:
        print(f'{"Keep" if target.exists() else "Would install"}: {target}')
    if not args.apply:
        print('Preview only; no changes made.')
        return 0
    if os.geteuid() != 0:
        parser.error('--apply requires root')
    install_files(files)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    services = [TARGET]
    if args.sensor_unit:
        services.append(SENSOR)
    if args.early_led:
        services.append(UNITS[4])
    if args.enable:
        subprocess.run(['systemctl', 'enable', *services], check=True)
    if args.start:
        subprocess.run(['systemctl', 'start', *services], check=True)
    print('Installation completed. No reboot requested.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
