# Changelog

All notable changes for users of SonosCDPlayer. The version number is in
[`VERSION`](VERSION); how a release is made is described in
[docs/RELEASE.md](docs/RELEASE.md).

## Unreleased

- Playback no longer breaks off at a track change when Sonos asks for the
  next track late: a read-ahead that gave up is read again instead of being
  answered with 409 (Sonos skipped the track).
- When cdparanoia ends without delivering any audio, the track server tries
  again after a short pause and logs cdparanoia's own messages.
- After playback on a group ends, the group is released before the disc is
  removed from the Sonos queue, and a member that Sonos reports late is no
  longer kept in the group.
- A stopped group left behind that consists only of the player's own selected
  speakers is split automatically when speakers are selected or PLAY is
  pressed; other groups are still left alone.
- When the player refuses a speaker selection, the display says why (grouped,
  playing, not found) and the reason is logged; the display service logs a
  status line only when the screen kind or mode changes.
- Developer documentation: [architecture overview](docs/ARCHITECTURE.md),
  [adding a display](docs/ADDING_A_DISPLAY.md) and
  [adding buttons and input modules](docs/ADDING_INPUTS.md).
- Code reference: every public module, class and function is documented;
  `tools/build_api_docs.sh` builds HTML with pdoc.
- The controller's interactive console mode (`player_controller.py` run
  directly) is removed; the player is controlled through the service.
- Unused USB-reconnect modules (`reconnect_observer`, `reconnect_policy`,
  `reconnect_coordinator`) removed; reconnects are handled by
  `drive/usb_identity.py` and `drive/disc_monitor.py` as before.
- The former one-shot test programs `playback_start`, `play_track` and
  `switch_track` are gone; what the player used from them now lives in
  `sonos/speaker_checks.py`, `audio/track_server_process.py`,
  `drive/drive_probe.py` and `player/state.py`, with clearer names and
  English messages.
- Code reorganized into one package, `src/sonoscdplayer/`, by area (audio,
  drive, metadata, sonos, player, ui, hardware, network_setup, system), with
  self-explanatory file names; C code in `src/native/`, images and fonts in
  `src/sonoscdplayer/assets/`; tests in `tests/<area>/`. Services start with
  `python3 -m sonoscdplayer.…`: **render and install the systemd units
  again after updating** (see docs/RELEASE.md).
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
