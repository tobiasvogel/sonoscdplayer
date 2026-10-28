"""Optional, capability-driven optical drive power management (M0.12b-r4).

All device commands run in the *service event-loop*, not from a background
thread. The service never ticks this manager while a CD reader is attached.
No audio reads and no privileged helper are started.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from drive_access import drive_lock
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
    changes = raw.get('mode_page_overrides', {})
    if type(enabled) is not bool or strategy not in STRATEGIES:
        raise ValueError('invalid drive_power enabled/strategy')
    if type(interval) not in (int, float) or not 3 <= interval <= 3600:
        raise ValueError('keepalive interval must be 3..3600 seconds')
    if not isinstance(changes, dict) or any(k not in ALLOWED_FIELDS or type(v) is not int or v < 0 or v > 65535
                                             for k, v in changes.items()):
        raise ValueError('invalid or unsupported power-condition field override')
    return {'enabled': enabled, 'strategy': strategy, 'interval': float(interval),
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

    def tick(self, *, reader_active=False):
        if reader_active or self.active_strategy != 'keepalive' or self.last_start is None:
            return
        if self.clock() - self.last_start < self.config['interval']:
            return
        try:
            # Nonblocking: never delay or overlap an active cdparanoia reader.
            with drive_lock(self.device, blocking=False) as acquired:
                if not acquired:
                    return
                self._start_motor()
                self.last_error = None
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.last_error = f'keepalive: {type(exc).__name__}: {exc}'
            self.last_start = self.clock()

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
                'power_condition_probe': self.power_condition_probe,
                'power_condition_probe_error': self.power_condition_probe_error,
                'power_condition_probe_exit_code': self.power_condition_probe_exit_code,
                'power_condition_fields': {k: dict(v) for k, v in self.power_condition_fields.items()},
                'last_error': self.last_error}
