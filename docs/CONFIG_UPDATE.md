# Updating the player configuration

SonosCDPlayer uses TOML files for configuration. The `tools/config_swap.py` utility helps replace an existing **player** configuration while retaining a backup of the previous version.

It provides a dry run, basic validation, a controlled service restart, and a rollback attempt if the restarted service fails its initial check.

## Before you begin

Prepare a new TOML file based on your existing player configuration, preserving any local and hardware-specific settings.

The repository includes [`config/player.example.toml`](../config/player.example.toml) as a reference. **Do not overwrite your existing configuration with the example file:** your installation may use different settings or defaults.

Run the following commands from the repository root, replacing the example paths with the actual paths used by your installation.

## 1. Check the new configuration

```sh
python3 tools/config_swap.py \
    --config /path/to/current/player.toml \
    --new /path/to/new/player.toml \
    --reference config/player.example.toml
```

Without `--apply`, the tool performs a dry run: it checks the files and the player's reported state and describes the planned operation **without modifying the configuration or restarting services**.

## 2. Apply the update

Once you have reviewed the dry-run result and confirmed that the player is idle:

```sh
sudo python3 tools/config_swap.py \
    --config /path/to/current/player.toml \
    --new /path/to/new/player.toml \
    --reference config/player.example.toml \
    --apply
```

The tool normally refuses to proceed while the player is active, owns a Sonos session, has pending group cleanup, or reports an active stream.

By default, only `sonoscdplayer-player.service` is restarted. Use the repeatable `--service` option when additional systemd services need to be included.

## What the tool does

When applying a change, the utility:

1. Checks for valid TOML and, if `--reference` is supplied, compares the types of known keys with the reference.
2. Checks the current player state and normally refuses to interrupt playback or an owned Sonos session.
3. Creates a timestamped backup of the old configuration alongside the original file.
4. Stops the selected service, replaces the configuration atomically, and starts the service.
5. Attempts to restore the previous configuration and restart the service if the initial startup check fails.

Backups use a `.bak-<timestamp>` suffix and restricted file permissions. The replacement preserves the previous configuration's ownership and file mode.

## Important limitations

- **Validation is not exhaustive.** Valid TOML and matching key types do not guarantee valid values or hardware compatibility.
- **The tool does not merge settings.** Missing and unknown keys are reported with `--reference`, but you remain responsible for reviewing changes.
- **An initial successful service check is not a full functional test.** Test the affected features after updating.
- **Only selected services are restarted.** Other services may retain their previous configuration.
- **An unavailable player control socket is not proof that all player processes have stopped.** Check the actual services before applying an update.
- **Rollback is best-effort.** If recovery fails, inspect the service status and use the preserved backup.

The optional `--force` flag bypasses the normal busy-state check and **may interrupt playback**. Avoid it for routine updates.

## Related configuration files

- [`config/player.example.toml`](../config/player.example.toml) — Player configuration reference.
- [`config/interface.example.toml`](../config/interface.example.toml) — Hardware input configuration.
- [`config/ui.example.toml`](../config/ui.example.toml) — Display preferences.

This utility is specifically intended for the player configuration. Other settings and services may require separate update procedures.
