#!/usr/bin/env python3
"""Opt-in, RAM-only, one-boot exitrd arming. Default action is read-only."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

from exitrd_preflight import PreflightError, native_arch, read_binary, mount_fs_type

LIVE = Path('/run/initramfs')
MARKER = Path('/run/sonoscdplayer-exitrd-once.marker')
ACK = 'I_UNDERSTAND_EXITRD_CAN_PREVENT_CLEAN_SHUTDOWN'
MAX_PRESERVE_BYTES = 1024 * 1024


def run_command(*args):
    subprocess.run(args, check=True, timeout=15)


def require_host():
    if not Path('/run/systemd/system').is_dir():
        raise PreflightError('systemd host runtime marker missing')
    if not LIVE.is_dir() or LIVE.is_symlink():
        raise PreflightError('/run/initramfs must be a real directory')
    if LIVE.stat().st_uid != 0 or stat.S_IMODE(LIVE.stat().st_mode) != 0o700:
        raise PreflightError('/run/initramfs must be root-owned and mode 0700')
    mountinfo = Path('/proc/self/mountinfo').read_text()
    if mount_fs_type(LIVE, mountinfo) != 'tmpfs':
        raise PreflightError('/run/initramfs must live on tmpfs')
    return mountinfo


def is_mountpoint(path: Path, mountinfo: str) -> bool:
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[4] == str(path):
            return True
    return False


def preserved_files() -> dict[str, bytes]:
    """Refuse unexpected initramfs contents instead of concealing them."""
    result = {}
    for child in LIVE.iterdir():
        if child.is_symlink() or not child.is_file():
            raise PreflightError(f'unexpected initramfs entry: {child.name}')
        if child.name not in ('fsck.log', 'fsck-root'):
            raise PreflightError(f'unknown initramfs entry: {child.name}')
        if child.stat().st_size > MAX_PRESERVE_BYTES:
            raise PreflightError(f'initramfs file too large: {child.name}')
        result[child.name] = child.read_bytes()
    return result


def precheck(binary: Path, expected_sha256: str | None, *, activating: bool):
    mountinfo = require_host()
    if is_mountpoint(LIVE, mountinfo):
        raise PreflightError('/run/initramfs already separately mounted; refusing overlay')
    if MARKER.exists() or MARKER.is_symlink():
        raise PreflightError('previous exitrd activation marker exists')
    if (LIVE / 'shutdown').exists() or (LIVE / 'shutdown').is_symlink():
        raise PreflightError('existing exitrd; refusing overwrite')
    preserved = preserved_files()
    data, arch = read_binary(binary)
    if arch != native_arch():
        raise PreflightError(f'wrong ELF architecture: {arch} instead of {native_arch()}')
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 and digest != expected_sha256.lower():
        raise PreflightError('SHA-256 mismatch; refusing activation')
    if activating and not expected_sha256:
        raise PreflightError('--expected-sha256 is mandatory for activation')
    if activating:
        for dev in ('/dev/spidev0.0', '/dev/gpiochip0'):
            if not stat.S_ISCHR(Path(dev).stat().st_mode):
                raise PreflightError(f'required device missing: {dev}')
    return data, digest, preserved


def arm_once(data: bytes, digest: str, preserved: dict[str, bytes]):
    if os.geteuid() != 0:
        raise PreflightError('root required for --activate-once')
    mounted = False
    try:
        run_command('mount', '-t', 'tmpfs',
                    '-o', 'rw,nosuid,nodev,exec,size=8m,mode=0700',
                    'tmpfs', str(LIVE))
        mounted = True
        for name, payload in preserved.items():
            (LIVE / name).write_bytes(payload)
            os.chmod(LIVE / name, 0o600)
        fd = os.open(LIVE / 'shutdown',
                     os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o700)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            (LIVE / 'shutdown').unlink(missing_ok=True)
            raise
        if hashlib.sha256((LIVE / 'shutdown').read_bytes()).hexdigest() != digest:
            raise PreflightError('staged binary SHA-256 mismatch')
        run_command(str(LIVE / 'shutdown'), '--check')
        with MARKER.open('x') as f:
            f.write('sha256=' + digest + '\n')
        os.chmod(MARKER, 0o600)
        print('ARMED FOR THIS BOOT ONLY: /run/initramfs/shutdown')
        print('NO SHUTDOWN TRIGGERED; --deactivate undoes the mount')
    except BaseException:
        MARKER.unlink(missing_ok=True)
        if mounted:
            try:
                run_command('umount', str(LIVE))
            except (OSError, subprocess.SubprocessError) as exc:
                print(f'CRITICAL: rollback umount failed: {exc}', file=sys.stderr)
        raise


def disarm():
    if os.geteuid() != 0:
        raise PreflightError('root required for --deactivate')
    mountinfo = require_host()
    if not is_mountpoint(LIVE, mountinfo):
        raise PreflightError('no separate /run/initramfs mount; refusing removal')
    if not MARKER.is_file() or MARKER.is_symlink():
        raise PreflightError('no trusted activation marker; refusing unmount')
    candidate = LIVE / 'shutdown'
    if not candidate.is_file() or candidate.is_symlink():
        raise PreflightError('shutdown binary missing or modified')
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if MARKER.read_text() != f'sha256={digest}\n':
        raise PreflightError('shutdown digest does not match activation marker')
    run_command('umount', str(LIVE))
    MARKER.unlink()
    print('DISARMED: previous /run/initramfs contents visible again')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--activate-once', action='store_true')
    actions.add_argument('--deactivate', action='store_true')
    parser.add_argument('--binary', type=Path, default=Path('build/exitrd/shutdown'))
    parser.add_argument('--expected-sha256')
    parser.add_argument('--acknowledge', default='')
    args = parser.parse_args(argv)
    try:
        if args.deactivate:
            disarm()
            return 0
        if args.activate_once and args.acknowledge != ACK:
            raise PreflightError('explicit --acknowledge token required')
        data, digest, preserved = precheck(args.binary, args.expected_sha256,
                                           activating=args.activate_once)
        print(f'CHECK: static ELF; sha256={digest}; preserved_files={sorted(preserved)}')
        if not args.activate_once:
            print('READ-ONLY: no mount, no copy, no activation')
            return 0
        arm_once(data, digest, preserved)
        return 0
    except (OSError, PreflightError, subprocess.SubprocessError) as exc:
        print(f'exitrd-once: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
