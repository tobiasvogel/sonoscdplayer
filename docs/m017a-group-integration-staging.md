# M0.17a — temporary group integration staging

## Adopted from the two external patches

- `src/sonos_group.py` contains the opt-in SoCo join/unjoin engine,
  exact-membership checks, STOPPED/solo gates, and rollback bookkeeping.
- `src/sonos_ui/selection_plan.py` derives deterministic coordinator/member
  intent from checkbox choices. No selection file or extra socket is created.
- `player_target_selection.SelectedTarget` stores the coordinator plus
  members in process memory. `set-target` can accept a validated `members`
  array through the **existing** `control.sock`.
- All members are rechecked live, individually STOPPED and solo, before their
  *selection* is accepted. The selection path invokes NO Sonos writes.
- `get-target` deliberately does not return UIDs.
- `tests/test_m017a_group_staging.py` covers intent, preflight, grouped
  speakers, unbound PLAY requests, release failures and foreign group changes.

## Deliberately not wired yet

- The existing OLED menu still uses the M0.17a single-target confirmation.
  The multi-target planner is available but *not yet connected*.
- `player_controller` still enforces solo-only playback ownership. It
  does not call the new grouping engine during PLAY or STOP.
- No change to button execution, player RPC PLAY semantics, systemd, main,
  or physical Sonos devices. The original single-target tests remain valid.
- Group formation/cleanup requires a separate explicit user playback action,
  exact live topology verification, careful recovery for partial rollback,
  and matching group ownership checks across every controller state transition.

## Why not copy the original patch directly?

The original temporary-group patch assumes joins can always be rolled back and
may forget unresolved joined speakers. The candidate records unresolved
members. It also must be reconciled with the existing player-owned target
selection (rather than the second patch's selection file). Automatic group
dissolution on failure/shutdown needs careful opt-in policy review.

## Offline verification (Raspberry Pi)

```bash
cd ~/src/sonoscdplayer
git switch feature/m017a-single-target-selection
git pull --ff-only
cd src
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_m017a_group_staging.py' -v
~/sonoscdplayer/.venv/bin/python -m unittest discover -s ../tests -p 'test_*.py'
```

Do not enable `--execute` or restart production services for this staging step.
