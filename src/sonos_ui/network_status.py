"""Bounded, read-only NetworkManager status for the INP-03c UI.

No connection activation, scanning, password access or configuration writes.
Only device TYPE and STATE are requested; device names, SSIDs, IPs and
connection profiles are neither retained nor emitted.
"""
from dataclasses import dataclass
import subprocess


@dataclass(frozen=True)
class NetworkStatus:
    ethernet: str = "unknown"
    wifi: str = "unknown"
    preferred: str = "none"
    available: bool = False


_STATES = {
    "connected": "connected",
    "connecting": "connecting",
    "disconnected": "disconnected",
    "unavailable": "unavailable",
    "unmanaged": "unavailable",
}
_TYPES = {"ethernet": "ethernet", "wifi": "wifi"}
_PRIORITY = {"unknown": 0, "unavailable": 1, "disconnected": 2,
             "connecting": 3, "connected": 4}


def parse_device_status(output):
    """Reduce nmcli terse device rows to coarse states without identifiers."""
    if not isinstance(output, str) or len(output) > 8192:
        return NetworkStatus()
    observed = {"ethernet": [], "wifi": []}
    for raw in output.splitlines():
        # Device names may contain escaped colons. Only TYPE/STATE are used.
        fields = []
        current = ""
        escaped = False
        for char in raw:
            if escaped:
                current += char
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == ":":
                fields.append(current)
                current = ""
            else:
                current += char
        fields.append(current)
        if len(fields) != 3:
            continue
        kind = _TYPES.get(fields[1].strip().lower())
        state = _STATES.get(fields[2].strip().lower())
        if kind is not None:
            observed[kind].append(state or "unknown")
    summary = {}
    for kind in ("ethernet", "wifi"):
        states = observed[kind]
        summary[kind] = max(states, key=_PRIORITY.get) if states else "unknown"
    preferred = ("ethernet" if summary["ethernet"] == "connected"
                 else "wifi" if summary["wifi"] == "connected"
                 else "none")
    return NetworkStatus(summary["ethernet"], summary["wifi"],
                         preferred, bool(observed["ethernet"] or observed["wifi"]))


def read_network_status(*, runner=subprocess.run, timeout=2.0):
    """Read-only bounded snapshot; never run a NetworkManager mutation."""
    command = ("/usr/bin/nmcli", "--terse", "--escape", "yes",
               "--fields", "DEVICE,TYPE,STATE", "device", "status")
    try:
        completed = runner(command, capture_output=True, text=True,
                           timeout=timeout, check=False)
        if completed.returncode != 0:
            return NetworkStatus()
        return parse_device_status(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return NetworkStatus()
