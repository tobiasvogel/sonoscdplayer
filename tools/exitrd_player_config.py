#!/usr/bin/env python3
"""Explicit, one-boot exitrd selection from existing SonosCDPlayer player TOML.

No automatic arming, no shutdown and no persistent systemd integration.
The config is a policy gate, never a substitute for operator acknowledgement.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import sys
import tomllib

import exitrd_once

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTAL = ROOT / "build/exitrd-unverified-safe/shutdown"
RISK_ACK = "I_ACCEPT_EARLY_SAFE_SCREEN_RISK"
EXPECTED_MODE = ("safe-approved=0", "diagnostic=0",
                 "unverified-safe=1", "delay-seconds=3")


class ConfigurationError(Exception):
    pass


def enabled_from_player_config(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        raise ConfigurationError("player TOML must be a regular non-symlink file")
    try:
        with path.open("rb") as stream:
            config = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot read player TOML: {exc}") from exc
    service = config.get("service", {})
    if not isinstance(service, dict):
        raise ConfigurationError("[service] must be a table")
    value = service.get("experimental_safe_screen", False)
    if not isinstance(value, bool):
        raise ConfigurationError("[service].experimental_safe_screen must be boolean")
    return value


def verify_binary() -> None:
    if EXPERIMENTAL.is_symlink() or not EXPERIMENTAL.is_file():
        raise ConfigurationError("experimental binary missing; build with risk acknowledgement")
    result = subprocess.run([str(EXPERIMENTAL), "--check"], text=True,
                            capture_output=True, check=True, timeout=10)
    tokens = result.stdout.split()
    if not all(tokens.count(item) == 1 for item in EXPECTED_MODE):
        raise ConfigurationError("wrong experimental exitrd binary mode")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="path to the actual player.toml; never inferred")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--activate-once", action="store_true")
    actions.add_argument("--deactivate", action="store_true")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--acknowledge", default="")
    parser.add_argument("--acknowledge-risk", default="")
    args = parser.parse_args(argv)
    try:
        if args.deactivate:
            # Rollback remains available even after config is disabled/corrupt.
            exitrd_once.disarm()
            return 0
        enabled = enabled_from_player_config(args.config)
        print(f"experimental_safe_screen={'true' if enabled else 'false'}")
        print("WARNING: OLED graphic precedes kernel poweroff; wait for completed ACT LED sequence")
        if not args.activate_once:
            print("READ-ONLY: no mount, activation or shutdown")
            return 0
        if not enabled:
            raise ConfigurationError("experimental screen disabled in player TOML")
        if args.acknowledge != exitrd_once.ACK or args.acknowledge_risk != RISK_ACK:
            raise ConfigurationError("both explicit shutdown and early-screen acknowledgements required")
        if not args.expected_sha256:
            raise ConfigurationError("--expected-sha256 is required")
        verify_binary()
        payload, digest, preserved = exitrd_once.precheck(
            EXPERIMENTAL, args.expected_sha256, activating=True)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ConfigurationError("post-precheck SHA mismatch")
        exitrd_once.arm_once(payload, digest, preserved)
        return 0
    except (ConfigurationError, exitrd_once.PreflightError, OSError,
            subprocess.SubprocessError) as exc:
        print(f"exitrd-player-config: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
