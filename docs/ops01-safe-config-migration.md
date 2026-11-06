# OPS-01 — Safe configuration migration (design gate)

Status: initial design and acceptance criteria; **no production migration or cutover**.

## Boundary

- Development checkout: `/home/pi/src/sonoscdplayer`.
- Production installation: `/home/pi/sonoscdplayer`.
- Actual production player config (systemd-verified): `/home/pi/sonoscdplayer/player.toml`.
- Do **not** assume these locations are symlinks or that `src/config.toml` is
  the live config. Determine paths from `systemctl show ... -p ExecStart`.
- A deployment must never copy the repository's example config over a local
  config, nor modify a private UID file, GPIO pin settings, socket permissions
  or production service overrides implicitly.
- Do not commit any local configuration, UID or private operational tracker to Git.

## Planned migration behavior

1. **Read-only inventory:** inspect effective service ExecStart, resolved
   working directories and config paths; list presence/permissions without
   reading or logging UID secrets. No service restart, motor action, Sonos RPC
   or file modifications.
2. **Parse and validate:** read old and new TOML with `tomllib`; reject
   malformed tables, invalid values and incompatible/unknown settings rather
   than silently discarding them.
3. **Deterministic preview:** show only sanitized structural changes:
   new keys with proposed defaults, existing key preservation, changed types,
   deprecated keys, and a clear block/warning for security-sensitive settings.
   Never expose tokens, Sonos identifiers or arbitrary config values.
4. **Explicit opt-in apply:** only during a separately approved maintenance
   window. Precondition: no active CD reader, owned stream, group recovery or
   conflicting player process. Backup original config to a private,
   timestamped file with restrictive permissions; write a validated candidate
   to a new temporary file in the same directory and atomically replace only
   the explicitly selected target.
5. **No implicit activation:** a newly introduced optional feature such as
   `drive_power.idle_spin_down_seconds` must remain disabled unless the
   operator knowingly opts in. Likewise no implicit `--execute` or
   `--drive-activity-events` on the button service.
6. **Post-apply verification and rollback:** compare checksums, reparse file,
   validate service arguments and expose a reversible restore path. Never
   auto-start services or send motor/Sonos actions.

## Required tests before an implementation is usable

- Read-only dry-run does not touch the filesystem or leak config values.
- Existing keys (including nested tables, `drive_power`, GPIO, locale and
  application-specific extras) survive without loss.
- New optional safety features default to off and are not added as active
  settings without consent.
- Malformed/unknown or conflicting configuration fails closed.
- Missing config, symlinks, permission errors, atomic-write failure and
  rollback failure are handled safely.
- Backup permissions are owner-only; no secrets in logs or Git.
- Dry-run works without GPIO, CD drive, SoCo, systemd or other hardware.

## M0.19 cross-check

The offline M0.19 tests were reported green (292 total, 2026-11-04).
Spin-down STOP was acknowledged on the isolated player, but the subsequent
START coincided with USB over-current/disconnect and failed. M0.19 hardware
acceptance remains **blocked** pending the externally powered USB hub.
Neither this document nor OPS-01 authorizes a production configuration edit.


## Implemented read-only preview (first slice)

The tool `src/tools/config_dry_run.py` compares the current configuration's
TOML **structure and types**, not its values. It does not write backups or
apply migrations; it does not restart services or contact the CD drive or Sonos.
Its `--service` option reads the effective systemd ExecStart with
`systemctl show` and resolves the absolute `--config` value.

From the **development** checkout on the Raspberry Pi:

```sh
cd ~/src/sonoscdplayer
python3 src/tools/config_dry_run.py \
  --service sonoscdplayer-player.service \
  --template src/config.toml
```

Alternatively provide explicit files:

```sh
python3 src/tools/config_dry_run.py \
  --current /path/to/local.toml \
  --template src/config.toml
```

Only names of missing reference keys, type conflict categories and counts of
unknown local keys are printed. No existing values or private paths are
printed. Exit codes: **0** structurally compatible, **1** manual review
required (unknown keys/type conflicts), **2** inspection error. Existing
values are **not** validated against current runtime semantics; a compatible
report is not permission to apply or restart services.

```sh
PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_ops01_*.py' -v
```

The historical production directory has its own configuration; never copy
the reference configuration over that file. The apply/rollback phase
remains **unimplemented**.


## Read-only migration proposal (second slice)

Use the new `--plan` switch to inspect **migration eligibility** without
writing anything:

```sh
cd ~/src/sonoscdplayer
python3 src/tools/config_dry_run.py \
  --service sonoscdplayer-player.service \
  --template src/config.toml \
  --plan
```

The planner has a deliberately narrow allowlist of **candidate proposals**:
`audio_startup.pcm_timeout_seconds = 45` and
`led_outputs.ambient.timeout_ms = 150`, only if the template value exactly
matches the audited value and the key is missing. A candidate is **never
applied automatically**, does not authorize deployment and requires separate
review. Existing settings are never changed.

Any missing key under `service`, `drive_power`, GPIO, button and encoder
settings is blocked as protected, even if its template value would otherwise
look harmless. Thus `service.experimental_safe_screen` and
`drive_power.idle_spin_down_seconds` cannot be implicitly enabled.
Other unknown missing keys are counted without disclosure. Conflicting
types require review; arbitrary user key names and all values remain redacted.
The output contains only approved candidate names and aggregate counts.

Exit status: **0** no differences requiring a decision; **1** proposed
candidates or other required review; **2** file or inspection failure. The
tool does not compare existing values, perform deep semantic validation,
generate rewritten TOML, modify files, or restart services.

```sh
PYTHONPATH=src python3 -m unittest discover \
  -s tests -p 'test_ops01_*.py' -v
```

**Reference discrepancy resolved during review:** The current
`main` reference defines `service.experimental_safe_screen = false`, which
explains the observed missing protected key in production. The original
OPS-01 branch predates the experimental shutdown feature. The option must
remain protected and must never be silently injected, enabled or disabled by
the migration planner. Review coverage includes both missing disabled
defaults and preservation of any existing local choice.

**Review integration:** The reviewed OPS-01 files have been carried onto a
fresh branch from the current `main`. This avoids merging the former
OPS-01 branch's 172-commit divergence. M0.19 remains independently gated.
No apply or rollback implementation is included.

## OPS-01 completion workstream (2026-11-05)

The read-only inspector and planner from PR #8 are an accepted first slice,
**not** a complete migration. Development on
`feature/ops01-safe-config-apply` must preserve the following gates.

### Phase 1: offline apply/rollback implementation

- Separate `--plan` from `--apply` and `--rollback`. Never infer permission to
  write from a structurally compatible report. Existing read-only commands
  must retain their behavior and exit codes.
- No generic merge of template TOML into production. Only audited, inert,
  allowlisted new keys may be proposed; protected subtrees (`service`,
  `drive_power`, GPIO, buttons, encoder, and identity/UID) are never
  changed by the automated writer.
- Before any writes, construct a proposed candidate and validate its parsed
  contents against the original plus the individually authorized additions.
  Preserve every existing value, table, and non-allowlisted extra key.
  Reject unsupported syntax/ambiguous placement, invalid TOML, or unexpected
  candidate differences; never silently rewrite the entire local file.
- Resolve the effective target from systemd, inspect path and parent directory
  without following symlinks, and reject nonregular files, permissions errors,
  unexpected owner/mode, and hard-link ambiguities. The exact expected
  original checksum is an apply precondition to prevent stale plans.
- Require distinct, explicit operator consent for the exact candidate and
  target. A CLI flag alone is not permission to stop services, send Sonos
  commands, or apply any unrelated settings.
- Before applying, verify the player is idle (`STOPPED`, no active reader or
  stream, no Sonos ownership or pending group recovery); reject stale,
  unknown, or unavailable state. Detect concurrent modifications by verifying
  original identity/checksum immediately before replacement.
- Backup the unmodified source with owner-only permissions, sync the backup,
  write a private temporary candidate in the same directory, reparse it,
  atomically replace the target, and sync the parent directory. On failures
  fail closed; if restoration is needed, use a separately validated backup
  path and atomic restore. Never restart services automatically.
- Explicit rollback requires backup identity/integrity and an expected
  checksum for the current target; reject any other drift. Keep immutable
  backup provenance and do not print contents, credentials or UIDs.
- Never apply or rollback against the live
  `/home/pi/sonoscdplayer/player.toml` without a separate approved
  maintenance window and a dry-run + offline test record.

### Phase 2: required verification

- Test happy-path apply and rollback solely in temporary directories with
  synthetic TOML, including nested keys and unrelated local extras.
- Test stale checksum, symbolic/hard links, permissions, nonregular files,
  malformed TOML, protected keys, changed template defaults, and injected
  failure before and after backup/replace. Verify no accidental write and
  no secret exposure, including error messages.
- Test explicit gates against absent/active/unknown player status and pending
  group recovery; exercise crash-safe rerun/rollback semantics.
- Run `PYTHONPATH=src /home/pi/sonoscdplayer/.venv/bin/python -m unittest discover -s tests`
  on the Pi without changing production services. Review the diff and
  release documentation before a separately approved live rehearsal.

The existing production `m016b-config.conf` and `m019-venv.conf` overrides
are not managed by OPS-01. No button `--execute`, OLED RPC, drive-power
change, or shutdown-hook setting is implied by completion of these phases.

### Security-review checkpoint (2026-11-06)

The first sandbox-only implementation has been exercised on the Raspberry Pi:
**503/503 offline tests passed** at commit `3eff89c`, including actual child
`SIGKILL` at the atomic-replace boundary and inspection from a newly started
Python process that inherits an already-open disposable directory descriptor.

Additional hardening in the same feature branch is under test:
- Independent recovery inspection rejects artifacts with group/other file
  permissions, including the backup, intent journal and replacement.
- A completed sandbox rollback retains its journal and rejects another apply;
  the transaction is deliberately **one-shot**.
- No production path is accepted by the sandbox apply/rollback interface.

**Unresolved production blockers:** Existing checksum checks do not eliminate
the time-of-check/time-of-use gap between the final pathname inspection and
`os.replace`. A production implementation needs exclusive, independently
verified coordination with all potential writers, safe target-directory
ownership and reopening, durable lifecycle/provenance rules, failure-injection
around `fsync`, and a fail-closed live player/reader/ownership preflight.
The current offline journal is not authenticated against an attacker who can
write its private directory. A subprocess inheriting a directory FD is not a
full reboot/reopen test. No production `--apply` or `--rollback` is exposed.

The safety-review test count must be updated from actual Pi results before
claiming this checkpoint complete. No status change to OPS-01 until the
production acceptance criteria and separately approved rehearsal pass.

### Fail-closed idle evidence (2026-11-06)

The player service's public `status` reply is **cached**, and its documented
snapshot reports `player.mode`, `sonos.owned`,
`sonos.group_cleanup_pending`, and `stream.active`. It does **not**
independently confirm reader activity, server/process state, ongoing commands,
or every remaining group membership. A `STOPPED` status is therefore
**insufficient** to authorize configuration replacement.

`src/tools/config_idle_evidence.py` is a pure offline diagnostic assessment
with additional hypothetical controller-level evidence. It returns
`write_authorized=false` unconditionally, even when all supplied values are
consistent with idle. Live freshness, exclusive coordination, safe filesystem
path handling and actual maintenance authorization remain separately required.
Do not wire this diagnostic result directly to a writer.

The OPS-01 implementation remains sandbox-only and the pull request remains
Draft. The most recent Pi-wide verified test result before this addition was
**507/507**, at commit `259e99e`; newer tests require their own Pi run.

### Cooperative sandbox writer lock (2026-11-06)

A stable, private `ops01-writer.lock` is now held using a nonblocking POSIX
`flock` during sandbox seed/apply/rollback. The lock file is never unlinked
and must remain an owner-owned regular file with mode `0600` and one link.
A second cooperating process must fail closed while the lock is held.

This is an **advisory lock only**. External tools that edit `player.toml`
without acquiring the same lock can still race between the pathname identity
check and `os.replace`. It does not remove that TOCTOU problem or prove a
safe production writer. The lock accepts an already-open sandbox directory
descriptor only and does not enable production apply.

The five new lock tests require confirmation on the Raspberry Pi. The last
confirmed full regression before this change was **515/515** at `46b8136`.

### Maintenance-window evidence policy (2026-11-06)

At commit `94e7139`, the Raspberry Pi passed **520/520** regression tests,
including cooperative sandbox advisory-lock tests.

The new pure function `assess_maintenance_evidence` models claims that an
operator has approved a particular maintenance window, the player service,
listener, reader and player process are inactive, group recovery is clear,
private paths and a cooperative writer lock are verified, and both the
original and candidate SHA-256 values match the planned transaction.
Missing, inconsistent or ambiguous claims fail closed.

**It does not perform a live preflight.** All evidence is supplied by the
caller and is not independently attested, cannot prove other writers are
excluded, and cannot establish freshness or guard the file-swap boundary.
Accordingly `write_authorized` is **always false**, including when every
claim is consistent. The function is not wired to the sandbox writer or any
real config path. Tests for this addition must pass before it can be counted
as an accepted OPS-01 substep.

Before allowing production apply we still need a separately approved
maintenance protocol, an exclusive cross-service coordination mechanism,
robust path/permission rules and a transactionally safe race strategy,
plus explicitly scoped failure tests. A successful offline gate is not
authorization to stop the player or migrate `player.toml`.

### Code-review checkpoint after 527/527 Pi regression (2026-11-06)

**Verified baseline:** `539be09`, 527/527 tests, Raspberry Pi. All new
work is on `feature/ops01-safe-config-apply`; no production file has been
migrated, and the pull request remains Draft.

**Review findings; do not treat sandbox tests as production acceptance:**

1. **P0 — No production cutover protocol.** The current maintenance and idle
   assessors only inspect caller-provided/cached evidence and always return
   `write_authorized=false`. There is no verified lease coordinating the
   player, socket command processing, CD reader and all configuration writers.
   A `STOPPED` snapshot cannot grant a migration.
2. **P0 — Noncooperating writer/TOCTOU gap.** `flock` serializes cooperating
   sandbox operations, but cannot prevent another process from replacing the
   current pathname after its final identity check and before `os.replace`.
   Do not expose a writer for the installed configuration until the lifecycle,
   process exclusion, directory ownership and replace boundary are proven
   safe under the chosen maintenance protocol.
3. **P1 — Durable state and failure injection.** The sandbox journals the
   intended candidate but has no durable completed/rolled-back marker;
   a completed rollback remains classified as `not_replaced`. The backup
   and intent can be left in partial states on an interrupted write, and the
   present tests do not inject `ENOSPC`/`EIO`/`fsync` failures at every step.
   Define explicit transaction states and recovery behavior, including when
   manual intervention is required, before production adoption.
4. **P1 — Filesystem and permission model.** The sandbox's target and
   replacement use private `0600` files. Production needs checks of parent
   directory and path components, trusted owner/group, original ownership and
   mode preservation, symlinks and hardlinks, safe recovery after directory
   reopening, and retention/cleanup policy for backup and journal. The
   independent inspector currently validates artifact mode but does not
   authenticate provenance against an actor who can write the directory.
5. **P1 — Transaction inspection consistency.** Read-only recovery inspection
   does not itself hold the writer lock. It must be coordinated with
   transactional transitions or produce a documented retry/indeterminate
   result when files change between reads. The sandbox rollback re-arms state
   using its own in-memory fields; production cannot trust that shortcut.

**Priority and next gate:** Design and test a restricted maintenance
lifecycle first, with service truly inactive, a verified single config writer,
secure stable directory, fail-closed operator approval, and no Sonos mutations.
Then add fault-injection and post-restart recovery tests. Only after an
independent review and separate permission may a production-specific
apply/rollback interface be considered. Keep the private tracker outside Git.

## P0 maintenance protocol — design, NOT a runnable cutover (2026-11-06)

**Scope:** A separately approved maintenance window for the exact effective
`--config` target of `sonoscdplayer-player.service`. This section is a
*design and review gate only*. It authorizes **no** stop/start, config write,
Sonos action, optical-drive command, socket cleanup or systemd unit edit.

### Why a normal STOPPED status is insufficient

The current service returns a cached status snapshot, not an atomic
maintenance lease. The status omits direct reader activity and full joined
group membership, and commands may be queued or accepted after inspection.
`Controller.shutdown_owned('stop_owned')` may issue an actual Sonos STOP
when it sees verified owned playback; `leave_playing` may intentionally
leave playback active. Starting the player also performs disc/USB inspection
and may start the drive-power manager. Therefore **do not simply run
`systemctl stop` or `restart` as part of an OPS-01 preflight**.

### Proposed operator-controlled phases

| Phase | Evidence required | Allowed action | Fail-closed response |
| --- | --- | --- | --- |
| P0-A Inspect | Effective systemd `ExecStart`, units and drop-ins, configured shutdown policy; independent observation of player and media state | Read-only inspection, no state changes | No maintenance permission |
| P0-B Agree maintenance | Explicit operator approval of scope, planned target, expected original and candidate hashes, service-impact and Sonos-shutdown consequences | Record approved scope, **no automatic service action** | Return to P0-A |
| P0-C Quiesce | Independently confirmed no active owned playback, reader, stream, queued command or unresolved Sonos group cleanup; assess buttons/OLED and service restart triggers | Only a *separately authorized* method to prevent new activity | Abort on unknown/active state; do not force-stop Sonos |
| P0-D Exclusive maintenance | Player fully inactive; no player child/reader/control listener; all relevant service activation mechanisms disabled for the window; all cooperating config writers locked; a trusted administrator assures noncooperating writers are excluded | Inspect evidence and hold an exclusive maintenance lease until completion | Abort on missing/contradictory evidence |
| P0-E Filesystem preflight | Trusted config path discovered from effective systemd args, safe parent and components, owner/mode/links, exact original hash, exclusive writer and transaction directory, validated candidate | **Only in a separately reviewed future implementation**: backup, staged write, atomic replace | Preserve original/backup; no unverified replacement |
| P0-F Verify/rollback | Completed journal and checksum match, backup integrity, no drift; no service restart during file verification | Read-only verification or an individually approved rollback | Preserve evidence, require manual review |
| P0-G Release | Signed-off result, service and audio-impact review, explicit authorization for activation, separate service-start safety review | Restore normal operation **only when expressly permitted** | Leave system in the approved maintenance state |

### Noncooperating writer threat model

A private advisory `flock` does **not** exclude another administrative
process (editor, deployment, installer, cron job or remote management).
A second copy of `player.toml` written between `fstat` and `os.replace`
would otherwise be lost. In the production design, an exclusive maintenance
lease must cover the entire inspection, backup, swap and verification, and
all writers must either honor it or be verifiably unavailable for the window.
The on-disk parent directory must be owned and controlled by a trusted
principal. **When any writer or activation path cannot be excluded, stop
before E; do not rely on a last-moment checksum alone.**

Because no OS primitive atomically compares a pathname's contents and then
replaces that name for arbitrary noncooperating writers, this is primarily
a *trusted environment and lifecycle* requirement, not solved by adding
more checksum checks. If trustworthy exclusion is impossible, production
apply stays blocked.

### System-specific hazards to verify before automation

- Current effective player config is
  `/home/pi/sonoscdplayer/player.toml`; verify it again from
  `systemctl show`, not from a repository example.
- Inventory the actual `Restart=`, `PartOf=`, `BindsTo=`,
  `Requires=`, `Wants=`, timers, sockets, triggers and all
  relevant units; a stopped player may be restarted by another service.
  Record and restore any existing unit state only through a later
  authorized procedure.
- Check the button service and any UI integration for control attempts
  during the window. Do not infer inactivity merely from an absent
  `/tmp/sonoscdplayer-control.sock`; the service intentionally refuses
  to delete pre-existing sockets.
- Existing systemd drop-ins `m016b-config.conf` and
  `m019-venv.conf` must remain unchanged.
- Preserve shutdown-hook artifacts and local untracked files. No
  `git clean`, `git reset --hard`, forced socket unlink or service
  activation as a shortcut.

### Read-only evidence collection (operator may run separately)

These commands only report systemd metadata and process information. **Do
not publish raw unit environment values or full command lines**, which can
contain private IDs. Review locally and share only the requested sanitized
fields. This is an *inventory*, not a migration approval:

```sh
systemctl show sonoscdplayer-player.service \
  -p LoadState -p ActiveState -p SubState -p UnitFileState \
  -p Restart -p MainPID -p ControlPID \
  -p TriggeredBy -p Triggers -p PartOf -p BindsTo -p Requires -p Wants \
  --no-pager
systemctl show sonoscdplayer-buttons.service \
  -p LoadState -p ActiveState -p SubState -p UnitFileState \
  -p Restart -p MainPID -p ControlPID \
  -p TriggeredBy -p Triggers -p PartOf -p BindsTo -p Requires -p Wants \
  --no-pager
```

Inspect service names and dependencies before adding any additional units
to the scope. The exact active `ExecStart`, unit environment and config
path must be checked *locally* but never pasted unredacted.

### Acceptance gates before a production writer may exist

1. An operator-reviewed topology of real systemd units, activation paths,
   controller/socket/reader lifetime and shutdown policy.
2. A separately approved, demonstrably side-effect-safe quiescence method.
   Active or uncertain Sonos ownership, reader or group recovery means **no
   stop, no migration** unless a separately approved recovery resolves it.
3. Verified exclusion of *all* other writers and automatic restarts for the
   full transaction; negative tests for concurrent cooperative and
   noncooperating writers.
4. Authenticated, durable transaction-state machine with explicit partial
   failure outcomes (including `fsync`, `EIO` and `ENOSPC`), mode/owner
   preservation and independently reviewed recovery/rollback.
5. Reboot/reopen and end-to-end rehearsal on **synthetic files only**.
6. An independent code review and a distinct live maintenance approval
   identifying the exact target, scope and potential Sonos/service effects.

**Status:** design initiated; no production implementation or cutover
authorization. As of `539be09`, the Pi regression was 527/527 green.

### Raspberry Pi systemd inventory — first read-only evidence (2026-11-06)

The operator inspected the actual Pi. These are **observed service properties**,
not a verified dependency/activation graph and not maintenance permission:

| Property | Player | Buttons |
| --- | --- | --- |
| `LoadState` | `loaded` | `loaded` |
| `ActiveState/SubState` | `active/running` | `active/running` |
| `MainPID` at inspection | `666` | `660` |
| `Restart` | `no` | `on-failure` |
| `UnitFileState` | `disabled` | `static` |
| `PartOf` | `sonoscdplayer.target` | `sonoscdplayer.target` |
| `Triggers/TriggeredBy/BindsTo/Wants` | empty | empty |

Both reported `Requires` on base system units
(`system.slice`, `sysinit.target`, `-.mount`). The installation also
contains these unit names: `sonoscdplayer.target`,
`sonoscdplayer-early-power-led.service`,
`sonoscdplayer-exitrd.service`, `sonoscdplayer-led.service`,
`sonoscdplayer-oled-shutdown-sentinel.service`,
`sonoscdplayer-oled.service`,
`sonoscdplayer-player.service`, and
`sonoscdplayer-buttons.service`.

The target itself is reported `enabled`; the set of units it pulls in,
whether it can restart them through dependency/activation, and the
OLED/LED/shutdown interactions have **not yet been inspected**. No inference
about safe stopping follows from `Restart=no` or `UnitFileState=disabled`.
Buttons are `Restart=on-failure` and must be included in activity-exclusion
review. **Do not stop the target, player or button service** as an
inventory step. The current player shutdown path may stop verified owned
Sonos playback; any future state-changing maintenance step requires
independent consent and a safe idle/ownership proof.

Next: collect sanitized `systemctl show` unit dependencies and state for
the target and adjacent UI/shutdown units, and `systemctl list-dependencies`
names only. No `systemctl stop/start/restart`, no unit edits, and no
configuration writes. The Pi's development checkout at the time was still
`539be09`, while this later documentation was committed only to GitHub.

### Raspberry Pi systemd inventory — target dependency graph (2026-11-06)

Second operator read-only inspection confirmed:

- `sonoscdplayer.target`: `active/active`, `enabled`;
  `Wants=` player, buttons, OLED, LED and OLED shutdown sentinel.
- `ConsistsOf=` player, buttons, OLED and LED. Each of these four units
  independently reported `PartOf=sonoscdplayer.target`.
  **Stopping the target would propagate a stop request to these four units.**
  It is therefore *not* a safe unapproved maintenance preparation.
- Player `Restart=no`; buttons, OLED and LED `Restart=on-failure`.
  All four are currently `active/running`.
- OLED shutdown sentinel `active/exited`, `Restart=no`,
  `PartOf=` empty; it is wanted by the target but not in its
  `ConsistsOf` list. Do **not** assume a target stop also stops the
  sentinel, or assume it has no shutdown effects.
- Early-power LED `inactive/dead` and exitrd `inactive/dead`;
  neither is in the target dependency list currently shown.

**Safety implication:** A target stop is not a no-op or a reliable
single-service quiescence primitive. It can stop multiple running
components, and player service exit may invoke a Sonos STOP depending on
validated ownership and shutdown policy. The service states do not prove
whether the CD reader or groups are inactive. Nothing has been stopped,
restarted or rewritten as part of these observations.

**Next read-only gate:** inspect effective unit definitions *without
disclosing private `ExecStart` arguments, environment or identifiers*.
Collect only unit metadata for `ExecStop` presence, stop propagation and
activation conditions, plus a sanitized unit-file review covering
`[Unit]`, `[Service]` lifecycle directives and `[Install]` without
command-line values. Include OLED shutdown sentinel before proposing any
target or service stop. Stop and restart operations still require separate
explicit consent.

### Raspberry Pi lifecycle metadata — third read-only inspection (2026-11-06)

The operator inspected effective unit lifecycle properties without exposing
raw `ExecStart` or `ExecStop` command arguments:

| Unit | Type | Restart | Stop timeout | Explicit ExecStop | Other relevant behavior |
| --- | --- | --- | --- | --- | --- |
| `sonoscdplayer-player.service` | simple | no | 15s | absent | SIGTERM (15), control-group kill, SIGKILL fallback |
| `sonoscdplayer-buttons.service` | simple | on-failure | 5s | absent | SIGTERM (15), control-group kill, SIGKILL fallback |
| `sonoscdplayer-oled.service` | simple | on-failure | 5s | absent | SIGTERM (15), control-group kill, SIGKILL fallback |
| `sonoscdplayer-led.service` | simple | on-failure | 5s | absent | SIGTERM (15), control-group kill, SIGKILL fallback |
| `sonoscdplayer-oled-shutdown-sentinel.service` | oneshot | no | 5s | **present** | RemainAfterExit=yes, SIGKILL fallback |
| `sonoscdplayer-early-power-led.service` | simple | no | 3s | absent | conflicts with normal LED |
| `sonoscdplayer-exitrd.service` | oneshot | no | 1min 30s | absent | shutdown-related unit |
| `sonoscdplayer.target` | target | n/a | n/a | absent | wanted by multi-user.target; conflicts with shutdown.target |

The player unit has no systemd `ExecStop`, **but the Python program's
`finally` block calls `shutdown_owned(shutdown_policy)`**. That logic may
send Sonos STOP for verified owned playback; stopping the service is not
side-effect-free. A 15-second stop timeout with forced kill is particularly
unsafe if the CD reader or group recovery is still active.

**New P0 concern:** the OLED shutdown sentinel is active/exited and has an
`ExecStop` of unknown content. It has no `PartOf=sonoscdplayer.target`
and is not in the target's `ConsistsOf`, but future explicit stops,
shutdown transactions, or dependency changes could run its stop action.
Do not assume the command is harmless or implicitly invoke it.

No `ExecStartPre`, `ExecStopPost`, `ExecReload` or `ExecCondition`
was reported for any of the inspected units. None has a reported `OnFailure`
or `Upholds` relationship. `RefuseManualStop=no` does not mean that a
manual stop is safe.

**Next gate:** inspect the *meaning* of the sentinel's `ExecStop` locally
without publishing any sensitive command-line values or invoking it. Review
the unit's `ExecStop` and shutdown-hook dependencies, then report only
whether it (a) writes a display/framebuffer, (b) invokes a player/Sonos
command, (c) modifies GPIO or drive power, or (d) triggers a system shutdown.
A safe maintenance sequence remains undesigned and unauthorized.

### Sentinel ExecStop executable identified (2026-11-06)

A further operator read-only `systemctl show ... -p ExecStop` check
confirmed that the OLED shutdown sentinel has exactly **one ExecStop
command**, and its executable basename is `touch`. The command was **not
executed** during inspection.

This narrows the likely direct effect to creating a file or updating its
timestamp, but the target pathname and any observers are still unknown.
A watcher might interpret the touched file as a shutdown signal and trigger
other actions. The absence of an immediate player/Sonos executable does
not prove stopping the sentinel is harmless.

Next inspect only the **classification** of the touch operand and the
existence of watcher references. Do not run `touch`, invoke `ExecStop`,
stop the sentinel, or copy raw command lines containing local identifiers
into logs/chats. OPS-01 production writes remain blocked.
