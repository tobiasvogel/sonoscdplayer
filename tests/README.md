# Tests

All automated tests live in this folder. They run offline: no GPIO, SPI, CD drive or Sonos speaker is touched.

The tests use [pytest](https://docs.pytest.org/) (Debian/Raspberry Pi OS package `python3-pytest`, installed by `scripts/setup.sh`). The settings are in `pytest.ini` in the repository root, so no `PYTHONPATH` is needed.

Run the whole suite from the repository root:

```sh
python3 -m pytest
```

Run one file, or only tests whose name contains a word:

```sh
python3 -m pytest tests/test_input_power.py
python3 -m pytest -k eject
```

pytest hides what a test prints unless the test fails; add `-s` to see it, `-v` to list every test.

The tests of the statically built shutdown hook are skipped until it has been built with `sh tools/build_final_shutdown_hook.sh`.

## Layout

The folders mirror `src/sonoscdplayer/`:

| Folder | Covers |
|---|---|
| `audio/` | RAM ring buffer and the HTTP track server |
| `drive/` | Disc detection, drive lock, power, idle and wake-up, USB reconnect, eject, the drive check tool |
| `metadata/` | CD-TEXT, MusicBrainz, metadata cache and worker, covers, language rules |
| `sonos/` | Finding and checking speakers before and during playback |
| `player/` | State machine, controller, control socket, Sonos playback and queue, groups, speaker selection |
| `ui/` | Display service, menu, speaker selection screens, rendering, notices, languages, system screens, display profiles |
| `network_setup/` | WLAN setup through the hotspot and on the device |
| `hardware/displays/` | SSD1327, SH1107 and ST7735S controllers and their renderers |
| `hardware/inputs/` | Buttons, encoder, shift register, PLAY/PAUSE policy, volume, POWER, CONNECT |
| `hardware/leds/` | Status LEDs |
| `hardware/sensors/` | Ambient light sensor and its daemon (C test in `c/`) |
| `system/` | Installation and setup script, configuration files, systemd units, version, stack dump, shutdown screens and handover |
| `helpers/` | Fakes and sample data shared by several test folders (on the import path) |

New tests go into the file for the part they cover; add a new file only for a new part of the player.
