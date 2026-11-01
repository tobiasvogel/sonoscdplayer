# M0.17a — safe single-target playback (incremental)

## Phase 1: isolated, read-only intent validation (implemented)

- Use the existing M0.16b speaker discovery and explicit local checkbox confirmation.
- Admit exactly one speaker that is a solo Sonos group member.
- Reject incomplete discovery, stale/missing target, unconfirmed changes and multiple targets.
- Return only an ephemeral `TargetIntent` (UID and display label), **not** ownership.
- No changes to GPIO, the player service, its socket protocol, systemd or Sonos devices.
- No automatic audio playback when changing a checkbox or confirming a target.

## Phase 2: explicit opt-in player command boundary (staged for offline tests)

- Existing player RPC accepts UID-bound `playpause` and `play` only. The
  new `ExplicitTransport` accepts *only* `playpause`, is disabled by default,
  and checks fresh topology before sending a single RPC request.
- Existing player controller independently enforces live solo/ownership
  checks immediately before audio operations. UI checks alone are never authoritative.
- `TargetIntent` lives in process memory, not argv, a file or logs.
- The actual cross-process handoff from OLED to GPIO owner remains **pending**.
  Never publish UID through the current UI event socket or status snapshots.
- Have the *player* revalidate live Sonos membership and ownership atomically
  at command execution. A UI discovery snapshot is never authorization.
- Route controls only through the existing player socket, never SoCo in OLED.
- Ensure no hidden side effect on menu open, speaker highlight, or checkbox.
- Keep `--execute` disabled until offline and hardware acceptance.
- `next`, `previous`, and `stop` are deliberately rejected because their
  current player RPC variants are not UID-bound. They require a separately
  tested target-scoped authorization boundary before enabling.

## Current status

This is a security boundary module and offline test suite, **not yet wired**
into the OLED or GPIO processes. No production service is changed.

## Test

```bash
cd /home/pi/src/sonoscdplayer/src
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_single_target.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_player_boundary.py' -v
/home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```
