"""Optional, capability-driven optical drive power management (M0.12b-r4).

All device commands run in the *service event-loop*, not from a background
thread. The service never ticks this manager while a CD reader is attached.
No audio reads and no privileged helper are started.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from drive_access import drive_lock
from drive_idle_policy import IdleDrivePolicy
from pathlib import Path
import re
import subprocess
import time
import tomllib

FIELD_RE = re.compile(r'^\s*([A-Z][A-Z0-9_]*)\s+([0-9]+)\s+\[cha:\s*([yn])', re.M)
ALLOWED_FIELDS = frozenset(('IACT', 'SZCT', 'IDLE_A', 'IDLE_B', 'IDLE_C',
                            'STANDBY_Y', 'STANDBY_Z', 'PM_BG'))
STRATEGIES = frozenset(('off', 'start', 'keepalive', 'power_condition', 'auto'))


def load_config(path):
    raw = tomllib.loads(Path(path).read_text(encoding='utf-8')).get('drive_power', {})
    if not isinstance(raw, dict):
        raise ValueError('drive_power must be a table')
    enabled = raw.get('enabled', True)
    strategy = raw.get('strategy', 'auto')
    interval = raw.get('keepalive_interval_seconds', 10)
    idle_seconds = raw.get('idle_spin_down_seconds', None)
    changes = raw.get('mode_page_overrides', {})
    if type(enabled) is not bool or strategy not in STRATEGIES:
        raise ValueError('invalid drive_power enabled/strategy')
    if type(interval) not in (int, float) or not 3 <= interval <= 3600:
        raise ValueError('keepalive interval must be 3..3600 seconds')
    if idle_seconds is not None:
        if type(idle_seconds) not in (int, float) or not 30 <= idle_seconds <= 86400:
            raise ValueError('idle_spin_down_seconds must be 30..86400 seconds')
        if not enabled or strategy not in ('keepalive', 'auto'):
            raise ValueError('idle spin-down requires enabled keepalive or auto strategy')
    if not isinstance(changes, dict) or any(k not in ALLOWED_FIELDS or type(v) is not int or v < 0 or v > 65535
                                             for k, v in changes.items()):
        raise ValueError('invalid or unsupported power-condition field override')
    if idle_seconds is not None and changes:
        raise ValueError('idle spin-down cannot combine with power-condition overrides')
    return {'enabled': enabled, 'strategy': strategy, 'interval': float(interval),
            'idle_spin_down_seconds': (float(idle_seconds) if idle_seconds is not None else None),
            'overrides': changes}


def parse_power_page(output):
    return {name: {'value': int(value), 'changeable': changeable == 'y'}
            for name, value, changeable in FIELD_RE.findall(output)
            if name in ALLOWED_FIELDS}


@dataclass
class DrivePowerManager:
    device: str
    config: dict
    runner: object = subprocess.run
    clock: object = time.monotonic
    active_strategy: str = 'off'
    supported: dict = field(default_factory=lambda: {
        'power_condition_read': False, 'power_condition_write': False,
        'start_stop_unit': False})
    last_error: str | None = None
    last_start: float | None = None
    original: dict = field(default_factory=dict)
    changed: dict = field(default_factory=dict)
    _started: bool = False
    idle_policy: IdleDrivePolicy | None = None
    power_condition_probe: str = 'not_checked'
    power_condition_fields: dict = field(default_factory=dict)
    power_condition_probe_error: str | None = None
    power_condition_probe_exit_code: int | None = None

    def _command(self, args):
        p = self.runner(args, capture_output=True, text=True, timeout=6, check=False)
        if p.returncode:
            raise RuntimeError(f'{args[0]} failed (exit {p.returncode}): {(p.stderr or "")[:160]}')
        return p.stdout

    def _start_motor(self):
        if self.runner is subprocess.run and not Path(self.device).exists():
            raise RuntimeError('optical drive device disappeared')
        self._command(['sg_start', '--start', self.device])
        self.supported['start_stop_unit'] = True
        self.last_start = self.clock()

    def _read_page(self):
        result = self.runner(['sdparm', '--page=0x1a', self.device],
                             capture_output=True, text=True, timeout=6, check=False)
        fields = parse_power_page(result.stdout or '')
        self.power_condition_probe_exit_code = result.returncode
        if not fields:
            raise RuntimeError(f'sdparm returned no recognised fields (exit {result.returncode}): {(result.stderr or "")[:160]}')
        self.power_condition_fields = {k: dict(v) for k, v in fields.items()}
        if result.returncode != 0:
            # Partial data are diagnostic, NOT proof that writes are safe.
            self.power_condition_probe = 'partial'
            self.power_condition_probe_error = f'sdparm returned exit {result.returncode} with readable fields'
            self.supported['power_condition_read'] = True
            self.supported['power_condition_write'] = False
            return {}  # Never write based on a nonzero probe.
        self.supported['power_condition_read'] = True
        self.supported['power_condition_write'] = any(v['changeable'] for v in fields.values())
        self.power_condition_probe = 'supported'
        self.power_condition_probe_error = None
        return fields

    def _apply_overrides(self, fields):
        wanted = self.config['overrides']
        if not wanted:
            return False  # no implicit firmware configuration guesses
        for key, value in wanted.items():
            if key not in fields or not fields[key]['changeable']:
                raise RuntimeError(f'{key} not changeable on this drive')
        self.original = {k: fields[k]['value'] for k in wanted}
        for key, value in wanted.items():
            if fields[key]['value'] == value:
                continue
            self._command(['sdparm', f'--set={key}={value}', '--page=0x1a', self.device])
            self.changed[key] = self.original[key]
        verified = self._read_page()
        if any(verified.get(k, {}).get('value') != v for k, v in wanted.items()):
            raise RuntimeError('mode page write verification failed')
        return True

    def start(self):
        """Called once while reader/server is idle. Never raises for unsupported hardware."""
        if self._started:
            return
        self._started = True
        idle_seconds = self.config.get('idle_spin_down_seconds')
        if idle_seconds is not None:
            # Start the inactivity clock at service startup, not at process epoch.
            self.idle_policy = IdleDrivePolicy(idle_seconds=idle_seconds,
                                               last_activity=self.clock())
        if not self.config['enabled'] or self.config['strategy'] == 'off':
            return
        strategy = self.config['strategy']
        page = {}
        # Probe for every enabled strategy, independently of strategy selection.
        # A successful motor START is not evidence of mode-page support.
        try:
            page = self._read_page()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.power_condition_probe = 'failed'
            self.power_condition_probe_error = f'{type(exc).__name__}: {exc}'
            self.last_error = f'power condition probe: {self.power_condition_probe_error}'
        if strategy in ('auto', 'power_condition') and page and self.config['overrides']:
            try:
                if self._apply_overrides(page):
                    self.active_strategy = 'power_condition'
                    return
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.last_error = f'power condition write: {type(exc).__name__}: {exc}'
        if strategy == 'power_condition':
            return  # explicit strategy never silently falls back
        if strategy in ('auto', 'start', 'keepalive'):
            try:
                self._start_motor()
                self.active_strategy = 'start' if strategy == 'start' else 'keepalive'
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.last_error = f'motor start: {type(exc).__name__}: {exc}'

    def rebind_device(self, device, *, reader_active=False):
        """Switch only after caller verified the same physical optical drive.

        Preserve the configured polling strategy and interval. No motor STOP.
        """
        if reader_active:
            raise RuntimeError('cannot rebind an active CD reader')
        if not re.fullmatch(r'/dev/sr[0-9]+', device):
            raise ValueError('invalid optical device path')
        if not Path(device).exists():
            raise RuntimeError('replacement optical device is unavailable')
        self.device = device
        # Re-arm a previously enabled keepalive immediately on the new path.
        # Existing interval and strategy are deliberately unchanged.
        if self.active_strategy == 'keepalive':
            self.last_start = self.clock() - self.config['interval']
        self.last_error = None

    def activity(self):
        """Record trusted physical activity; never treat keepalive as user input.

        Returns whether wake is needed. The eventual wake must be verified by
        the player service before an explicit PLAY can be forwarded.
        """
        if self.idle_policy is None:
            return False
        return self.idle_policy.activity(self.clock()) == 'wake'

    def tick(self, *, reader_active=False, playback_active=False,
             group_cleanup_pending=False):
        if reader_active or self.active_strategy != 'keepalive' or self.last_start is None:
            return
        policy = self.idle_policy
        now = self.clock()
        if policy is not None:
            action = policy.idle_action(
                now, playback_active=playback_active, reader_active=reader_active,
                group_cleanup_pending=group_cleanup_pending,
                drive_ready=self.supported['start_stop_unit'])
            if action == 'stop':
                try:
                    # Never send STOP while a CD reader holds the device lock.
                    with drive_lock(self.device, blocking=False) as acquired:
                        if not acquired:
                            # No STOP was sent: forget the attempt, retry on a later tick.
                            policy.stop_not_sent()
                            return
                        self._command(['sg_start', '--stop', self.device])
                        policy.stop_result(acknowledged=True)
                        self.last_error = None
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                    policy.stop_result(acknowledged=False)
                    self.last_error = f'idle spin-down: {type(exc).__name__}: {exc}'
                return
            if not policy.keepalive_allowed(now):
                return
        if now - self.last_start < self.config['interval']:
            return
        try:
            # Nonblocking lock; no overlapping cdparanoia reader.
            with drive_lock(self.device, blocking=False) as acquired:
                if not acquired:
                    return
                self._start_motor()
                self.last_error = None
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.last_error = f'keepalive: {type(exc).__name__}: {exc}'
            self.last_start = self.clock()

    def clear_fault_after_eject(self):
        """A verified EJECT removed the disc a failed wake could not verify."""
        if self.idle_policy is not None:
            self.idle_policy.reset_after_eject(self.clock())
            self.last_error = None

    def wake(self, *, verify):
        """Wake after a user event; no PLAY until the caller verifies the disc.

        Must run on the serialized player worker. Only a confirmed stop requires
        a wake; any failure retains the stopped/blocked state.
        """
        policy = self.idle_policy
        if policy is None or not policy.play_must_wait:
            return True
        if policy.wake_fault_latched:
            self.last_error = self.last_error or 'idle wake: previous failure; manual intervention required'
            return False
        if not callable(verify):
            raise ValueError('wake requires disc identity/TOC verification')
        try:
            with drive_lock(self.device, blocking=False) as acquired:
                if not acquired:
                    raise RuntimeError('drive busy; wake not attempted')
                self._start_motor()
            if verify(self.device) is not True:
                raise RuntimeError('drive/disc verification failed after wake')
            policy.wake_result(self.clock(), drive_verified=True)
            self.last_error = None
            return True
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            policy.wake_result(self.clock(), drive_verified=False)
            self.last_error = f'idle wake: {type(exc).__name__}: {exc}'
            return False

    def close(self):
        """Best effort restoration of only fields this process changed; never STOP motor."""
        for key, value in list(self.changed.items()):
            try:
                self._command(['sdparm', f'--set={key}={value}', '--page=0x1a', self.device])
                del self.changed[key]
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.last_error = f'power condition restore: {type(exc).__name__}: {exc}'
        self.active_strategy = 'off'

    def snapshot(self, *, reader_active):
        return {'enabled': self.config['enabled'],
                'requested_strategy': self.config['strategy'],
                'active_strategy': self.active_strategy, 'device': self.device,
                'reader_active': bool(reader_active), 'supported': dict(self.supported),
                'idle_spin_down_seconds': self.config.get('idle_spin_down_seconds'),
                'idle_spun_down': (self.idle_policy.spun_down if self.idle_policy else False),
                'idle_waking': (self.idle_policy.wake_in_progress if self.idle_policy else False),
                'idle_wake_fault_latched': (self.idle_policy.wake_fault_latched if self.idle_policy else False),
                'idle_wake_retry_in_seconds': (self.idle_policy.retry_state(self.clock())[0] if self.idle_policy else None),
                'idle_wake_retries_exhausted': (self.idle_policy.retry_state(self.clock())[1] if self.idle_policy else False),
                'idle_stop_attempted': (self.idle_policy.stop_attempted if self.idle_policy else False),
                'power_condition_probe': self.power_condition_probe,
                'power_condition_probe_error': self.power_condition_probe_error,
                'power_condition_probe_exit_code': self.power_condition_probe_exit_code,
                'power_condition_fields': {k: dict(v) for k, v in self.power_condition_fields.items()},
                'last_error': self.last_error}
