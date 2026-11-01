# M0.17a — safe single-target playback (incremental)

## Phase 1: isolated, read-only intent validation (implemented)

- Use the existing M0.16b speaker discovery and explicit local checkbox confirmation.
- Admit exactly one speaker that is a solo Sonos group member.
- Reject incomplete discovery, stale/missing target, unconfirmed changes and multiple targets.
- Return only an ephemeral `TargetIntent` (UID and display label), **not** ownership.
- No changes to GPIO, the player service, its socket protocol, systemd or Sonos devices.
- No automatic audio playback when changing a checkbox or confirming a target.

## Phase 2: explicit opt-in command boundary (not yet implemented)

- Confirm how the player selects/validates a target and when it accepts
  `play --uid` and `playpause --uid`.
- Decide how private target IDs are stored or carried without exposing them in
  process argv, logs, public state or world-readable configuration.
- Have the *player* revalidate live Sonos membership and ownership atomically
  at command execution. A UI discovery snapshot is never authorization.
- Route controls only through the existing player socket, never SoCo in OLED.
- Ensure no hidden side effect on menu open, speaker highlight, or checkbox.
- Keep `--execute` disabled until offline and hardware acceptance.

## Test

```bash
cd /home/pi/src/sonoscdplayer/src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_single_target.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```
