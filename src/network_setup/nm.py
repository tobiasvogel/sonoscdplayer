"""Thin, bounded nmcli wrapper for the WLAN setup.

Only these NetworkManager operations are used: list devices, scan WLANs,
create/remove the temporary hotspot profile, and add/activate/remove WLAN
profiles created by this player (name prefix ``sonoscdplayer-wifi``). Other
profiles are never modified. Secrets are passed to nmcli but never logged or
included in error messages.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import subprocess

NMCLI = '/usr/bin/nmcli'
HOTSPOT_PROFILE = 'sonoscdplayer-setup'
WIFI_PROFILE_PREFIX = 'sonoscdplayer-wifi-'
NEW_PRIORITY = 100     # newest WLAN set up here wins ...
OLDER_PRIORITY = 90    # ... over earlier ones; other profiles keep theirs (default 0)
CONNECT_WAIT_SECONDS = 30
MAX_NETWORKS = 30


class NmError(RuntimeError):
    """nmcli failed; the message never contains a password."""


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int
    secure: bool


def split_terse(line):
    """Split one `nmcli --terse --escape yes` line at unescaped colons."""
    fields, current, escaped = [], '', False
    for char in line:
        if escaped:
            current += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(current)
            current = ''
        else:
            current += char
    fields.append(current)
    return fields


def valid_ssid(ssid):
    return isinstance(ssid, str) and 1 <= len(ssid.encode('utf-8')) <= 32 and ssid.isprintable()


def valid_password(password):
    """WPA2 passphrase (8-63 printable ASCII) or empty for an open network."""
    if password == '':
        return True
    return (isinstance(password, str) and 8 <= len(password) <= 63
            and all(32 <= ord(c) < 127 for c in password))


def profile_name(ssid):
    return WIFI_PROFILE_PREFIX + hashlib.sha256(ssid.encode('utf-8')).hexdigest()[:8]


class Nm:
    def __init__(self, runner=subprocess.run, timeout=15):
        self._run_command = runner
        self._timeout = timeout

    def _run(self, *args, timeout=None, check=True):
        try:
            result = self._run_command(
                [NMCLI, *args], capture_output=True, text=True, check=False,
                timeout=timeout or self._timeout, env={**os.environ, 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NmError(f'nmcli {args[0]} failed: {type(exc).__name__}') from None
        if check and result.returncode != 0:
            raise NmError(f'nmcli {args[0]} {args[1] if len(args) > 1 else ""} '
                          f'failed with exit code {result.returncode}')
        return result

    def wifi_device(self):
        """Name of the first WLAN device, or None (no WLAN hardware)."""
        out = self._run('--terse', '--escape', 'yes', '--fields', 'DEVICE,TYPE',
                        'device', 'status').stdout
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) == 2 and fields[1] == 'wifi' and fields[0]:
                return fields[0]
        return None

    def hardware_suffix(self, device):
        """Last 4 hex digits of the WLAN MAC address (stable per device, D-184)."""
        out = self._run('--get-values', 'GENERAL.HWADDR', 'device', 'show', device).stdout
        digits = ''.join(c for c in out.strip() if c in '0123456789abcdefABCDEF')
        return digits[-4:].upper() if len(digits) >= 4 else '0000'

    def scan(self, device):
        """Visible WLANs, strongest first; best effort (empty on failure)."""
        try:
            out = self._run('--terse', '--escape', 'yes', '--fields', 'SSID,SIGNAL,SECURITY',
                            'device', 'wifi', 'list', 'ifname', device, '--rescan', 'yes',
                            timeout=30).stdout
        except NmError:
            return []
        best = {}
        for line in out.splitlines():
            fields = split_terse(line)
            if len(fields) != 3 or not valid_ssid(fields[0]):
                continue
            try:
                signal = max(0, min(100, int(fields[1])))
            except ValueError:
                signal = 0
            secure = fields[2].strip() not in ('', '--')
            if fields[0] not in best or best[fields[0]].signal < signal:
                best[fields[0]] = WifiNetwork(fields[0], signal, secure)
        networks = sorted(best.values(), key=lambda n: (-n.signal, n.ssid.lower()))
        return networks[:MAX_NETWORKS]

    def start_hotspot(self, device, ssid, password):
        self.stop_hotspot()
        self._run('device', 'wifi', 'hotspot', 'ifname', device, 'con-name', HOTSPOT_PROFILE,
                  'ssid', ssid, 'password', password, timeout=30)
        # Never come back by itself after a restart.
        self._run('connection', 'modify', HOTSPOT_PROFILE, 'connection.autoconnect', 'no',
                  check=False)

    def hotspot_address(self, device):
        """IPv4 address of the hotspot (NetworkManager shared mode), e.g. 10.42.0.1."""
        out = self._run('--get-values', 'IP4.ADDRESS', 'device', 'show', device).stdout
        for line in out.splitlines():
            address = line.strip().split('/')[0]
            if address.count('.') == 3:
                return address
        return '10.42.0.1'

    def stop_hotspot(self):
        """Remove the temporary hotspot profile (and with it its password)."""
        self._run('connection', 'down', 'id', HOTSPOT_PROFILE, check=False)
        self._run('connection', 'delete', 'id', HOTSPOT_PROFILE, check=False)

    def connect(self, device, ssid, password):
        """Add (or replace) the player's profile for ssid, prefer it, activate it."""
        if not valid_ssid(ssid) or not valid_password(password):
            raise NmError('invalid network name or password')
        name = profile_name(ssid)
        self._run('connection', 'delete', 'id', name, check=False)
        args = ['connection', 'add', 'type', 'wifi', 'ifname', device, 'con-name', name,
                'ssid', ssid, 'connection.autoconnect', 'yes',
                'connection.autoconnect-priority', str(NEW_PRIORITY)]
        if password:
            args += ['wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
        self._run(*args)
        result = self._run('--wait', str(CONNECT_WAIT_SECONDS), 'connection', 'up', 'id', name,
                           timeout=CONNECT_WAIT_SECONDS + 10, check=False)
        if result.returncode != 0:
            self._run('connection', 'delete', 'id', name, check=False)
            raise NmError('could not connect to the network')
        self._demote_older(name)
        return name

    def _demote_older(self, keep):
        try:
            out = self._run('--terse', '--escape', 'yes', '--fields', 'NAME',
                            'connection', 'show').stdout
        except NmError:
            return
        for line in out.splitlines():
            name = split_terse(line)[0]
            if name.startswith(WIFI_PROFILE_PREFIX) and name != keep:
                self._run('connection', 'modify', 'id', name,
                          'connection.autoconnect-priority', str(OLDER_PRIORITY), check=False)

    def hotspot_active(self):
        """True while the setup hotspot profile is active (for the connection LED)."""
        out = self._run('--terse', '--escape', 'yes', '--fields', 'NAME',
                        'connection', 'show', '--active').stdout
        return any(split_terse(line)[0] == HOTSPOT_PROFILE for line in out.splitlines())
