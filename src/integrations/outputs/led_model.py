"""M0.13a: pure, fail-safe LED view model (NO GPIO, network or Sonos calls).

Connection indicator never infers reachability from `owned`, `PLAYING` or a
cached target UID. `reachability` must be an *explicit, fresh observation*
from a future read-only discovery/health adapter. Until then UNKNOWN is shown.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class Pattern(str, Enum):
    OFF = 'off'
    STEADY = 'steady'
    BREATHE = 'breathe'
    BLINK = 'blink'  # legacy name, retained for compatibility
    SLOW_BLINK = 'slow_blink'
    FAST_BLINK = 'fast_blink'
    DOUBLE_FLASH = 'double_flash'
    TRIPLE_FLASH = 'triple_flash'


class Connection(str, Enum):
    UNKNOWN = 'unknown'
    UNSELECTED = 'unselected'
    CONNECTING = 'connecting'
    REACHABLE = 'reachable'
    UNREACHABLE = 'unreachable'
    UNSAFE_TOPOLOGY = 'unsafe_topology'
    OWNERSHIP_LOST = 'ownership_lost'




class PowerFault(str, Enum):
    DRIVE = 'drive'
    CRITICAL = 'critical'

@dataclass(frozen=True)
class Led:
    pattern: Pattern
    reason: str


@dataclass(frozen=True)
class LedState:
    power: Led
    connection: Led


def _value(value: object) -> str:
    return value.value if isinstance(value, Enum) else value if isinstance(value, str) else ''


def derive_led_state(
    status: Mapping[str, object],
    *,
    operating: bool = True,
    connection: Connection = Connection.UNKNOWN,
    power_fault: 'PowerFault' = None,
    shutting_down: bool = False,
) -> LedState:
    """Map schema-v1 status to LED semantics. Never mutate the input.

    `operating=False` means the output adapter is shutting down; a missing CD
    (NO_DISC) is still an *operational*, ready appliance.

    Connection is a separate externally established observation; `owned=True`
    is NOT proof of network reachability. No live checks happen here.
    """
    if not isinstance(status, Mapping) or status.get('schema_version') != 1:
        raise ValueError('expected structured status schema_version=1')
    player = status.get('player')
    if not isinstance(player, Mapping):
        raise ValueError('missing player status')
    mode = _value(player.get('mode'))
    if mode not in {'NO_DISC', 'STOPPED', 'STARTING', 'PLAYING', 'PAUSED', 'ERROR'}:
        raise ValueError('unknown player mode')
    if not isinstance(operating, bool):
        raise ValueError('operating must be boolean')
    if not isinstance(connection, Connection):
        raise ValueError('connection must be an explicit Connection observation')

    if power_fault is not None and not isinstance(power_fault, PowerFault):
        raise ValueError('power_fault must be an explicit PowerFault observation')
    if not isinstance(shutting_down, bool):
        raise ValueError('shutting_down must be boolean')
    if not operating:
        return LedState(Led(Pattern.OFF, 'device_off'), Led(Pattern.OFF, 'device_off'))

    if power_fault == PowerFault.CRITICAL:
        power = Led(Pattern.TRIPLE_FLASH, 'critical_device_fault')
    elif power_fault == PowerFault.DRIVE:
        power = Led(Pattern.DOUBLE_FLASH, 'drive_fault')
    elif mode == 'ERROR':
        power = Led(Pattern.BLINK, 'player_error_unclassified')
    elif shutting_down:
        power = Led(Pattern.SLOW_BLINK, 'shutting_down')
    elif mode == 'STARTING':
        power = Led(Pattern.BREATHE, 'starting')
    else:
        # PLAYING/PAUSED do not change operational power indication.
        power = Led(Pattern.STEADY, 'operational')

    connections = {
        Connection.UNKNOWN: Led(Pattern.OFF, 'reachability_unknown'),
        Connection.UNSELECTED: Led(Pattern.OFF, 'no_target_selected'),
        Connection.CONNECTING: Led(Pattern.BREATHE, 'connection_in_progress'),
        Connection.REACHABLE: Led(Pattern.STEADY, 'target_reachable'),
        Connection.UNREACHABLE: Led(Pattern.SLOW_BLINK, 'target_unreachable'),
        Connection.UNSAFE_TOPOLOGY: Led(Pattern.DOUBLE_FLASH, 'unsafe_sonos_topology'),
        Connection.OWNERSHIP_LOST: Led(Pattern.TRIPLE_FLASH, 'stream_ownership_lost'),
    }
    return LedState(power, connections[connection])
