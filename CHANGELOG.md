# Changelog

All notable changes for users of SonosCDPlayer. The version number is in
[`VERSION`](VERSION); how a release is made is described in
[docs/RELEASE.md](docs/RELEASE.md).

## Unreleased

- CD-TEXT is read with a 45 s limit instead of 15 s; some slim drives need
  about 20 s on a CD with many tracks.
- Drive check: read speed WARN limit 1.25x (was 1.5x); the report names the
  USB adapter id as such. Panasonic/Matshita UJ265 and LG GTC0N added as
  reference drives.
- `tools/check_drive.py` checks whether a CD drive suits the player
  (read speed, spin-down and wake-up, eject, USB reconnect, CD-TEXT) and
  prints a report that can be shared; see `docs/DRIVE_CHECK.md`.
- Log lines are tagged by component (`[player]`, `[service]`, `[disc]`,
  `[drive-wake]`, …) instead of internal milestone names such as `[M0.11d]`.
- Tests run with pytest (`python3 -m pytest`, settings in `pytest.ini`).
- The control socket accepts connections as soon as it appears.
- Drivers, test-pattern diagnostics and renderers for the SH1107 OLED
  (SPI/I2C) and the ST7735S color LCD; not yet connected to the player.
- A request for another track right after PLAY (Sonos loads the first
  queue entry before it seeks to the selected track) no longer stops the
  drive while it spins up.
- The player shows its version in its status reply and logs it at start.
- Settings → Reset CD entry: forgets the inserted CD's stored names and looks
  them up again.
- Cover store limited to 100 MB by default; cover JPEG quality configurable
  in `ui.toml`.

## 0.9.0 – 2026-11-18

First numbered version. It describes the state that has been tested on the
device up to this date.

- Plays an audio CD on Sonos speakers in the local network, alone or as a
  group; speaker selection is kept across restarts.
- The whole CD is added as one block to the Sonos queue: the Sonos app
  shows all tracks, skipping in the app works and the player follows; the
  next track is read ahead into RAM. With shuffle/repeat or
  `use_queue = false` each track is set directly.
- Pause and resume continue from RAM where possible.
- Track titles, artists (per track on compilations), album and cover from
  CD-TEXT and MusicBrainz, cached locally.
- 128×128 OLED with menus for speakers, network, language and POWER;
  notices; optional cover. Status LEDs for power and network.
- WLAN setup through a hotspot or directly at the device (network list,
  password entry).
- Player errors can be cleared at the device: STOP clears, PLAY retries;
  a start that never begins playing is given up after 30 s.
- Ambient light sensor (APDS9306) for LED and OLED brightness; the sensor
  service reports missing or stale values instead of old ones.
- Configuration in `/etc/sonoscdplayer/`, systemd units rendered from the
  repository.
