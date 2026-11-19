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

Files are named after the part of the player they cover:

| Prefix | Covers |
|---|---|
| `test_streaming` | RAM ring buffer and HTTP track server |
| `test_disc_*`, `test_metadata_*` | CD-TEXT, MusicBrainz, metadata cache, covers, language rules |
| `test_player_*` | State machine, controller, control socket, Sonos playback, groups, target RPCs |
| `test_drive_*`, `test_disc_state` | Drive power, idle, wake-up, USB reconnect, disc detection, the drive check tool |
| `test_input_*` | Buttons, encoder, safe transport buttons, volume, POWER, CONNECT |
| `test_led`, `test_ambient_brightness` | Status LEDs and ambient light |
| `test_oled_*` | SSD1327 driver and the OLED status service |
| `test_sh1107*`, `test_st7735s*`, `test_display_*` | Further display controllers, their renderers and display profiles |
| `test_ui_*` | Menu, speaker selection, rendering, notices, languages, network status |
| `test_system_screens` | Localized start, shutdown and restart screens |
| `test_shutdown_*` | Shutdown handover, late shutdown hook, frame renderer, experimental exitrd |
| `test_install` | Config files, systemd units, no-local-paths guard |

New tests go into the file for the part they cover; add a new file only for a new part of the player.
