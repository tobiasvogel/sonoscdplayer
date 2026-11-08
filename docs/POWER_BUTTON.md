# Power button

| Gesture | Result |
| --- | --- |
| Short press | Opens the power menu on the OLED: **Back** (default), **Power off**, **Restart**. Turn the encoder, press it to choose. A second short press also closes the menu. |
| Hold for 3 s or more | Powers the system off directly, with or without the menu and the OLED. |

The hold time is `[power] long_press_ms` in `interface.toml` (3000 to 10000, never shorter than 3 s).

## How it fits together

* The buttons process owns the GPIO. It tells a short press apart from a long
  one (`integrations/interfaces/power_gesture.py`), sends a short press to the
  OLED process over the existing UI event socket and handles a long press
  itself, so shutdown does not depend on the display.
* The OLED process shows the menu (`sonos_ui/interactive_menu.py`) and runs the
  chosen action on a worker thread.
* Both end in `src/power_action.py`, which runs exactly
  `/usr/bin/systemctl --no-ask-password poweroff|reboot`: no shell, no input
  in the command line, and only one request per process once it was accepted.
* systemd stops the units. The player service now handles `SIGTERM` like
  Ctrl+C, so a CD stream is stopped and Sonos is released through the normal
  `shutdown_owned` path before the system goes down.

## Privilege

The services run as `pi`. A polkit rule grants that user exactly four
`org.freedesktop.login1` actions (power off and reboot, with and without other
sessions) and nothing else:

```sh
sudo install -m 0644 systemd/polkit/50-sonoscdplayer-power.rules /etc/polkit-1/rules.d/
sudo systemctl daemon-reload   # not needed for polkit, harmless
```

Check it as the service user: `sudo -u pi systemctl --no-ask-password reboot --dry-run`
should not ask for authentication (`--dry-run` shows what would happen without doing it).
Delete the rule file to revoke the permission.

## Enabling

Both switches are opt-in, like the other command paths:

* buttons: `interface_input … --gpio --commands --power-actions`
* OLED: `oled_status … --ui-modern --ui-events-socket … --ui-power-actions`

Without `--power-actions` a long press only logs "preview only"; without
`--ui-power-actions` the short press does nothing. The files in
`systemd/m016b-release/` already contain both flags. Install the polkit rule
first, then restart the buttons and OLED units.

The experimental OLED shutdown screen (`docs/EXPERIMENTAL_SHUTDOWN.md`) is
independent and keeps working, because the system goes down through the normal
systemd shutdown.
