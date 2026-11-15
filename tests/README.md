# Tests

All automated tests live in this folder. They run offline: no GPIO, SPI, CD drive or Sonos speaker is touched.

Run the whole suite from the repository root:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

Run a single file:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_input_power.py'
```

The tests of the statically built shutdown hook are skipped until it has been built with `sh tools/build_final_shutdown_hook.sh`.

## Layout

Files are named after the part of the player they cover:

| Prefix | Covers |
|---|---|
| `test_streaming` | RAM ring buffer and HTTP track server |
| `test_player_*` | State machine, controller, control socket, Sonos playback, groups, target RPCs |
| `test_drive_*`, `test_disc_state` | Drive power, idle, wake-up, USB reconnect, disc detection |
| `test_input_*` | Buttons, encoder, safe transport buttons, volume, POWER, CONNECT |
| `test_led`, `test_ambient_brightness` | Status LEDs and ambient light |
| `test_oled_*` | SSD1327 driver and the OLED status service |
| `test_ui_*` | Menu, speaker selection, rendering, notices, languages, network status |
| `test_system_screens` | Localized start, shutdown and restart screens |
| `test_shutdown_*` | Shutdown handover, late shutdown hook, frame renderer, experimental exitrd |
| `test_install` | Config files, systemd units, no-local-paths guard |

New tests go into the file for the part they cover; add a new file only for a new part of the player.
