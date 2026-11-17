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
| Breathing | The system is starting: from boot (early boot LED) until the player answers for the first time; also while playback is starting |
| On | Ready, also while playing or paused |
| Double flash | The CD drive is missing or not connected |
| Fast blinking | The player reports an error, or its status cannot be read (at start only after 2 minutes without an answer) |
| Slow blinking | The device is shutting down or restarting |
| Off | Shutdown finished: the device can be switched off |

Shutting down takes precedence over everything else. Errors stay visible for at least 3 seconds, even if they clear sooner.
