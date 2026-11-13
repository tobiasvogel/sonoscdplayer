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

- `ui.toml` is optional; without it the built-in UI defaults apply.
- The early boot power LED falls back to BCM12 so that boot is never blocked.
- The OLED renderer without `--execute` (offline preview) uses the default display pins.

## systemd units

The unit files in [`systemd/`](../systemd/) are templates. Render them with [`tools/render_runtime_units.py`](../tools/render_runtime_units.py); the rendered units pass the standard configuration paths explicitly:

```sh
python3 tools/render_runtime_units.py --output-dir /tmp/sonoscdplayer-units \
    --checkout <checkout>/src --python-venv <venv>/bin/python --service-user <account>
```

By default the rendered units enable the POWER button actions (and render the matching polkit rule), idle-drive wake on button activity and the CONNECT gestures. Use `--no-power-actions`, `--no-drive-activity-events` or `--no-connect-gestures` to switch an option off. The tool only writes the output directory; review the files before installing them.
