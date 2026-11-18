# Configuration files and location

All SonosCDPlayer services and tools read their configuration from **one standard directory**:

```text
/etc/sonoscdplayer/
├── player.toml      # player, streaming, optical drive, LEDs, display pins, service behaviour
├── interface.toml   # buttons, rotary encoder, shift registers, input behaviour
└── ui.toml          # display language and UI preferences (optional)
```

Nothing in the repository is read as a live configuration. The files in [`config/`](../config/) (`*.example.toml`) are templates only.

The environment variable `SONOSCDPLAYER_CONFIG_DIR` overrides the directory. It is intended for tests and development checkouts; a normal installation does not need it.

## Creating or migrating the configuration

[`tools/install_config.py`](../tools/install_config.py) creates missing files at the standard location. It never overwrites existing files and runs as a dry run unless `--apply` is given.

```sh
# Fresh installation: start from the repository templates
python3 tools/install_config.py --service-user <account>
sudo python3 tools/install_config.py --service-user <account> --apply

# Migration: copy existing files (player.toml, interface.toml, ui.toml) from an older location
python3 tools/install_config.py --from-dir <old directory> --service-user <account>
sudo python3 tools/install_config.py --from-dir <old directory> --service-user <account> --apply
```

For each file the tool uses the file of the same name in `--from-dir` if present (copied byte for byte), otherwise the template. Invalid TOML is refused before anything is written. Source files are never modified or deleted: remove old copies yourself once the services work with the new location.

`<account>` is the account that runs the services. The directory is created as `root:<account's group>` with mode `0750` and the files with mode `0640`, so the services can read but not change their configuration. Edit the files with `sudo`, or use [`tools/config_swap.py`](CONFIG_UPDATE.md) for a checked replacement of `player.toml`.

## Who reads what

| Component | Reads |
| --- | --- |
| Player service and player tools | `player.toml` |
| LED service, early boot power LED | `player.toml` (`[led_outputs]`) |
| OLED service, splash and shutdown-screen builds | `player.toml` (`[display]`), `ui.toml` |
| Button/encoder service | `interface.toml`; `player.toml` (`[gpio]`) |
| Experimental shutdown screen installer | `player.toml` (`[service]`) |
| `tools/config_swap.py` | `player.toml` (default `--config`) |

`[gpio] warnings` in `player.toml` (default `false`) switches the RPi.GPIO library warnings on for all services that use GPIO pins; warnings such as "This channel is already in use" are expected during the hand-over from the early boot LED and are only useful for diagnostics.

Every tool still accepts an explicit path (`--config`, `--display-config`, `--ui-config`, `DISPLAY_CONFIG`) for testing, but installed services use the standard location.

If a required file is missing, the service stops with an error naming the expected path. Exceptions:

- `ui.toml` is optional; without it the built-in UI defaults apply. Besides the language and cover mode it holds `[ui.text_input] extra_characters`, the characters offered in addition to ASCII when typing a name on the device (default `äöüÄÖÜß`, see [Wi-Fi setup](NETWORK_SETUP.md#entering-on-the-device)).
- The early boot power LED falls back to BCM12 so that boot is never blocked.
- The OLED renderer without `--execute` (offline preview) uses the default display pins.

## Remembered state

The configuration files are only read, never written by the services. What is chosen during operation is remembered separately in `/var/lib/sonoscdplayer/` (created by systemd through `StateDirectory=` in the player and OLED units):

| File | Content |
| --- | --- |
| `target.json` | The last confirmed speaker selection (coordinator and group members), written by the player |
| `ui.json` | The display language chosen in the menu, written by the OLED service; it takes precedence over `ui.toml` and is also read by the shutdown screens |
| `metadata.sqlite3` | Album and track names found for each CD (CD-TEXT, MusicBrainz), written by the player |
| `covers/` | One cached cover image per album, if covers are enabled (`[ui.cover] mode`); at most `max_dimension_px` (default 512), JPEG quality `jpeg_quality` (default 85), together at most `budget_mb` (default 100 MB). When the budget is full, stored covers are kept and new ones are not stored |

After a restart the player restores this selection without contacting any speaker and never starts playback on its own. The display menu asks the player for it and shows the remembered speakers as checked. PLAY checks the speakers as usual; if one is not available, an error is shown and the selection is kept. Choosing other speakers replaces the file; deleting it forgets the selection. Outside systemd the directory can be set with `player_service.py --state-dir`.

A CD's names are looked up once and then kept. To look them up again (e.g. after a correction on MusicBrainz), choose **Settings → Reset CD entry** on the device with the CD inserted; this forgets the stored names of this CD only, also a manually chosen release, and searches again. The same is available as `player_remote.py reset-disc`.

## Playing on

On PLAY the player appends all tracks of the CD as one block at the end of the Sonos queue and starts the selected track. Sonos then plays on by itself to the end of the CD, and the Sonos app shows the whole track list: next, previous or tapping a track in the app works, and the player and the display follow. The NEXT and PREVIOUS buttons move within the same block. After STOP, at the end of the CD or when the player shuts down, the block is removed again, entry by entry and only where an entry is still the player's own; the rest of the queue is never touched. If music other than the CD is started from the app, the player shows an error and leaves the speaker alone, as before. After such a player error the display shows *PLAY: retry · STOP: OK*: PLAY clears the error and starts again, STOP only clears it. Both work only once nothing of the player's own playback is left on the speaker; otherwise the error stays until that is resolved. If Sonos has not started playing 30 seconds after PLAY, the player gives up the start, stops its stream server and removes its block from the queue, so this error can be cleared the same way.

While the current track plays, the stream server already reads the next track into the RAM buffer, so the following track starts without a gap caused by the drive. The buffer is set in `player.toml` and must be at least 16 MiB:

```toml
[streaming]
max_buffer_mib = 16   # 16 to 512
```

While shuffle or repeat is on in the Sonos app, or with `use_queue = false` under `[sonos]`, the player sets each track directly instead. Then the player itself plays the next track when a track has played to its end; after the last track it stops. A STOP from the Sonos app is not mistaken for the end of a track: the player continues only if the last position it saw was within a few seconds of the end. Switch this off in `player.toml`:

```toml
[playback]
continue = false
```

## CD information on Sonos

When the player knows the CD (CD-TEXT or MusicBrainz), Sonos shows the track title, the artist, the album and the cover instead of "SonosCDPlayer Track 3". The album name gets a prefix so the CD stands out in the Sonos app; set it in `player.toml`:

```toml
[sonos]
album_prefix = "CD: "   # empty string: no prefix
```

Names that arrive while a track is already playing appear from the next track on; a running track is never restarted for them. The Sonos app shows artist and album only for music played from the Sonos queue (see *Playing on*). While shuffle or repeat is on, or with `use_queue = false` under `[sonos]`, the stream is set directly and the app shows the title only. The Sonos app does not show the cover for this stream, even though the speaker receives its address. The cover is offered to Sonos by the player's stream server. With `[ui.cover] mode = "auto"` covers are downloaded because Sonos shows them; `"off"` switches covers off everywhere.

## Boot setup

Before Linux starts, the Raspberry Pi firmware sets three pins and can draw the start screen on the OLED. Both are configured in `/boot/firmware/config.txt` by

```sh
sh tools/install_screens.sh            # --dry-run shows the planned changes, including a diff of config.txt
```

| config.txt line | From `player.toml` | Purpose |
| --- | --- | --- |
| `gpio=<power_pin>=op,dh` | `[led_outputs] power_pin` | Power LED on from the start |
| `gpio=<connection_pin>=op,dl` | `[led_outputs] connection_pin` | Connection LED off |
| `gpio=<rst_pin>=op,dh` | `[display] rst_pin` | OLED RESET released, so the boot splash can draw |
| `splash_screen=ssd1327.bin` | – | Firmware boot splash (start screen) |

The lines are kept in one marked block at the end of `config.txt` (under `[all]`), and a backup is written to `config.txt.sonoscdplayer.bak`. Identical lines set by hand elsewhere are moved into the block. If one of these pins or `splash_screen` is set differently, the script stops without changing anything. After changing the pins in `player.toml`, run the script again; changes take effect at the next start.

The boot splash is built from `src/data/start_screen.png` with the localized text and assembled with `splash_assembler.py` from the Raspberry Pi [utils](https://github.com/raspberrypi/utils) (`splashasm` folder). The script looks for it in `~/src/utils/splashasm/` and `~/utils/splashasm/`; set `SPLASH_ASSEMBLER=/path/to/splash_assembler.py` otherwise. The firmware splash requires the beta bootloader (`raspi-config nonint do_beta_mode B1`, done by `scripts/setup.sh`).

## systemd units

The unit files in [`systemd/`](../systemd/) are templates. Render them with [`tools/render_runtime_units.py`](../tools/render_runtime_units.py); the rendered units pass the standard configuration paths explicitly:

```sh
python3 tools/render_runtime_units.py --output-dir /tmp/sonoscdplayer-units \
    --checkout <checkout>/src --python-venv <venv>/bin/python --service-user <account>
```

By default the rendered units enable the POWER button actions (and render the matching polkit rule), idle-drive wake on button activity, the CONNECT gestures and the Wi-Fi setup hotspot (with its own polkit rule, see [Wi-Fi setup](NETWORK_SETUP.md)). Use `--no-power-actions`, `--no-drive-activity-events`, `--no-connect-gestures` or `--no-network-setup` to switch an option off. The tool only writes the output directory; review the files before installing them.
