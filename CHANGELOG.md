# Changelog

All notable changes for users of SonosCDPlayer. The version number is in
[`VERSION`](VERSION); how a release is made is described in
[docs/RELEASE.md](docs/RELEASE.md).

## Unreleased

- Fixes from a second code audit: the track server's log lines no longer
  pile up in memory during playback; a wrong password for an already saved
  WLAN no longer deletes the working profile; cancelling the WLAN setup in
  the seconds after the phone submitted no longer switches the network; an
  idle group is only split completely if the player remembers it as its own
  playback group (matching speaker names are no proof), otherwise only the
  chosen speakers leave it; the stored playback group is updated after a
  group recovery; a name majority without an album name takes no cover;
  commands that waited behind a slow PLAY are not run late once their
  sender gave up, and at most four may wait.
- Python packages are installed from `requirements.txt` with the tested
  version ranges (SoCo 0.32, Pillow 11).
- A CD is ready as soon as its table of contents is read; CD-TEXT follows in
  the background, and PLAY or EJECT no longer wait for it (they cancel the
  read). On a disc where cd-info hangs this saved 45 s at insertion and up to
  65 s at service start; such a disc is not tried again until it is
  re-inserted after a restart. The drive is detected with the cdparanoia
  TOC query instead of cd-info.
- Editions on MusicBrainz that differ: a name that more than half of them use
  (album, artist, each title) is taken; a field without a majority stays
  empty. The cover comes from an edition with the majority album name.
- CDs whose first track does not start at the very beginning of the disc
  (for example 33 sectors later) are no longer rejected as unreadable.
- A CD taken out with the drive's own eject button is noticed at once: during
  playback the player stops (and releases the group) instead of playing on
  from its buffer into an error; while the drive sleeps, the display shows
  the open tray and the new CD is loaded without a button press. Only the
  drive status is asked for this, which does not spin the drive up.
- A CD swapped with the drive's own eject button while the drive slept no
  longer ends in "drive error" at the next button press: the wake accepts
  the new disc and the player loads it (track 1, CD-TEXT, metadata).
- Stereo pairs and home-theatre sets (soundbar with Sub or surrounds) count
  as one speaker: their hidden bonded speakers no longer make them look
  grouped, which refused them for playback.
- A group left pending after STOP no longer blocks PLAY, EJECT and the
  drive's spin-down when it consists only of the player's own speakers and
  plays nothing: the player releases it right after STOP, and otherwise
  before PLAY or EJECT. Each group release is logged with its outcome.
- A track change of the disc that happens between two reads of the
  regular ownership check no longer ends playback with "exact URI
  mismatch"; the player follows the new track on the next check.
- CDs without CD-TEXT that MusicBrainz lists in several editions now get
  their titles when all editions name album, artist and tracks alike
  (spelling differences such as case or "…" are ignored); the cover comes
  from the first edition that has one. Editions that differ still show
  track numbers only.
- An unreachable or overloaded MusicBrainz is asked again after 2, 10 and
  30 minutes (before: once), and the log shows the reason (e.g. HTTP 503).
- STOP on a group (and the stop at shutdown) releases the group before the
  disc is removed from the Sonos queue. Before, the release could fail,
  and the remaining "group cleanup pending" blocked EJECT and PLAY.
- A speaker chosen on the CD player that sits in an idle Sonos group
  (stopped or paused) is taken out of it on its own; the rest of the group
  stays together. Groups that are playing are never touched. Idle speakers
  (stopped or paused) can be used for CD playback.
- Group changes made in the Sonos app during CD playback now also change
  the speaker selection, and the menu shows them.
- Pressing PLAY shows "Starting playback …" at once (while the drive spins
  up); if the player refuses, the display says why (speaker grouped,
  playing or missing, no speaker selected, CD not ready, drive error).
- The player remembers the speakers of the running playback group in its
  state directory (`playback_group.json`). After a restart or crash, a
  stopped leftover of that group, including speakers added in the Sonos
  app, is split at the next selection or PLAY. When the player leaves a
  group alone, the log says why.
- The menu no longer refuses grouped speakers itself; the player checks
  them live and reports the reason.
- The speakers can be changed in the menu during playback: adding or
  removing speakers keeps the music playing; choosing another main speaker
  restarts the current track there. The playing main speaker stays main
  speaker as long as it is still chosen.
- Changing the group in the Sonos app during CD playback no longer ends it
  with a player error: added or removed speakers become part of the
  playback, and if the app moves the playback to one of the group's
  speakers, the player follows. At the end every speaker of the group is
  released again.
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
