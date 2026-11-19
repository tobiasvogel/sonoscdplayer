#!/usr/bin/env python3
"""Read-only prerequisite and conflict checks for the setup orchestrator.

Preflight runs before package installation, file writes or service activation.
Checks never mutate the host. Only explicit setup choices are used.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import tomllib


CONFIG_NAMES = ('player.toml', 'interface.toml', 'ui.toml')


def verify_toml(path: Path, errors: list[str]) -> None:
    try:
        tomllib.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        errors.append(f'Invalid configuration {path}: {exc}')


def inspect_configs(repo: Path, config_dir: Path, create_config: bool,
                    need_config: bool, errors: list[str]) -> None:
    for name in CONFIG_NAMES:
        target = config_dir / name
        if target.is_symlink():
            errors.append(f'Configuration is a symlink: {target}')
        elif target.is_file():
            verify_toml(target, errors)
        elif target.exists():
            errors.append(f'Configuration is not a regular file: {target}')
        elif create_config:
            source = repo / 'config' / name.replace('.toml', '.example.toml')
            verify_toml(source, errors)
        elif need_config and name != 'ui.toml':
            errors.append(f'Missing required configuration: {target} (choose --config yes)')


def inspect_runtime_units(repo: Path, service_user: str, venv: Path, flags: dict[str, bool],
                          sensor: bool, early_led: bool, shutdown: bool,
                          errors: list[str], warnings: list[str], preview: bool) -> None:
    renderer = repo / 'tools/render_runtime_units.py'
    installer = repo / 'tools/install_runtime_services.py'
    if not renderer.is_file() or not installer.is_file():
        errors.append('Missing service rendering or installation helper')
        return
    with tempfile.TemporaryDirectory(prefix='sonoscdplayer-preflight-') as tmp:
        cmd = [sys.executable, str(renderer), '--repo-root', str(repo),
               '--output-dir', tmp, '--checkout', str(repo / 'src'),
               '--python-venv', str(venv / 'bin/python'), '--service-user', service_user]
        for name, selected in flags.items():
            cmd.append(('--' if selected else '--no-') + name)
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        if result.returncode:
            errors.append(f'Unit rendering failed: {(result.stderr or result.stdout).strip()}')
            return
        spec = importlib.util.spec_from_file_location('sonos_setup_runtime_installer', installer)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            module.plan(Path(tmp), Path('/etc/systemd/system'),
                        Path('/etc/polkit-1/rules.d'),
                        sensor_unit=(repo / 'src/native/apds9306/service/apds9306d.service'
                                     if sensor else None),
                        target_unit=repo / 'systemd/sonoscdplayer.target',
                        early_led=early_led,
                        shutdown_units_dir=(repo / 'systemd' if shutdown else None))
        except PermissionError as exc:
            message = (f'Cannot inspect protected service/Polkit file: {exc}; '
                       'rerun with sudo for a complete conflict check')
            if os.geteuid() != 0 and preview:
                warnings.append(message)
            else:
                errors.append(message)
        except (OSError, ValueError) as exc:
            errors.append(f'Installed service conflict: {exc}')


def check(args: argparse.Namespace, *, pi_model: Path = Path('/proc/device-tree/model'),
          os_release: Path = Path('/etc/os-release')) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    repo = args.repo.resolve()
    if not (repo / 'scripts/packages.lst').is_file():
        errors.append('Missing scripts/packages.lst')
    if not (repo / 'src/sonoscdplayer/player/service.py').is_file():
        errors.append('Checkout does not contain src/sonoscdplayer/player/service.py')
    if os_release.is_file():
        data = dict(line.split('=', 1) for line in os_release.read_text().splitlines()
                    if '=' in line and not line.startswith('#'))
        os_id = data.get('ID', '').strip('"')
        if os_id not in ('debian', 'raspbian'):
            (errors if args.mode == 'apply' else warnings).append(
                f'Unexpected OS ID {os_id!r}; expected Raspberry Pi OS (Debian based)')
    else:
        (errors if args.mode == 'apply' else warnings).append('Cannot identify operating system')
    try:
        model = pi_model.read_bytes().decode('utf-8', 'replace').rstrip('\x00')
    except OSError:
        model = ''
    if 'Raspberry Pi' not in model:
        (errors if args.mode == 'apply' else warnings).append('Raspberry Pi hardware not detected')
    if args.mode == 'apply' and os.geteuid() != 0:
        errors.append('Installation requires root (sudo)')

    try:
        user = pwd.getpwnam(args.service_user)
    except KeyError:
        errors.append(f'Service account does not exist: {args.service_user}')
    else:
        if user.pw_uid == 0:
            errors.append('Service account must not be root')

    def require_command(name: str, needed: bool, can_install: bool = False) -> None:
        if needed and shutil.which(name) is None:
            (warnings if can_install and args.packages else errors).append(
                f'Missing command {name}' + (' (expected from selected apt package installation)' if can_install and args.packages else ''))

    require_command('python3', True)
    require_command('apt-get', args.packages)
    require_command('raspi-config', args.spi or args.i2c)
    require_command('git', args.assembler, can_install=True)
    require_command('make', args.sensor, can_install=True)
    require_command('gcc', args.sensor, can_install=True)
    require_command('cc', args.screens, can_install=True)
    require_command('install', args.sensor or args.screens or args.services)
    require_command('systemctl', args.services)
    require_command('sudo', args.screens)
    if args.assembler and not (repo / 'tools/prepare_splash_assembler.sh').is_file():
        errors.append('Missing splash assembler preparation helper')

    # Same override as the player (src/sonoscdplayer/system/config_paths.py).
    config_dir = Path(os.environ.get('SONOSCDPLAYER_CONFIG_DIR') or '/etc/sonoscdplayer')
    inspect_configs(repo, config_dir, args.config,
                    args.services or args.screens, errors)
    if args.services:
        inspect_runtime_units(repo, args.service_user, args.venv,
                              {'power-actions': args.power_actions,
                               'drive-activity-events': args.drive_activity_events,
                               'connect-gestures': args.connect_gestures,
                               'network-setup': args.network_setup},
                              args.sensor, args.early_led, args.shutdown_services,
                              errors, warnings, args.mode == 'preview')
    if args.screens:
        for file in ('tools/build_shutdown_renderer.sh', 'tools/install_screens.sh',
                     'systemd/sonoscdplayer-final-shutdown-hook'):
            if not (repo / file).is_file():
                errors.append(f'Missing screen installation source: {file}')
        if not Path('/boot/firmware/config.txt').is_file():
            warnings.append('No /boot/firmware/config.txt found; boot splash/pin settings cannot be installed')
    if args.packages and (repo / 'scripts/packages.lst').is_file() and not (repo / 'scripts/packages.lst').read_text().strip():
        errors.append('OS package list is empty')
    if args.venv.exists() and not (args.venv / 'bin/python').is_file():
        errors.append(f'Existing venv path has no bin/python: {args.venv}')
    if args.venv.is_symlink():
        errors.append(f'Refusing symlink virtual environment: {args.venv}')
    return errors, warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--service-user', required=True)
    parser.add_argument('--venv', type=Path, required=True)
    parser.add_argument('--mode', choices=('apply', 'preview'), required=True)
    for key in ('packages', 'spi', 'i2c', 'config', 'sensor', 'services', 'screens',
                'assembler', 'early-led', 'shutdown-services', 'power-actions',
                'drive-activity-events', 'connect-gestures', 'network-setup'):
        parser.add_argument('--' + key, choices=('yes', 'no'), required=True)
    args = parser.parse_args(argv)
    for key in ('packages', 'spi', 'i2c', 'config', 'sensor', 'services', 'screens',
                'assembler', 'early_led', 'shutdown_services', 'power_actions',
                'drive_activity_events', 'connect_gestures', 'network_setup'):
        setattr(args, key, getattr(args, key) == 'yes')
    errors, warnings = check(args)
    for warning in warnings:
        print(f'[WARN] {warning}')
    for error in errors:
        print(f'[FAIL] {error}', file=sys.stderr)
    if errors:
        print(f'Preflight failed: {len(errors)} error(s)', file=sys.stderr)
        return 1
    print(f'Preflight passed ({len(warnings)} warning(s)); no system changes made.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
