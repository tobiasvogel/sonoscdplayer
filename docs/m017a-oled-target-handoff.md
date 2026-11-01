# M0.17a — optional OLED to player target handoff

The existing player `/run/sonoscdplayer/control.sock` accepts
`set-target`, `get-target`, and `clear-target` in the M0.17a branch.
The OLED now has an **explicit opt-in** `--ui-target-rpc` flag. Without
the flag, the M0.16b checkbox menu remains local-only.

On review confirmation, the UI calls `select_one()`, requiring one
confirmed solo speaker and healthy topology. A daemon worker sends one
`set-target` request to the existing player socket; the UI continues
drawing while waiting. It shows translated pending/success/failure notices.
Failures are never retried or queued. No target UID is displayed or logged.

The player independently checks live solo-coordinator status, STOPPED
transport, and unowned player state. Target selection does not start music
or authorize subsequent playback automatically.

**Do not activate on the production Pi yet:** both OLED and player must run
the compatible M0.17a versions, and the player service must not be restarted
during active playback. The current M0.16b systemd drop-ins intentionally
contain no `--ui-target-rpc`. Buttons stay without `--execute`.

## Offline checks

```bash
cd ~/src/sonoscdplayer
git switch feature/m017a-single-target-selection
git pull --ff-only
cd src
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_oled_handoff.py' -v
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

A later controlled hardware experiment will need a reversible systemd
override and a safe idle window. No such override is installed by this step.
