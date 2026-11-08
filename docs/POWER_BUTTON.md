# Power button

SonosCDPlayer supports a dedicated Power button for shutting down or restarting the device.

A short press can open a power menu on the display. Holding the button requests an orderly system shutdown directly, even if the display menu is unavailable.

Power actions are **optional** and must be explicitly enabled.

## Button behaviour

| Action | Behaviour |
| --- | --- |
| Short press | Opens the display's power menu when UI power actions are enabled |
| Short press while the menu is open | Closes the menu |
| Long press (3 seconds by default) | Requests a system shutdown, independently of the display |

The power menu offers **Back** (the default selection), **Power off**, and **Restart**. Turn the rotary encoder to navigate, then press it to confirm.

A long press does not also trigger a short press when the button is released.

## Configuring the long press

The hold duration is configured in the hardware interface TOML file:

```toml
[power]
long_press_ms = 3000
```

The supported range is **3000–10000 milliseconds** (3–10 seconds).

See [`config/interface.example.toml`](../config/interface.example.toml) for the reference hardware configuration.

## Enabling power actions

Power actions are opt-in. The relevant command-line options are:

| Component | Option |
| --- | --- |
| Hardware input service | `--power-actions` |
| Display service | `--ui-power-actions` |

The input service also needs its usual `--gpio --commands` configuration. The display requires its interactive mode (`--ui-modern`) and an active `--ui-events-socket` for receiving short presses.

Without `--power-actions`, holding the button only logs a preview message; it does not shut down the system. Without `--ui-power-actions`, a short press does not open an actionable power menu.

The repository contains systemd configurations and examples, including `systemd/m016b-release/`. Review these against the actual installation rather than treating them as universal drop-in instructions. Restart the relevant services after making configuration changes, while the player is idle.

## System permissions

The input and display services generally run without root privileges, so they need narrowly scoped permission to request shutdown or reboot.

SonosCDPlayer supplies a polkit rule:

[`systemd/polkit/50-sonoscdplayer-power.rules`](../systemd/polkit/50-sonoscdplayer-power.rules)

On a compatible system, install the rule with:

```sh
sudo install -m 0644 \
    systemd/polkit/50-sonoscdplayer-power.rules \
    /etc/polkit-1/rules.d/
```

**Important:** The supplied rule is restricted to the `pi` service user in the Raspberry Pi reference installation. If your services use another account, review and adapt the rule before installation.

The rule permits only the specified power-off and reboot actions; it does not grant general administrative privileges. To revoke the permission, remove the installed rule from `/etc/polkit-1/rules.d/`.

## Shutdown behaviour

Both the menu and the long-press path request an orderly shutdown through systemd. During shutdown, the player attempts to stop its CD stream and release its owned Sonos playback session.

The Power button **does not directly disconnect electrical power**. A long press requests shutdown; successful completion still depends on the operating system.

### Optional shutdown screen

An optional **"Safe to unplug"** OLED screen can be displayed near the end of the shutdown sequence. It is a separate feature and is not needed for the Power button itself.

See [Optional OLED shutdown screen](EXPERIMENTAL_SHUTDOWN.md) for its requirements, installation, and behaviour.

## Notes

- The long-press shutdown path does not depend on the display service.
- The implementation restricts power requests to the supported actions and guards against duplicate accepted requests within a process.
- Button mappings, service flags, and system permissions must match the actual installation.

Test the configured controls and display menu before relying on them for everyday operation.
