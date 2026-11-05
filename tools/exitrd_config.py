#!/usr/bin/env python3
"""Default-off experimental shutdown screen configuration and one-boot gate.

Never installs units, arms on boot, or initiates shutdown. Activation requires
explicit CLI tokens, config opt-in, exact executable identity, and SHA-256.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
from pathlib import Path
import subprocess
import sys

import exitrd_once

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path('/etc/sonoscdplayer/shutdown.ini')
TEMPLATE = ROOT / 'config/shutdown.ini'
EXPERIMENTAL = ROOT / 'build/exitrd-unverified-safe/shutdown'
RISK_ACK = 'I_ACCEPT_EARLY_SAFE_SCREEN_RISK'
REQUIRED_CHECK = (
    'safe-approved=0', 'diagnostic=0',
    'unverified-safe=1', 'delay-seconds=3',
)


class ConfigError(Exception):
    pass


def read_enabled(path: Path = CONFIG) -> bool:
    if not path.exists() and not path.is_symlink():
        # No local override is equivalent to the shipped disabled default.
        return False
    if path.is_symlink() or not path.is_file():
        raise ConfigError('invalid or symlinked shutdown config')
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with path.open(encoding='utf-8') as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error, UnicodeError) as exc:
        raise ConfigError(f'invalid shutdown config: {exc}') from exc
    if set(parser.defaults()):
        raise ConfigError('DEFAULT section is not allowed')
    if set(parser.sections()) != {'shutdown'}:
        raise ConfigError('config must contain only [shutdown]')
    if set(parser['shutdown']) != {'experimental_safe_screen'}:
        raise ConfigError('unrecognized/missing shutdown option')
    value = parser['shutdown']['experimental_safe_screen'].strip().lower()
    if value not in ('true', 'false'):
        raise ConfigError('experimental_safe_screen must be true or false')
    return value == 'true'


def verify_experimental_binary(path: Path = EXPERIMENTAL) -> None:
    if path.is_symlink() or not path.is_file():
        raise ConfigError('experimental binary missing or symlinked; build it first')
    result = subprocess.run([str(path), '--check'], check=True,
                            capture_output=True, text=True, timeout=10)
    words = result.stdout.split()
    if any(words.count(item) != 1 for item in REQUIRED_CHECK):
        raise ConfigError('binary check does not identify the experimental 3-second mode')
    if 'exitrd:' not in words or 'offline' not in words:
        raise ConfigError('unexpected binary check output')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--activate-once', action='store_true')
    actions.add_argument('--deactivate', action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--acknowledge', default='')
    parser.add_argument('--acknowledge-risk', default='')
    args = parser.parse_args(argv)
    try:
        if args.deactivate:
            # Rollback is available even if the config is disabled or corrupted.
            exitrd_once.disarm()
            return 0
        enabled = read_enabled()
        print('experimental_safe_screen=' + ('true' if enabled else 'false'))
        print('WARNING: experimental graphic precedes poweroff; wait for ACT LED completion')
        if not args.activate_once:
            print('READ-ONLY: no exitrd staged; no shutdown triggered')
            return 0
        if not enabled:
            raise ConfigError('experimental screen is disabled by configuration')
        if args.acknowledge_risk != RISK_ACK:
            raise ConfigError('explicit --acknowledge-risk token required')
        if args.acknowledge != exitrd_once.ACK:
            raise ConfigError('explicit --acknowledge token required')
        if not args.expected_sha256:
            raise ConfigError('--expected-sha256 is mandatory')
        verify_experimental_binary()
        payload, digest, preserved = exitrd_once.precheck(
            EXPERIMENTAL, args.expected_sha256, activating=True)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ConfigError('binary checksum changed after precheck')
        print(f'CHECK: verified experimental SHA-256 {digest}')
        exitrd_once.arm_once(payload, digest, preserved)
        return 0
    except (ConfigError, exitrd_once.PreflightError, OSError,
            subprocess.SubprocessError) as exc:
        print(f'exitrd-config: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
