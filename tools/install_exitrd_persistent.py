#!/usr/bin/env python3
"""Root-only persistent install/remove of the experimental exitrd boot stager.

Invoked after a separate non-root build and interactive risk acknowledgement.
Does not power off or reboot. Removes only files owned by this feature.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
INSTALL = Path('/usr/local/libexec/sonoscdplayer-exitrd')
UNIT = Path('/etc/systemd/system/sonoscdplayer-exitrd.service')
CONFIG = ROOT / 'src/config.toml'
FILES = ('exitrd_autostage.py', 'exitrd_once.py', 'exitrd_preflight.py')
NAME = 'sonoscdplayer-exitrd.service'
EXPERIMENTAL = ROOT / 'build/exitrd-unverified-safe/shutdown'
ACK = 'I_ACCEPT_EARLY_SAFE_SCREEN_RISK'
EXPECTED = ('safe-approved=0', 'diagnostic=0', 'unverified-safe=1', 'delay-seconds=3')


class InstallError(Exception):
    pass


def command(*argv):
    subprocess.run(argv, check=True, timeout=60)


def verified_config_text(text: str, enabled: bool) -> str:
    data = tomllib.loads(text)
    if not isinstance(data.get('service'), dict):
        raise InstallError('[service] table required in player config')
    if 'experimental_safe_screen' in data['service'] and not isinstance(
            data['service']['experimental_safe_screen'], bool):
        raise InstallError('experimental_safe_screen must be boolean')
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == '[service]'), None)
    if start is None:
        raise InstallError('[service] section not found')
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r'^\s*\[', lines[i])), len(lines))
    matched = [i for i in range(start + 1, end)
               if re.match(r'^\s*experimental_safe_screen\s*=', lines[i])]
    if len(matched) > 1:
        raise InstallError('duplicate experimental setting')
    value = 'true' if enabled else 'false'
    if matched:
        lines[matched[0]] = f'experimental_safe_screen = {value}\n'
    else:
        lines.insert(end, f'experimental_safe_screen = {value}\n')
    rendered = ''.join(lines)
    if tomllib.loads(rendered)['service']['experimental_safe_screen'] is not enabled:
        raise InstallError('config verification failed')
    return rendered


def write_config(enabled: bool):
    if CONFIG.is_symlink() or not CONFIG.is_file():
        raise InstallError('actual player config missing or symlinked')
    initial = CONFIG.read_text(encoding='utf-8')
    rewritten = verified_config_text(initial, enabled)
    if rewritten == initial:
        return
    state = CONFIG.stat()
    handle, name = tempfile.mkstemp(prefix='.config-exitrd-', dir=CONFIG.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(rewritten)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, stat.S_IMODE(state.st_mode))
        os.chown(name, state.st_uid, state.st_gid)
        os.replace(name, CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def safe_remove_file(path: Path):
    if path.is_symlink():
        raise InstallError(f'refusing symlink: {path}')
    if path.exists():
        if not path.is_file():
            raise InstallError(f'not a regular file: {path}')
        path.unlink()


def install(expected: str):
    if UNIT.exists() or UNIT.is_symlink() or INSTALL.exists() or INSTALL.is_symlink():
        raise InstallError('feature already installed or conflicting installation exists; uninstall first')
    if len(expected) != 64 or any(x not in '0123456789abcdef' for x in expected):
        raise InstallError('invalid SHA-256')
    if not EXPERIMENTAL.is_file() or EXPERIMENTAL.is_symlink():
        raise InstallError('experimental binary missing')
    payload = EXPERIMENTAL.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected:
        raise InstallError('binary SHA-256 mismatch')
    result = subprocess.run([str(EXPERIMENTAL), '--check'],
                            capture_output=True, text=True, check=True, timeout=10)
    if any(result.stdout.split().count(token) != 1 for token in EXPECTED):
        raise InstallError('wrong binary mode')
    # Reject unmanaged RAM exitrd before installing. Never overwrite an existing stage.
    from exitrd_once import precheck
    precheck(EXPERIMENTAL, expected, activating=True)
    verified_config_text(CONFIG.read_text(encoding='utf-8'), True)
    try:
        INSTALL.mkdir(mode=0o755, parents=False, exist_ok=False)
        for file in FILES:
            shutil.copyfile(ROOT / 'tools' / file, INSTALL / file)
            os.chmod(INSTALL / file, 0o644)
        (INSTALL / 'shutdown').write_bytes(payload)
        os.chmod(INSTALL / 'shutdown', 0o755)
        (INSTALL / 'shutdown.sha256').write_text(expected + '\n', encoding='ascii')
        os.chmod(INSTALL / 'shutdown.sha256', 0o644)
        shutil.copyfile(ROOT / 'systemd' / NAME, UNIT)
        os.chmod(UNIT, 0o644)
        write_config(True)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'enable', '--now', NAME)
        from exitrd_once import LIVE, MARKER, require_host, is_mountpoint
        if (not is_mountpoint(LIVE, require_host()) or
                not MARKER.is_file() or
                MARKER.read_text() != f'sha256={expected}\n'):
            raise InstallError('boot stager did not confirm the expected RAM exitrd')
        print('INSTALLED and RAM-staged; automatically re-armed on future boots.')
        print('NO POWEROFF TRIGGERED. Wait for ACT LED after any later shutdown.')
    except BaseException:
        # Never leave a partially installed auto-start service enabled.
        subprocess.run(['systemctl', 'disable', NAME], check=False, timeout=20)
        if UNIT.exists():
            UNIT.unlink()
        subprocess.run(['systemctl', 'daemon-reload'], check=False, timeout=20)
        try:
            from exitrd_once import LIVE, MARKER, require_host, is_mountpoint, disarm
            if is_mountpoint(LIVE, require_host()) and MARKER.is_file():
                disarm()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            write_config(False)
        except (OSError, InstallError, tomllib.TOMLDecodeError):
            pass
        raise


def remove():
    # Always disable autostaging before attempting any RAM cleanup.
    if UNIT.exists() or UNIT.is_symlink():
        command('systemctl', 'disable', NAME)
    if UNIT.exists() or UNIT.is_symlink():
        safe_remove_file(UNIT)
    command('systemctl', 'daemon-reload')
    from exitrd_once import LIVE, MARKER, disarm, require_host, is_mountpoint
    mountinfo = require_host()
    if is_mountpoint(LIVE, mountinfo):
        # Refuse to disarm an exitrd that does not belong to this installer.
        if not MARKER.is_file() or MARKER.is_symlink():
            raise InstallError('unrecognized staged exitrd; leaving it untouched')
        disarm()
    for file in (*FILES, 'shutdown', 'shutdown.sha256'):
        safe_remove_file(INSTALL / file)
    if INSTALL.is_dir():
        try:
            INSTALL.rmdir()
        except OSError as exc:
            raise InstallError(f'unknown files in installed directory: {exc}') from exc
    write_config(False)
    print('UNINSTALLED; experimental player setting disabled; no shutdown triggered.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--install', action='store_true')
    group.add_argument('--uninstall', action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--risk-ack', default='')
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise InstallError('root required')
        if args.install:
            if args.risk_ack != ACK or not args.expected_sha256:
                raise InstallError('explicit risk acknowledgement and SHA-256 required')
            install(args.expected_sha256)
        else:
            remove()
        return 0
    except (InstallError, OSError, ValueError, subprocess.SubprocessError,
            tomllib.TOMLDecodeError) as exc:
        print(f'exitrd-install: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
