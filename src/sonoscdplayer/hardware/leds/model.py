"""Pure, fail-safe LED view model (NO GPIO, network or Sonos calls).

The connection LED shows the Raspberry Pi's own network connection, not the state of any Sonos speaker: the player runs headless, and the
LED is how one sees that it is on the network. The observation comes from the
read-only NetworkManager status; Ethernet has precedence over WLAN.
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


class Network(str, Enum):
    UNKNOWN = 'unknown'            # status not readable (e.g. no NetworkManager)
    CONNECTED = 'connected'        # LAN or WLAN connected
    CONNECTING = 'connecting'      # a connection is being established
    DISCONNECTED = 'disconnected'  # no network connection
    HOTSPOT = 'hotspot'            # setup hotspot active


def classify_network(status) -> Network:
    """Reduce a NetworkStatus-like snapshot (available, preferred, ethernet, wifi)."""
    if not getattr(status, 'available', False):
        return Network.UNKNOWN
    if getattr(status, 'preferred', 'none') in ('ethernet', 'wifi'):
        return Network.CONNECTED
    if 'connecting' in (getattr(status, 'ethernet', None), getattr(status, 'wifi', None)):
        return Network.CONNECTING
    return Network.DISCONNECTED




class PowerFault(str, Enum):
    DRIVE = 'drive'          # optical drive missing (player status disc NO_DRIVE)

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
    network: Network = Network.UNKNOWN,
    power_fault: 'PowerFault' = None,
    shutting_down: bool = False,
) -> LedState:
    """Map schema-v1 status to LED semantics. Never mutate the input.

    `operating=False` means the output adapter is shutting down; a missing CD
    (NO_DISC) is still an *operational*, ready appliance.

    `network` is the separately observed network connection of the Pi; it is
    independent of the player mode and of any Sonos state. No live checks here.
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
    if not isinstance(network, Network):
        raise ValueError('network must be an explicit Network observation')

    if power_fault is not None and not isinstance(power_fault, PowerFault):
        raise ValueError('power_fault must be an explicit PowerFault observation')
    if not isinstance(shutting_down, bool):
        raise ValueError('shutting_down must be boolean')
    if not operating:
        return LedState(Led(Pattern.OFF, 'device_off'), Led(Pattern.OFF, 'device_off'))

    disc = status.get('disc')
    if power_fault is None and isinstance(disc, Mapping) and disc.get('mode') == 'NO_DRIVE':
        power_fault = PowerFault.DRIVE

    # shutting down wins (the player stops meanwhile), then the drive,
    # then player errors.
    if shutting_down:
        power = Led(Pattern.SLOW_BLINK, 'shutting_down')
    elif power_fault == PowerFault.DRIVE:
        power = Led(Pattern.DOUBLE_FLASH, 'drive_fault')
    elif mode == 'ERROR':
        power = Led(Pattern.FAST_BLINK, 'player_error_unclassified')
    elif mode == 'STARTING':
        power = Led(Pattern.BREATHE, 'starting')
    else:
        # PLAYING/PAUSED do not change operational power indication.
        power = Led(Pattern.STEADY, 'operational')

    connection = {
        Network.UNKNOWN: Led(Pattern.OFF, 'network_unknown'),
        Network.CONNECTED: Led(Pattern.STEADY, 'network_connected'),
        Network.CONNECTING: Led(Pattern.BREATHE, 'network_connecting'),
        Network.DISCONNECTED: Led(Pattern.SLOW_BLINK, 'network_disconnected'),
        Network.HOTSPOT: Led(Pattern.FAST_BLINK, 'setup_hotspot'),
    }[network]
    return LedState(power, connection)
