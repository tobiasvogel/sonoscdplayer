# Status LEDs

The player has two LEDs, driven by the LED service (`sonoscdplayer-led.service`). Their pins are set in `player.toml` under `[led_outputs]` (`power_pin`, `connection_pin`); the same pins are set early at boot by `tools/install_screens.sh` (see [Configuration](CONFIGURATION.md#boot-setup)). With an ambient light sensor, both LEDs follow the room brightness.

## Connection LED: network of the Raspberry Pi

The player runs without a screen of its own on the network, so the connection LED shows whether the **Raspberry Pi itself** is connected to the network. It does not show the state of any Sonos speaker.

| LED | Meaning |
| --- | --- |
| On | Connected (LAN or WLAN) |
| Breathing | A connection is being established |
| Slow blinking | No network connection |
| Fast blinking | Setup hotspot active (see [Wi-Fi setup](NETWORK_SETUP.md)) |
| Off | Network state cannot be read (for example, NetworkManager is not available), or the device is shutting down |

The state is read from NetworkManager every 5 seconds, read-only (`nmcli device status`, only device type and state; no names, addresses or passwords). LAN takes precedence over WLAN. For a test without changing the network, the LED service accepts `--network connected|connecting|disconnected|hotspot|unknown`; the default `auto` reads the real state.

## Power LED

| LED | Meaning |
| --- | --- |
| Breathing at boot | The system is starting (early boot LED, until the LED service takes over) |
| On | Ready, also while playing or paused |
| Breathing | Playback is starting |
| Blinking | The player reports an error, or its status cannot be read |
| Off | Device is shutting down |

Errors stay visible for at least 3 seconds, even if they clear sooner.
