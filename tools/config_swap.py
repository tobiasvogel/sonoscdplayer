#!/usr/bin/env python3
"""Replace the player configuration: stop the service, swap the file, start it.

Dry run unless --apply is given. With --apply:

  1. the new file must be valid TOML and, with --reference, must not change the
     type of any key the reference defines;
  2. the swap is refused while a CD plays or a Sonos session is owned
     (--force overrides this), so a running disc is never interrupted;
  3. the service is stopped, the old file is kept as <config>.bak-<timestamp>,
     the new file is moved into place atomically (owner and mode of the old
     file are kept) and the service is started again;
  4. if the service does not stay up, the old file is restored and started.

Only key names are ever printed, never values.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

MAX_BYTES = 1024 * 1024
DEFAULT_SOCKET = '/run/sonoscdplayer/control.sock'
DEFAULT_SERVICE = 'sonoscdplayer-player.service'
IDLE_MODES = frozenset({'NO_DISC', 'STOPPED', 'ERROR'})


class SwapError(Exception):
    """A refusal or failure with a message that is safe to print."""


def read_toml(path):
    """Return (raw bytes, parsed). Only regular files up to 1 MiB are accepted."""
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size > MAX_BYTES:
            raise SwapError(f'{path.name}: not a regular file or larger than 1 MiB')
        raw = path.read_bytes()
        return raw, tomllib.loads(raw.decode('utf-8'))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise SwapError(f'{path.name}: unreadable or invalid TOML ({type(exc).__name__})') from exc


def compare(new, reference, prefix=()):
    """Compare ``new`` with ``reference``; return (missing, conflicts, extra) key paths."""
    missing, conflicts, extra = [], [], []
    for key, ref in reference.items():
        here = prefix + (key,)
        if key not in new:
            missing.append('.'.join(here))
        elif isinstance(ref, dict) and isinstance(new[key], dict):
            sub = compare(new[key], ref, here)
            missing += sub[0]
            conflicts += sub[1]
            extra += sub[2]
        elif type(new[key]) is not type(ref):
            conflicts.append('.'.join(here))
    extra += ['.'.join(prefix + (key,)) for key in new if key not in reference]
    return sorted(missing), sorted(conflicts), sorted(extra)


def player_state(socket_path, *, timeout=2.0):
    """Return the cached status ``state`` of the running player, or None if none listens."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(socket_path))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        sock.sendall(b'{"command": "status"}\n')
        data = b''
        while b'\n' not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data.split(b'\n', 1)[0].decode('utf-8'))['state']


def busy_reason(read_state):
    """Return why the player must not be restarted now, or None when it is safe."""
    try:
        state = read_state()
        if state is None:
            return None  # no player is running, so there is nothing to interrupt
        mode = state['player']['mode']
        owned = state['sonos']['owned']
        cleanup = state['sonos']['group_cleanup_pending']
        streaming = state['stream']['active']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f'player status unavailable ({type(exc).__name__})'
    if mode not in IDLE_MODES:
        return f'player is {mode}'
    if owned:
        return 'a Sonos session is still owned'
    if cleanup:
        return 'Sonos group cleanup is pending'
    if streaming:
        return 'the audio stream is active'
    return None


def replace_atomic(path, data, *, mode, owner=None):
    """Write ``data`` to a temp file next to ``path`` and rename it over ``path``."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        if owner is not None and os.geteuid() == 0:
            os.chown(tmp, *owner)  # a root-written file must stay readable by the service user
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def systemctl(run, action, services):
    result = run(['systemctl', action, *services], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise SwapError(f'systemctl {action} failed')


def swap(config, new, *, services, reference=None, socket_path=DEFAULT_SOCKET,
         apply=False, force=False, run=subprocess.run, read_state=None,
         sleep=time.sleep, settle=5.0, out=print):
    config, new = Path(config), Path(new)
    if config.is_symlink():
        raise SwapError(f'{config.name}: refusing to replace a symlink')
    old_raw, _ = read_toml(config)
    new_raw, new_cfg = read_toml(new)
    if new_raw == old_raw:
        out('new configuration is identical to the current one; nothing to do')
        return 0

    if reference is not None:
        missing, conflicts, extra = compare(new_cfg, read_toml(reference)[1])
        for label, keys in (('missing (defaults apply)', missing),
                            ('not in reference', extra)):
            if keys:
                out(f'{label}: {", ".join(keys)}')
        if conflicts:
            raise SwapError(f'type differs from the reference: {", ".join(conflicts)}')

    reason = busy_reason(read_state or (lambda: player_state(socket_path)))
    if reason and not force:
        raise SwapError(f'refusing while the player is busy: {reason} (--force interrupts it)')

    units = ' '.join(services)
    backup = config.with_name(f'{config.name}.bak-{time.strftime("%Y%m%d-%H%M%S")}')
    if not apply:
        out(f'dry run: would stop {units}, keep {backup.name}, replace {config.name}, start {units}')
        return 0
    if backup.exists():
        raise SwapError(f'{backup.name} already exists; try again in a second')

    info = config.stat()
    mode, owner = stat.S_IMODE(info.st_mode), (info.st_uid, info.st_gid)
    replace_atomic(backup, old_raw, mode=0o600, owner=owner)
    try:
        systemctl(run, 'stop', services)
    except SwapError:
        backup.unlink(missing_ok=True)  # nothing was changed
        raise
    try:
        replace_atomic(config, new_raw, mode=mode, owner=owner)
        systemctl(run, 'start', services)
        sleep(settle)
        if run(['systemctl', 'is-active', *services], capture_output=True, text=True,
               timeout=30).returncode != 0:
            raise SwapError('service did not stay active')
    except (OSError, SwapError) as exc:
        out(f'failed ({exc}); restoring the previous configuration')
        try:
            replace_atomic(config, old_raw, mode=mode, owner=owner)
            run(['systemctl', 'reset-failed', *services], capture_output=True, text=True, timeout=30)
            systemctl(run, 'start', services)
        except (OSError, SwapError) as rollback:
            raise SwapError(f'rollback failed ({rollback}); previous configuration is in {backup.name}') from exc
        raise SwapError(f'{exc}; previous configuration restored') from exc
    out(f'configuration replaced, {units} running; previous version kept as {backup.name}')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', required=True, type=Path, help='production config to replace')
    parser.add_argument('--new', required=True, type=Path, help='new config file')
    parser.add_argument('--reference', type=Path,
                        help='reference TOML (for example config/player.example.toml) for a structure check')
    parser.add_argument('--service', action='append', dest='services',
                        help=f'systemd unit to stop and start, repeatable (default {DEFAULT_SERVICE})')
    parser.add_argument('--socket', default=DEFAULT_SOCKET, help='player control socket')
    parser.add_argument('--apply', action='store_true', help='really swap; without it only a dry run')
    parser.add_argument('--force', action='store_true',
                        help='swap even while the player is busy (interrupts playback)')
    args = parser.parse_args(argv)
    try:
        return swap(args.config, args.new, services=args.services or [DEFAULT_SERVICE],
                    reference=args.reference, socket_path=args.socket,
                    apply=args.apply, force=args.force)
    except SwapError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
