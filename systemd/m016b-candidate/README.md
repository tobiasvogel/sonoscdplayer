# M0.16b candidate systemd drop-ins (not automatically installed)

The repository's production systemd unit files are intentionally unchanged.
These examples activate the read-only modern status and local checkbox menu
using the existing OLED and GPIO owner processes.

## Prerequisites

- Worktree running the feature branch at `/home/pi/src/sonoscdplayer/src`.
- The Python interpreter named in **both** drop-ins exists and imports
  `soco`, `PIL`, `RPi.GPIO`, `spidev`.
- Tests for the current feature branch and manual OLED/encoder acceptance passed.
- No CD is playing during the service switch.
- Both services run under `User=pi`.

The candidate deliberately names the already tested
`/home/pi/sonoscdplayer-tests/.venv/bin/python`. Before final production,
replace both occurrences with a separately validated stable environment such as
`/home/pi/sonoscdplayer/.venv/bin/python`.

The OLED service creates a private `0700` runtime directory
`/run/sonoscdplayer-ui`. The OLED's Unix datagram event socket is `0600`.
The button service uses the same path and **does not** execute Sonos commands.

## Controlled installation (manual, not setup.sh)

Inspect existing overrides first:

```bash
systemctl cat sonoscdplayer-oled.service
systemctl cat sonoscdplayer-buttons.service
```

Do **not** overwrite existing drop-ins without reviewing them. Save copies
before installing any new files. With no conflicting overrides:

```bash
cd /home/pi/src/sonoscdplayer
sudo install -d /etc/systemd/system/sonoscdplayer-oled.service.d
sudo install -d /etc/systemd/system/sonoscdplayer-buttons.service.d
sudo install -m 0644 systemd/m016b-candidate/sonoscdplayer-oled.service.d/override.conf /etc/systemd/system/sonoscdplayer-oled.service.d/m016b-ui.conf
sudo install -m 0644 systemd/m016b-candidate/sonoscdplayer-buttons.service.d/override.conf /etc/systemd/system/sonoscdplayer-buttons.service.d/m016b-ui.conf
sudo systemctl daemon-reload
sudo systemctl stop sonoscdplayer-buttons.service sonoscdplayer-oled.service
sudo systemctl start sonoscdplayer-oled.service
sudo systemctl start sonoscdplayer-buttons.service
systemctl status --no-pager sonoscdplayer-oled.service sonoscdplayer-buttons.service
```

If either service fails, inspect:
`journalctl -u sonoscdplayer-oled.service -u sonoscdplayer-buttons.service -b --no-pager -n 100`.
The UI receiver starts after the initial player readiness wait; transient
early button events may be dropped, never replayed.

## Rollback

```bash
sudo systemctl stop sonoscdplayer-buttons.service sonoscdplayer-oled.service
sudo rm -f /etc/systemd/system/sonoscdplayer-buttons.service.d/m016b-ui.conf
sudo rm -f /etc/systemd/system/sonoscdplayer-oled.service.d/m016b-ui.conf
sudo systemctl daemon-reload
sudo systemctl start sonoscdplayer-oled.service
sudo systemctl start sonoscdplayer-buttons.service
```

Only remove the two `m016b-ui.conf` files that this procedure installed.
Never remove other site-specific systemd overrides.

## Notes

- The existing player service is never stopped, restarted, or reconfigured.
- Firmware boot splash remains the initial image; OLED handover uses `0xAF`.
- `--oled-recover-on-error` is limited to one reinitialization per process.
- Multi-checkbox selection remains local. No `join`, `unjoin`, or playback
  command is emitted by the UI.
- Reboot validation follows successful manual service validation, not before.
