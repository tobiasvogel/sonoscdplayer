# M0.16b UX and i18n acceptance candidate

This is an opt-in feature-branch implementation; do not merge into `main`
or enable Sonos commands as part of localization work.

## Input behavior

- Encoder press on status screen opens playback targets.
- Turn selects an individual speaker or a menu action.
- Encoder press on a speaker toggles its *local* checkbox.
- `Review selection` confirms locally only; no player or group commands.
- `Language` opens the de/en selector; turn and press select.
- `Back` cancels unconfirmed selection and returns to status.
- Connect is **not** used to open or close the menu; its future
  Sonos-connect function has not been implemented.

## Language preferences

Copy `config/ui.example.toml` to `/home/pi/sonoscdplayer/ui.toml`
only when configuration tests pass. The file is optional: defaults are
`language = "de"` and `cover.mode = "auto"`. A loaded unsupported
language falls back to English with a warning. A malformed TOML file or
invalid cover mode is rejected at startup. No Linux locale probing.

All UI messages added in this milestone use IDs from `src/sonos_ui/i18n/locales`.
Both catalogs must have identical keys and format placeholders.
Speaker and music names are user metadata and must not be translated.
The old standalone UI prototype and non-user-facing diagnostics retain
their existing behavior until separately migrated.

**Persistence safety:** the on-device Language picker is session-only.
It redraws the running UI without restarting the player. It deliberately
does not write `ui.toml`; atomic persistence is reserved for a future,
separately tested settings-writer implementation.

## Verify before hardware

```bash
cd /home/pi/src/sonoscdplayer
git pull --ff-only
cd src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_sonos_ui_i18n.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_sonos_ui_menu.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py' -v
```

Do not reboot or restart frontend services until these tests pass. After
manual acceptance, restart only the OLED and button services. The audio
player service must stay untouched.

## Manual OLED test

With the working m016b systemd drop-ins in place, both frontend services
should load the updated branch on the next controlled restart. Check encoder
press (open), turning (focus), press (toggle), Language (choose English),
Back (exit), and that Connect **does not** open the UI.

No systemd unit or persistent configuration file is modified automatically.
