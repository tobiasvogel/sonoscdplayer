# M0.17a — target selection over the existing control socket

## Implemented

The player service now owns an **in-memory selected target** and exposes three
additional requests on its existing private Unix socket:

- `{"command":"set-target","uid":"<validated device UID>"}`
- `{"command":"get-target"}`
- `{"command":"clear-target"}`

The `get-target` reply contains only `target_selection.selected` and
`target_selection.ready_for_playback=false`. It deliberately does not
return the UID. The request `set-target` requires the player's mode to be
STOPPED and unowned and performs a live read-only check that the exact
device is a solo coordinator with Sonos transport STOPPED.

No Sonos play/pause/stop, join/unjoin or track action occurs when setting,
reading or clearing the target. The existing PLAY and PLAYPAUSE requests
**still require an explicit UID** and behave exactly as before. Selecting
a target does not authorize playback, nor is it persisted across restarts.

All mutations of the in-memory selection take place on the player service's
existing single serialized control worker. No new socket is created, and the
GPIO event socket stays unchanged.

## Important scope limit

The OLED menu is **not yet wired** to `set-target`. Linking the local
checkbox-confirmation to this RPC must be separately tested, with short,
nonblocking UI submission, failure feedback, and a strict single-target
policy. The buttons systemd service remains without `--execute`.

Do not restart the player just to exercise the new RPC while it owns music.
No UI confirmation, speaker highlight, systemd startup or reconnect should
cause automatic playback.

## Offline tests

```bash
cd /home/pi/src/sonoscdplayer
git switch feature/m017a-single-target-selection
git pull --ff-only
cd src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_target_rpc.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

The above verifies the RPC handler without starting the production player
service or contacting a real Sonos speaker.
