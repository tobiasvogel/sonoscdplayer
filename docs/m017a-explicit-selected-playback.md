# M0.17a — explicit selected-target playback RPC

## Architecture

The existing owner-only `control.sock` accepts:

1. `set-target` with one coordinator UID and optional `members` array.
   The player checks that each selected Sonos speaker is standalone and
   STOPPED, and stores intent in memory. **No Sonos group join or playback.**
2. `get-target` / `clear-target` as before; the response does not disclose UIDs.
3. NEW `play-selected` with **no UID or members fields**.
   Only this explicit command uses the stored coordinator and members.
   The player controller performs live checks again before group creation
   and streams only to the coordinator.

Unscoped legacy `play --uid` and `playpause --uid` remain solo; requests
carrying `members` with those commands are still rejected.

The OLED `--ui-target-rpc` opt-in now sends single or multiple selected
standalone speakers to `set-target` using the existing socket. Its checkbox
confirmation **never sends `play-selected`**. No additional file, Unix socket,
automatic playback, GPIO execution or group adoption was introduced.

## Deliberate activation boundary

The systemd files remain unchanged and no GPIO button sends
`play-selected`. The CLI `src/player_remote.py play-selected` is
available for a separately approved, controlled hardware test **only after**
compatible M0.17a player deployment and a confirmed stopped/standalone
selection. Do not run it as part of offline acceptance or while anybody
is using Sonos.

When `play_uri` outcome is uncertain, temporary members are retained for
manual recovery instead of an unsafe blind `unjoin()`. Continue reviewing
shutdown and recovery behavior before first live grouped playback.

## Pi offline tests

```bash
cd ~/src/sonoscdplayer
git switch feature/m017a-single-target-selection
git pull --ff-only
cd src
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_play_selected_rpc.py' -v
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

No production service restart or physical Sonos action is needed.
