# M0.17a: temporary group controller integration (offline candidate)

## Implemented on feature branch

The existing player controller now accepts an **explicit** optional member list
through its Python method `play(uid, members=[...])`. It uses the staged
`sonos_group` engine and checks coordinator and each member as STOPPED solo
speakers before startup, then rechecks immediately before joining. Only the
coordinator gets the CD-stream `play_uri` request.

The existing read-only group membership and exact-owned-URI gates now cover
pause, resume, stop, refresh and shutdown. A track change retains the
previously verified group. Stop and natural end attempt to detach only the
members that this process joined, with topology and URI verification.
Uncertain cleanup is retained in memory and blocks further group playback
and reset; it is **not** blindly retried.

If `play_uri` fails after joining, the outcome may be ambiguous. The group
is deliberately preserved for manual recovery rather than issuing unsafe
unjoin commands.

## NOT activated

- Existing `player_service` `play --uid` and `playpause --uid` still call
  the unchanged single-UID form of the controller method; `members` are
  rejected on these RPCs.
- `set-target` can remember a read-only multiple-speaker intention, but
  **does not cause group join or start playback**.
- OLED group checkbox confirmation is not yet wired to the multi-member RPC.
- No changes to the existing systemd units, button execution or `main`.

## Pi offline acceptance

```bash
cd ~/src/sonoscdplayer
git switch feature/m017a-single-target-selection
git pull --ff-only
cd src
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_group_lifecycle.py' -v
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

The six new tests exercise explicit group formation, coordinator-only play,
owned stop and release, foreign topology, track switch and ambiguous startup
failure. The full suite must pass before connecting player RPC actions or
attempting the first hardware group test.

## Remaining review before activation

- Conservative manual recovery UX for uncertain release after a failed join,
  failed unjoin or ambiguous `play_uri`.
- Controlled explicit transport RPC requiring matching selection and fresh
  live ownership, without enabling generic GPIO `--execute`.
- Determine user-facing policy for release on stop, natural end, shutdown
  and pause; do not silently alter existing external groups.
- Test with two standalone, stopped Sonos speakers in a scheduled safe window.
