# M0.16b configuration layout migration

The repository contains versioned templates:

- `config/player.example.toml`: exact copy of legacy `src/config.toml`
- `config/interface.example.toml`: exact copy of legacy `src/integrations/interfaces/interface.toml`
- `config/ui.example.toml`: default language and cover mode

Installed configuration (not committed to git):

- `/home/pi/sonoscdplayer/player.toml`
- `/home/pi/sonoscdplayer/interface.toml`
- `/home/pi/sonoscdplayer/ui.toml`

The legacy source TOMLs remain temporarily in place so a rollback to
existing services does not change player or GPIO behavior. Remove legacy
copies only after boot and full hardware acceptance. Do not commit local
configuration or device-specific private data.

## Step 1: copy, never overwrite

From the repository root:

```bash
python3 tools/migrate_ui_config.py
python3 tools/migrate_ui_config.py --apply
ls -l /home/pi/sonoscdplayer/{player,interface,ui}.toml
cmp src/config.toml /home/pi/sonoscdplayer/player.toml
cmp src/integrations/interfaces/interface.toml /home/pi/sonoscdplayer/interface.toml
```

The migration tool copies existing legacy files **byte-for-byte** and uses
examples only when the originals are missing. It never replaces already
installed TOML files. Review `ui.toml` before activating.

## Step 2: regression tests

```bash
cd /home/pi/src/sonoscdplayer/src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_config_migration.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

## Step 3: controlled systemd cutover

The release candidate drop-ins are located in `systemd/m016b-release/`.
They refer to persistent TOMLs via explicit flags:

- buttons: `--config /home/pi/sonoscdplayer/interface.toml`
- OLED: `--ui-config /home/pi/sonoscdplayer/ui.toml`
- LED: `--config /home/pi/sonoscdplayer/player.toml`
- player: `--config /home/pi/sonoscdplayer/player.toml`

Do NOT activate all four services automatically. Back up existing
`/etc/systemd/system/*service.d/` drop-ins first. Start with OLED/buttons
in idle mode, then LED. Defer the player service change to an explicit idle
maintenance window with a separately approved stop/start; do not interrupt
a CD, restart to test ownership, or change playback behavior.

Compare `systemctl cat` before/after. Roll back by restoring the original
drop-ins, daemon-reloading, and restarting *only* the affected frontend
services. If the player override was activated, undo it only during a safe
maintenance window; never force an active playback session to stop.

The original `systemd/*.service` files and service arguments remain
unchanged. Neither the migration helper nor a git pull changes running
services. The only persistent writes performed by the helper are new,
non-overwriting per-device TOMLs under the destination directory.
